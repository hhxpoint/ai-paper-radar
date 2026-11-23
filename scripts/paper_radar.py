#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-radar: AI 论文雷达每日流水线。

职责：
  prepare   抓取候选 → 评分排序 → 生成草稿（digest + 每篇 note 骨架）
  finalize  校验摘要完整性 → 写入 data/ → 更新索引/README → 可选 git commit/push
  status    查看近期状态（给每日智能体/人工巡检用）
  check     仓库完整性检查（CI 与本地都跑）
  fulltext  抓取并抽取 arXiv HTML 正文（给精读用，带本地缓存）
  download  下载当日论文 PDF 到本地 library/（不入库）
  weekly    生成《本周观点雷达》周报骨架

设计约束：
  - 只依赖标准库 + PyYAML（读配置）；
  - 网络操作全部 fail-soft：单源失败不阻断整体，失败信息写入 state；
  - 幂等：同日重复 prepare 返回已有草稿；finalize 后可重复运行；
  - 可审计：data/daily/<date>.json 记录候选、评分信号、抓取元信息与摘要模式。
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DAILY_DIR = DATA_DIR / "daily"
PAPERS_DIR = ROOT / "papers"
DIGESTS_DIR = ROOT / "digests"
VENUES_DIR = ROOT / "venues"

CN_TZ = dt.timezone(dt.timedelta(hours=8))
ATOM = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

MODE_LABELS = {
    "auto": "自动完成",
    "llm": "自动摘要（LLM 速读）",
    "fallback": "自动速览（待精读）",
    "manual": "每日精读（智能体）",
}

# note 各小节：占位符 → (标题, 最少字符数)
NOTE_SECTIONS = [
    ("TLDR", "一句话结论", 15),
    ("WHY_READ", "为什么值得读（观点）", 40),
    ("PROBLEM", "问题与动机", 30),
    ("INSIGHT", "方法与关键洞察", 60),
    ("EVIDENCE", "证据与实验", 30),
    ("CRITIQUE", "可质疑之处", 40),
    ("RELEVANCE", "与我的研究关联", 20),
    ("FURTHER", "延伸阅读", 2),
]

AI_CATEGORIES = {
    "cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.MA", "cs.NE", "cs.SD",
    "eess.AS", "cs.IR", "cs.HC", "cs.CY", "cs.GT", "cs.LO", "cs.SE", "stat.ML",
    "eess.IV", "eess.SP", "q-bio.NC", "cs.DC", "cs.NI",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def log(msg: str = "") -> None:
    stamp = dt.datetime.now(CN_TZ).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"ERROR: {msg}")
    raise SystemExit(code)


def now_cn() -> dt.datetime:
    return dt.datetime.now(CN_TZ)


def today_cn() -> dt.date:
    return now_cn().date()


def iso_now() -> str:
    return now_cn().isoformat(timespec="seconds")


def parse_date(value: str) -> dt.date:
    value = (value or "").strip()
    if not value:
        return today_cn()
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        die(f"--date 需要 YYYY-MM-DD 格式，收到：{value!r}")


def clean_text(value) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def norm_id(value: str) -> str:
    value = (value or "").strip()
    if "/abs/" in value:
        value = value.rsplit("/abs/", 1)[-1]
    if "/pdf/" in value:
        value = value.rsplit("/pdf/", 1)[-1]
    value = re.sub(r"\.pdf$", "", value)
    return re.sub(r"v\d+$", "", value)


def norm_title(title: str) -> str:
    t = clean_text(title).lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def slugify(title: str, max_len: int = 48) -> str:
    t = clean_text(title).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    words = [w for w in t.split("-") if w]
    out = ""
    for w in words:
        candidate = f"{out}-{w}" if out else w
        if len(candidate) > max_len:
            break
        out = candidate
    return out or "paper"


def read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        log(f"WARN: 读取 {path} 失败：{exc}")
    return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path: Path, obj, indent: int = 1) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")


def load_yaml(path: Path):
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        die("缺少 PyYAML，请先运行：pip install pyyaml")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:  # noqa: BLE001
        die(f"解析 {path.name} 失败：{exc}")


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, DAILY_DIR, PAPERS_DIR, DIGESTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_config():
    profile = load_yaml(CONFIG_DIR / "profile.yml")
    sources = load_yaml(CONFIG_DIR / "sources.yml")
    if not profile.get("profile") or not profile.get("topics"):
        die("config/profile.yml 缺少 profile/topics 配置")
    return profile, sources


def seen_path() -> Path:
    return DATA_DIR / "seen.json"


def papers_jsonl() -> Path:
    return DATA_DIR / "papers.jsonl"


def state_path(date: dt.date) -> Path:
    return DAILY_DIR / f"{date.isoformat()}.json"


def digest_path(date: dt.date) -> Path:
    return DIGESTS_DIR / f"{date.isoformat()}.md"


def note_path(date: dt.date, paper: dict) -> Path:
    return PAPERS_DIR / f"{date:%Y-%m}" / f"{paper['id']}-{slugify(paper['title'])}.md"


def rel_link(from_file: Path, to_file: Path) -> str:
    return os.path.relpath(to_file, from_file.parent).replace("\\", "/")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_get(url: str, *, timeout: int = 60, retries: int = 3, backoff: int = 4,
             binary: bool = False, headers: dict | None = None):
    cfg_sources = None
    ua = "paper-radar/1.0 (https://github.com/hhxpoint/ai-paper-radar)"
    if headers is None:
        headers = {}
    headers = {"User-Agent": ua, "Accept": "*/*", **headers}
    last = ""
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return raw if binary else raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (400, 401, 404):  # 永久性错误不重试
                raise RuntimeError(f"GET {url} -> {last}") from exc
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        if attempt < retries - 1:
            wait = min(backoff * (2 ** attempt), 60)
            log(f"  retry {attempt + 1}/{retries - 1} in {wait}s ({last})")
            time.sleep(wait)
    raise RuntimeError(f"GET {url} -> {last}")


def http_post_json(url: str, payload: dict, *, headers: dict | None = None,
                   timeout: int = 120, retries: int = 2):
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "paper-radar/1.0"}
    if headers:
        hdrs.update(headers)
    last = ""
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            last = f"HTTP {exc.code} {detail}"
            if exc.code in (400, 401, 403, 404):
                raise RuntimeError(f"POST {url} -> {last}") from exc
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        if attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"POST {url} -> {last}")


# ---------------------------------------------------------------------------
# arXiv / HuggingFace 抓取
# ---------------------------------------------------------------------------

def _entry_text(entry, tag: str, default: str = "") -> str:
    found = entry.findtext(f"a:{tag}", default, ATOM)
    return clean_text(found or default)


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("a:entry", ATOM):
        raw_id = entry.findtext("a:id", "", ATOM)
        rec = {
            "id": norm_id(raw_id),
            "title": _entry_text(entry, "title"),
            "abstract": _entry_text(entry, "summary"),
            "published": (entry.findtext("a:published", "", ATOM) or "")[:10],
            "updated": (entry.findtext("a:updated", "", ATOM) or "")[:10],
            "authors": [clean_text(a.findtext("a:name", "", ATOM)) for a in entry.findall("a:author", ATOM)],
            "comment": clean_text(entry.findtext(f"{ARXIV_NS}comment", "")),
            "journal_ref": clean_text(entry.findtext(f"{ARXIV_NS}journal_ref", "")),
            "categories": [c.get("term", "") for c in entry.findall("a:category", ATOM)],
            "primary_category": "",
        }
        prim = entry.find(f"{ARXIV_NS}primary_category")
        if prim is not None:
            rec["primary_category"] = prim.get("term", "")
        out.append(rec)
    return out


def arxiv_search(api: str, query: str, *, page_size: int, max_pages: int,
                 delay: float, timeout: int, retries: int) -> tuple[list[dict], dict]:
    entries: list[dict] = []
    requests = 0
    errors: list[str] = []
    for page in range(max_pages):
        params = {
            "search_query": query,
            "start": page * page_size,
            "max_results": page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = api + "?" + urllib.parse.urlencode(params)
        try:
            text = http_get(url, timeout=timeout, retries=retries)
            requests += 1
            page_entries = parse_arxiv_feed(text)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:300])
            break
        entries.extend(page_entries)
        if len(page_entries) < page_size:
            break
        if page < max_pages - 1:
            time.sleep(delay)
    return entries, {"requests": requests, "entries": len(entries), "errors": errors}


def _norm_candidate(rec: dict, source: str) -> dict:
    cid = norm_id(rec.get("id", ""))
    if not cid:
        return {}
    title = clean_text(rec.get("title"))
    abstract = clean_text(rec.get("abstract"))
    cand = {
        "id": cid,
        "title": title,
        "abstract": abstract,
        "authors": [clean_text(a) for a in (rec.get("authors") or []) if clean_text(a)],
        "published": (rec.get("published") or "")[:10],
        "updated": (rec.get("updated") or "")[:10],
        "comment": clean_text(rec.get("comment")),
        "journal_ref": clean_text(rec.get("journal_ref")),
        "categories": rec.get("categories") or [],
        "primary_category": rec.get("primary_category") or "",
        "abs_url": f"https://arxiv.org/abs/{cid}",
        "pdf_url": f"https://arxiv.org/pdf/{cid}",
        "html_url": f"https://arxiv.org/html/{cid}",
        "source": [source],
        "hf_upvotes": 0,
        "hf_github": "",
        "hf_url": "",
    }
    return cand


def is_ai_relevant(profile: dict, cand: dict) -> bool:
    if "hf" in cand.get("source", []) and not cand.get("categories"):
        return True  # HF Daily 已经是 AI 论文人工/社区筛选结果
    if any(cat in AI_CATEGORIES for cat in cand.get("categories", [])):
        return True
    text = f"{cand.get('title', '')} {cand.get('abstract', '')}".lower()
    for spec in (profile.get("topics") or {}).values():
        for kw in spec.get("keywords") or []:
            if len(kw) <= 4:
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    return True
            elif kw in text:
                return True
    return False


def fetch_candidates(profile: dict, sources: dict, date: dt.date,
                     verbose: bool = True) -> tuple[list[dict], dict]:
    ensure_dirs()
    arxiv_cfg = sources.get("arxiv") or {}
    hf_cfg = sources.get("hf_daily") or {}
    p = profile["profile"]
    lookback = int(p.get("lookback_days", 4))
    window_start = date - dt.timedelta(days=lookback)

    meta = {
        "window": {"start": window_start.isoformat(), "end": date.isoformat()},
        "arxiv": {"requests": 0, "entries": 0, "errors": []},
        "hf": {"entries": 0, "errors": []},
    }

    by_id: dict[str, dict] = {}

    def merge(cand: dict) -> None:
        if not cand:
            return
        cid = cand["id"]
        if cid in by_id:
            existing = by_id[cid]
            for src in cand["source"]:
                if src not in existing["source"]:
                    existing["source"].append(src)
            for key in ("hf_upvotes", "hf_github", "hf_url"):
                if cand.get(key) and not existing.get(key):
                    existing[key] = cand[key]
            for key in ("comment", "journal_ref", "primary_category", "abstract"):
                if not existing.get(key) and cand.get(key):
                    existing[key] = cand[key]
            if not existing.get("authors") and cand.get("authors"):
                existing["authors"] = cand["authors"]
            return
        by_id[cid] = cand

    if arxiv_cfg.get("enabled", True):
        categories = profile.get("categories") or ["cs.AI"]
        cats_query = "(" + " OR ".join(f"cat:{c}" for c in categories) + ")"
        date_query = f"submittedDate:[{window_start:%Y%m%d}0000 TO {date:%Y%m%d}2359]"
        main_query = f"{cats_query} AND {date_query}"
        if verbose:
            log(f"arXiv 主查询：{len(categories)} 个分类，窗口 {window_start} ~ {date}")
        entries, stat = arxiv_search(
            arxiv_cfg.get("api", "https://export.arxiv.org/api/query"),
            main_query,
            page_size=int(arxiv_cfg.get("page_size", 500)),
            max_pages=int(arxiv_cfg.get("max_pages", 3)),
            delay=float(arxiv_cfg.get("request_delay_seconds", 3.2)),
            timeout=int(arxiv_cfg.get("timeout_seconds", 90)),
            retries=int(arxiv_cfg.get("retries", 4)),
        )
        meta["arxiv"]["requests"] += stat["requests"]
        meta["arxiv"]["entries"] += stat["entries"]
        meta["arxiv"]["errors"].extend(stat["errors"])
        for rec in entries:
            merge(_norm_candidate(rec, "arxiv"))

        # 「有观点」专项检索：跨分类，只取近期
        opinion_cfg = profile.get("opinion") or {}
        phrases = []
        for phrase in (opinion_cfg.get("strong_keywords") or [])[:8]:
            if " " in phrase:
                phrases.append(f'abs:"{phrase}"')
        for phrase in (opinion_cfg.get("strong_keywords") or [])[:6]:
            if " " not in phrase:
                phrases.append(f"ti:{phrase}")
        opinion_window = date - dt.timedelta(days=int(p.get("opinion_lookback_days", 21)))
        if phrases:
            opinion_query = "(" + " OR ".join(phrases) + ")" + \
                f" AND submittedDate:[{opinion_window:%Y%m%d}0000 TO {date:%Y%m%d}2359]"
            if verbose:
                log(f"arXiv 观点专项查询（{len(phrases)} 个短语，窗口 {opinion_window} ~ {date}）")
            entries2, stat2 = arxiv_search(
                arxiv_cfg.get("api", "https://export.arxiv.org/api/query"),
                opinion_query,
                page_size=100,
                max_pages=1,
                delay=float(arxiv_cfg.get("request_delay_seconds", 3.2)),
                timeout=int(arxiv_cfg.get("timeout_seconds", 90)),
                retries=int(arxiv_cfg.get("retries", 4)),
            )
            meta["arxiv"]["requests"] += stat2["requests"]
            meta["arxiv"]["entries"] += stat2["entries"]
            meta["arxiv"]["errors"].extend(stat2["errors"])
            for rec in entries2:
                rec = _norm_candidate(rec, "arxiv")
                merge(rec)

    if hf_cfg.get("enabled", True):
        limit = int(hf_cfg.get("limit", 100))
        for api in hf_cfg.get("apis") or []:
            url = f"{api}?limit={limit}"
            try:
                raw = http_get(url, timeout=int(hf_cfg.get("timeout_seconds", 30)),
                               retries=int(hf_cfg.get("retries", 2)))
                items = json.loads(raw)
                if not isinstance(items, list):
                    raise RuntimeError("返回不是列表")
                count = 0
                for item in items:
                    paper = item.get("paper") or {}
                    cid = norm_id(paper.get("id") or item.get("paperId") or "")
                    if not cid:
                        continue
                    rec = {
                        "id": cid,
                        "title": clean_text(paper.get("title") or item.get("title")),
                        "abstract": clean_text(paper.get("summary") or item.get("summary")),
                        "authors": [clean_text(a.get("name")) for a in (paper.get("authors") or [])
                                    if isinstance(a, dict) and clean_text(a.get("name"))],
                        "published": (paper.get("publishedAt") or item.get("publishedAt") or "")[:10],
                        "categories": [],
                        "primary_category": "",
                        "comment": "",
                        "journal_ref": "",
                    }
                    cand = _norm_candidate(rec, "hf")
                    if not cand:
                        continue
                    cand["hf_upvotes"] = int(paper.get("upvotes") or item.get("upvotes") or 0)
                    cand["hf_github"] = clean_text(paper.get("githubRepo") or "")
                    cand["hf_url"] = f"https://huggingface.co/papers/{cid}"
                    merge(cand)
                    count += 1
                meta["hf"]["entries"] += count
                if verbose:
                    log(f"HuggingFace Daily：{count} 条（{api}）")
                break
            except Exception as exc:  # noqa: BLE001
                meta["hf"]["errors"].append(f"{api}: {exc}"[:300])
                if verbose:
                    log(f"WARN: HF Daily 源失败（{api}）：{exc}")

    cands = list(by_id.values())
    for cand in cands:
        cand["norm_title"] = norm_title(cand["title"])
    return cands, meta


# ---------------------------------------------------------------------------
# 评分与选文
# ---------------------------------------------------------------------------

def _keyword_hit(kw: str, text: str) -> bool:
    kw = kw.lower()
    if len(kw) <= 4 and re.fullmatch(r"[a-z0-9]+", kw):
        return re.search(rf"\b{re.escape(kw)}\b", text) is not None
    return kw in text


def detect_venue(profile: dict, cand: dict):
    text = f"{cand.get('comment', '')} {cand.get('journal_ref', '')}".lower()
    if not text.strip():
        return "", []
    for spec in profile.get("venue_signals") or []:
        if re.search(spec["pattern"], text, re.I):
            year = re.search(r"\b(20\d\d)\b", text)
            venue = f"{spec['name']} {year.group(1)}" if year else spec["name"]
            return venue, [spec["name"]]
    return "", []


def assign_topic(profile: dict, cand: dict):
    title = cand["title"].lower()
    abstract = cand["abstract"].lower()
    best_name, best_hits, best_value = "其他", 0, 0.0
    matched = []
    for name, spec in (profile.get("topics") or {}).items():
        hits = 0
        own = 0
        for kw in spec.get("keywords") or []:
            hit = _keyword_hit(kw, title) or _keyword_hit(kw, abstract)
            if hit:
                own += 1
                hits += 2 if _keyword_hit(kw, title) else 1
        if own:
            matched.append(name)
        value = hits * float(spec.get("weight", 1.0))
        if value > best_value:
            best_name, best_hits, best_value = name, hits, value
    return best_name, best_hits, best_value, matched


def opinion_score(profile: dict, cand: dict) -> tuple[float, list[str]]:
    spec = profile.get("opinion") or {}
    title = cand["title"].lower()
    text = f"{title} {cand['abstract'].lower()} {cand.get('comment', '').lower()}"
    total = 0.0
    found: list[str] = []
    for level, cap_key in (("strong", "strong_cap"), ("medium", "medium_cap"), ("weak", "weak_cap")):
        weight = float(spec.get(f"{level}_weight", 0))
        cap = float(spec.get(cap_key, weight * 2))
        level_sum = 0.0
        for phrase in spec.get(f"{level}_keywords") or []:
            p = phrase.lower()
            if p in title:
                level_sum += weight * 1.3
                found.append(phrase)
            elif p in text:
                level_sum += weight
                found.append(phrase)
        for pattern in spec.get(f"{level}_regex") or []:
            if re.search(pattern, text, re.I):
                level_sum += weight
                found.append(f"re:{pattern}" if level == "strong" else "")
        total += min(level_sum, cap)
    found = [f for f in found if f]
    return round(total, 3), found[:6]


def score_candidate(profile: dict, cand: dict, date: dt.date) -> dict | None:
    sc = profile.get("scoring") or {}
    signals: dict[str, float] = {}

    # 负信号直接剔除
    text_all = f"{cand['title']} {cand['abstract']}".lower()
    for neg in profile.get("negative_keywords") or []:
        if neg.lower() in text_all:
            return None

    if len(cand["abstract"]) < int(profile["profile"].get("min_abstract_chars", 200)):
        return None

    venue, venue_tags = detect_venue(profile, cand)
    cand["venue"] = venue
    cand["venue_tags"] = venue_tags
    signals["venue"] = float(sc.get("venue_bonus", 4.0)) if venue else 0.0

    topic, hits, value, matched = assign_topic(profile, cand)
    cand["topic"] = topic
    cand["topic_matches"] = matched
    signals["topic"] = round(min(float(sc.get("topic_cap", 2.2)),
                                 hits * float(sc.get("topic_scale", 0.25))), 3)

    op, op_found = opinion_score(profile, cand)
    signals["opinion"] = round(op, 3)
    cand["opinion_hits"] = op_found

    if cand.get("hf_upvotes"):
        signals["hf"] = round(min(float(sc.get("hf_upvote_cap", 2.5)),
                                  math.log1p(cand["hf_upvotes"]) * float(sc.get("hf_upvote_log_scale", 0.9))), 3)
    else:
        signals["hf"] = 0.0

    freshness = 0.0
    try:
        pub = dt.date.fromisoformat(cand["published"])
        days = (date - pub).days
        if 0 <= days <= 2:
            freshness = float(sc.get("freshness_days_2", 1.0))
        elif days <= 7:
            freshness = float(sc.get("freshness_days_7", 0.5))
        elif days <= 14:
            freshness = float(sc.get("freshness_days_14", 0.2))
    except ValueError:
        pass
    signals["freshness"] = freshness

    code = ""
    text_links = f"{cand['abstract']} {cand.get('comment', '')}"
    for url in re.findall(r"https?://[^\s()<>\[\]{}\"'，。、；]+", text_links):
        url = re.sub(r"[).,;:]+$", "", url)
        if "github.com" in url:
            code = url
            break
    if not code and cand.get("hf_github"):
        code = clean_text(cand["hf_github"])
        code = re.sub(r"[).,;:]+$", "", code)
    cand["code_url"] = code
    signals["code"] = float(sc.get("code_bonus", 0.4)) if code else 0.0

    signals["abstract"] = float(sc.get("abstract_bonus", 0.3)) if 700 <= len(cand["abstract"]) <= 2400 else 0.0

    cand["signals"] = signals
    cand["score"] = round(sum(signals.values()), 3)
    return cand


def build_pool(profile: dict, cands: list[dict], seen: dict, date: dt.date):
    counts = {"candidates": len(cands), "excluded_seen": 0,
              "excluded_negative": 0, "excluded_short": 0}
    seen_ids = (seen or {}).get("ids") or {}
    seen_titles = (seen or {}).get("titles") or {}
    pool: list[dict] = []
    for cand in cands:
        cid = cand["id"]
        nt = cand.get("norm_title", norm_title(cand["title"]))
        if cid in seen_ids or nt in seen_titles:
            counts["excluded_seen"] += 1
            continue
        scored = score_candidate(profile, cand, date)
        if scored is None:
            if len(cand["abstract"]) < int(profile["profile"].get("min_abstract_chars", 200)):
                counts["excluded_short"] += 1
            else:
                counts["excluded_negative"] += 1
            continue
        if not is_ai_relevant(profile, scored):
            counts["excluded_negative"] += 1
            continue
        pool.append(scored)
    counts["pool"] = len(pool)
    return pool, counts


def select_candidates(profile: dict, pool: list[dict], count: int):
    p = profile["profile"]
    max_per = int(p.get("max_per_topic", 2))
    max_backups = int(p.get("max_digest_backups", 8))
    ranked = sorted(pool, key=lambda c: (-c["score"], c["id"]))
    selected: list[dict] = []
    topic_counts: dict[str, int] = {}

    def is_dup(cand: dict) -> bool:
        for s in selected:
            if difflib.SequenceMatcher(None, cand["norm_title"], s["norm_title"]).ratio() > 0.92:
                return True
        return False

    for cand in ranked:
        if len(selected) >= count:
            break
        if topic_counts.get(cand["topic"], 0) >= max_per:
            continue
        if is_dup(cand):
            continue
        selected.append(cand)
        topic_counts[cand["topic"]] = topic_counts.get(cand["topic"], 0) + 1

    required = p.get("required_topics") or {}
    for topic, min_n in required.items():
        have = sum(1 for s in selected if s["topic"] == topic)
        if have >= int(min_n):
            continue
        best = next((c for c in ranked if c["topic"] == topic
                     and all(c["id"] != s["id"] for s in selected) and not is_dup(c)), None)
        if not best:
            continue
        if len(selected) < count:
            selected.append(best)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        else:
            required_names = set(required)
            victims = [(i, s) for i, s in enumerate(selected) if s["topic"] not in required_names]
            if not victims:
                continue
            victim_i, victim = min(victims, key=lambda item: item[1]["score"])
            topic_counts[victim["topic"]] = max(0, topic_counts.get(victim["topic"], 1) - 1)
            selected[victim_i] = best
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    selected.sort(key=lambda c: -c["score"])
    sel_ids = {c["id"] for c in selected}
    backups: list[dict] = []
    for cand in ranked:
        if len(backups) >= max_backups:
            break
        if cand["id"] in sel_ids:
            continue
        if any(difflib.SequenceMatcher(None, cand["norm_title"], b["norm_title"]).ratio() > 0.92
               for b in backups):
            continue
        backups.append(cand)
    return selected, backups


# ---------------------------------------------------------------------------
# 草稿生成
# ---------------------------------------------------------------------------

def _md_cell(text: str) -> str:
    return clean_text(text).replace("|", "\\|")


def _authors_line(authors: list[str]) -> str:
    if not authors:
        return "未知"
    shown = authors[:8]
    tail = f" 等 {len(authors)} 人" if len(authors) > 8 else ""
    return "、".join(shown) + tail


def _meta_line(paper: dict) -> str:
    parts = [f"**arXiv**：[{paper['id']}]({paper['abs_url']})",
             f"**主题**：`{paper['topic']}`",
             f"**发表**：{paper['published'] or '未知'}"]
    if paper.get("venue"):
        parts.append(f"**Venue**：{paper['venue']}")
    return "> " + " · ".join(parts) + "  \n"


def render_note(date: dt.date, paper: dict, digest: Path) -> str:
    links = [f"[PDF]({paper['pdf_url']})", f"[HTML]({paper['html_url']})"]
    if paper.get("code_url"):
        links.append(f"[代码]({paper['code_url']})")
    if paper.get("hf_url"):
        links.append(f"[HF 讨论]({paper['hf_url']})")
    rel = rel_link(note_path(date, paper), digest)
    lines = [
        f"# {paper['title']}",
        "",
        _meta_line(paper).rstrip("  \n"),
        f"> **作者**：{_authors_line(paper.get('authors') or [])} · " + " · ".join(links) + "  ",
        "> 状态：草稿（待精读补全）",
        "",
        "## 一句话结论",
        "{{TLDR}}",
        "",
        "## 为什么值得读（观点）",
        "{{WHY_READ}}",
        "",
        "## 问题与动机",
        "{{PROBLEM}}",
        "",
        "## 方法与关键洞察",
        "{{INSIGHT}}",
        "",
        "## 证据与实验",
        "{{EVIDENCE}}",
        "",
        "## 可质疑之处",
        "{{CRITIQUE}}",
        "",
        "## 与我的研究关联",
        "{{RELEVANCE}}",
        "",
        "## 延伸阅读",
        "{{FURTHER}}",
        "",
        "## 摘要原文",
        "",
        "<details>",
        "<summary>展开英文摘要</summary>",
        "",
        paper["abstract"] or "（摘要缺失）",
        "",
        "</details>",
        "",
        "## 我的笔记",
        "",
        "<!-- 你的批注写在这里；自动流水线只会更新上面的总结区，不会覆盖本节。 -->",
        "",
        "---",
        "",
        f"> 返回到当日雷达：[{date.isoformat()} 论文雷达]({rel})",
        "",
    ]
    return "\n".join(lines)


def render_digest(date: dt.date, count: int, selected: list[dict], backups: list[dict],
                  state: dict) -> str:
    fetch = state.get("fetch") or {}
    counts = state.get("counts") or {}
    window = fetch.get("window") or {}
    topic_dist = state.get("topic_distribution") or {}
    top_topics = " · ".join(f"{k} {v}" for k, v in sorted(topic_dist.items(), key=lambda x: -x[1])[:5])
    venue_hits = [p for p in selected if p.get("venue")]

    lines = [
        f"# {date.isoformat()} · 论文雷达",
        "",
        f"> 扫描窗口：{window.get('start', '?')} ～ {window.get('end', '?')} · "
        f"候选 {counts.get('candidates', 0):,} 篇 · 精选 {count} 篇  ",
        "> 状态：草稿（待精读补全）  ",
        "> 数据源：arXiv（主） · HuggingFace Daily（热度） · 生成：paper-radar",
        "",
        "## 今日看点",
        "",
        "{{OVERVIEW}}",
        "",
        "## 精选论文",
        "",
    ]
    for idx, paper in enumerate(selected, 1):
        note = note_path(date, paper)
        rel = rel_link(digest_path(date), note)
        meta = [f"`{paper['topic']}`", f"{paper['published'] or '未知'}"]
        if paper.get("venue"):
            meta.append(paper["venue"])
        links = [f"[精读笔记]({rel})", f"[arXiv]({paper['abs_url']})", f"[PDF]({paper['pdf_url']})"]
        if paper.get("code_url"):
            links.append(f"[代码]({paper['code_url']})")
        lines.extend([
            f"### {idx}. {paper['title']}",
            "",
            f"- **一句话**：{{{{TLDR_{idx}}}}}",
            f"- **为什么值得读**：{{{{WHY_{idx}}}}}",
            f"- **信息**：" + " · ".join(meta) + " · " + " · ".join(links),
            f"- **作者**：{_authors_line(paper.get('authors') or [])}",
            "",
        ])
    lines.extend([
        "## 备选（未精读，按分数排序）",
        "",
        "| # | 论文 | 主题 | 分数 | 链接 |",
        "|---|------|------|------|------|",
    ])
    for idx, paper in enumerate(backups, 1):
        lines.append(f"| {idx} | {_md_cell(paper['title'])} | {_md_cell(paper.get('topic', ''))} | "
                     f"{paper.get('score', 0):.1f} | [arXiv]({paper.get('abs_url', '')}) |")
    lines.extend([
        "",
        "## 今日数据",
        "",
        f"- arXiv：请求 {fetch.get('arxiv', {}).get('requests', 0)} 次，"
        f"获取 {fetch.get('arxiv', {}).get('entries', 0):,} 条，"
        f"失败 {len(fetch.get('arxiv', {}).get('errors') or [])} 次",
        f"- HuggingFace Daily：{fetch.get('hf', {}).get('entries', 0)} 条",
        f"- 去重后候选：{counts.get('candidates', 0):,} · 排除历史已读：{counts.get('excluded_seen', 0)} · "
        f"入选 {count} 篇",
        f"- 候选主题分布：{top_topics or '—'}",
        f"- 入选论文中顶会/期刊录用：{len(venue_hits)} 篇",
        "",
        "> 说明：本页由自动化流水线生成，摘要与观点可能出错；重要结论请以论文原文为准。",
        "",
    ])
    return "\n".join(lines)


def _paper_state(date: dt.date, paper: dict, note: Path) -> dict:
    return {
        "id": paper["id"],
        "title": paper["title"],
        "topic": paper.get("topic", ""),
        "venue": paper.get("venue", ""),
        "score": paper.get("score", 0),
        "published": paper.get("published", ""),
        "authors": (paper.get("authors") or [])[:12],
        "abs_url": paper["abs_url"],
        "pdf_url": paper["pdf_url"],
        "html_url": paper["html_url"],
        "code_url": paper.get("code_url", ""),
        "hf_url": paper.get("hf_url", ""),
        "comment": paper.get("comment", ""),
        "abstract": (paper.get("abstract") or "")[:4000],
        "signals": paper.get("signals", {}),
        "note_path": note.relative_to(ROOT).as_posix(),
    }


def _backup_state(paper: dict) -> dict:
    return {
        "id": paper["id"],
        "title": paper["title"],
        "topic": paper.get("topic", ""),
        "venue": paper.get("venue", ""),
        "score": paper.get("score", 0),
        "abs_url": paper["abs_url"],
    }


def write_scaffold(state: dict, *, overwrite: bool = False) -> None:
    date = dt.date.fromisoformat(state["date"])
    digest = digest_path(date)
    if overwrite or not digest.exists():
        atomic_write(digest, render_digest(date, len(state["selected"]), state["selected"],
                                           state.get("backups") or [], state))
    for paper in state["selected"]:
        note = ROOT / paper["note_path"]
        if overwrite or not note.exists():
            atomic_write(note, render_note(date, paper, digest))


def build_state(date: dt.date, profile: dict, selected: list[dict], backups: list[dict],
                fetch_meta: dict, counts: dict, pool: list[dict]) -> dict:
    topic_dist: dict[str, int] = {}
    for cand in pool:
        topic_dist[cand["topic"]] = topic_dist.get(cand["topic"], 0) + 1
    digest = digest_path(date)
    return {
        "date": date.isoformat(),
        "status": "draft",
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "window": fetch_meta.get("window"),
        "fetch": fetch_meta,
        "counts": counts,
        "topic_distribution": dict(sorted(topic_dist.items(), key=lambda x: -x[1])),
        "selected": [_paper_state(date, p, note_path(date, p)) for p in selected],
        "backups": [_backup_state(p) for p in backups],
        "digest_path": digest.relative_to(ROOT).as_posix(),
        "summary_mode": "pending",
        "finalized_at": None,
    }


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def cmd_prepare(args) -> int:
    profile, sources = load_config()
    date = parse_date(args.date)
    count = args.count or int(profile["profile"].get("daily_count", 5))
    ensure_dirs()

    prev = read_json(state_path(date))
    if prev and not args.force:
        status = prev.get("status")
        if status in ("final", "empty"):
            payload = {"status": "already-final", "date": date.isoformat(),
                       "digest": prev.get("digest_path")}
            _emit(payload, args.json, f"{date} 已完成（{status}），无需重复 prepare")
            return 0
        if status == "draft":
            write_scaffold(prev)  # 自愈：补齐缺失文件，不覆盖已有内容
            payload = {"status": "draft-exists", "date": date.isoformat(),
                       "digest": prev.get("digest_path"),
                       "selected": [{"id": p["id"], "title": p["title"],
                                     "note_path": p["note_path"]} for p in prev["selected"]],
                       "count": len(prev["selected"])}
            _emit(payload, args.json, f"{date} 已存在草稿（{len(prev['selected'])} 篇），继续精读即可")
            return 0
    if prev and args.force and prev.get("status") != "draft":
        die(f"{date} 已是 {prev.get('status')} 状态，禁止 --force 覆盖已完成内容；"
            f"如需重做，请先手工移除 state/digest/note 或新建日期", 2)

    fetch_meta: dict = {}
    if args.offline:
        cache_file = CACHE_DIR / f"candidates-{date.isoformat()}.json"
        cached = read_json(cache_file)
        if not cached:
            die(f"--offline 模式需要本地缓存 {cache_file}，未找到", 2)
        cands = cached.get("candidates") or []
        fetch_meta = cached.get("meta") or {}
        fetch_meta["offline"] = True
        log(f"离线模式：使用缓存候选 {len(cands)} 条")
    else:
        log(f"开始抓取候选（目标日期 {date}）")
        cands, fetch_meta = fetch_candidates(profile, sources, date)
        if not cands:
            errors = (fetch_meta.get("arxiv") or {}).get("errors") or []
            payload = {"status": "failed", "date": date.isoformat(), "errors": errors}
            _emit(payload, args.json, "抓取失败：所有数据源均未返回候选")
            return 2
        write_json(CACHE_DIR / f"candidates-{date.isoformat()}.json",
                   {"meta": fetch_meta, "candidates": cands})

    seen = read_json(seen_path(), {"version": 1, "ids": {}, "titles": {}})
    pool, counts = build_pool(profile, cands, seen, date)
    selected, backups = select_candidates(profile, pool, count)
    log(f"候选 {counts['candidates']} → 可用 {counts['pool']} → 入选 {len(selected)}")

    state = build_state(date, profile, selected, backups, fetch_meta, counts, pool)
    state["updated_at"] = iso_now()
    digest = digest_path(date)
    if selected:
        atomic_write(digest, render_digest(date, len(selected), selected, backups, state))
        for paper in selected:
            atomic_write(note_path(date, paper), render_note(date, paper, digest))
        state["status"] = "draft"
    else:
        atomic_write(digest, render_empty_digest(date, state))
        state["status"] = "empty"
    write_json(state_path(date), state)
    if prev and args.force:
        cleanup_orphan_notes(prev, state)
    payload = {
        "status": state["status"],
        "date": date.isoformat(),
        "digest": state["digest_path"],
        "count": len(selected),
        "selected": [{"id": p["id"], "title": p["title"], "topic": p["topic"],
                      "venue": p["venue"], "score": p["score"], "note_path": p["note_path"]}
                     for p in state["selected"]],
        "backups": [b["title"] for b in state["backups"]],
        "counts": counts,
    }
    _emit(payload, args.json, f"已生成 {date} 草稿：{len(selected)} 篇 → {state['digest_path']}")
    return 0


def cleanup_orphan_notes(prev_state: dict, new_state: dict) -> None:
    """--force 重做草稿时，删除旧草稿中已不再入选论文的 note 文件。"""
    keep = {p.get("note_path") for p in new_state.get("selected") or []}
    for paper in prev_state.get("selected") or []:
        rel = paper.get("note_path")
        if not rel or rel in keep:
            continue
        target = ROOT / rel
        if target.exists():
            target.unlink()
            log(f"清理旧草稿 note：{rel}")


def render_empty_digest(date: dt.date, state: dict) -> str:
    fetch = state.get("fetch") or {}
    lines = [
        f"# {date.isoformat()} · 论文雷达",
        "",
        "> 状态：今日无新增候选（周末/假期，或数据源暂时不可用）；本页保留记录。  ",
        "> 数据源：arXiv（主） · HuggingFace Daily（热度） · 生成：paper-radar",
        "",
        "## 今日看点",
        "",
        "今日没有可用的新候选（可能原因：arXiv 无新公告、候选均已在历史雷达中读过、网络抓取失败）。",
        "可以回看最近一期：[digests/index.md](index.md)。",
        "",
        "## 抓取信息",
        "",
        f"- arXiv 请求：{(fetch.get('arxiv') or {}).get('requests', 0)} 次；"
        f"失败：{len((fetch.get('arxiv') or {}).get('errors') or [])} 次",
        f"- 候选：{(state.get('counts') or {}).get('candidates', 0)} 条；"
        f"排除历史已读：{(state.get('counts') or {}).get('excluded_seen', 0)} 条",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", re.S | re.M)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def validate_note(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"缺失论文文件：{path}"]
    text = path.read_text(encoding="utf-8")
    remaining = PLACEHOLDER_RE.findall(text)
    if remaining:
        errors.append(f"{path.name} 仍有未填占位符：{', '.join(sorted(set(remaining)))}")
    for key, heading, min_len in NOTE_SECTIONS:
        body = section_text(text, heading)
        if not body:
            errors.append(f"{path.name} 缺少小节或为空：「{heading}」")
            continue
        if PLACEHOLDER_RE.search(body):
            continue
        if len(body) < min_len:
            errors.append(f"{path.name}「{heading}」过短（{len(body)} < {min_len} 字）")
    return errors


def validate_digest(path: Path, expected_count: int) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"缺失 digest：{path}"]
    text = path.read_text(encoding="utf-8")
    remaining = PLACEHOLDER_RE.findall(text)
    if remaining:
        errors.append(f"{path.name} 仍有未填占位符：{', '.join(sorted(set(remaining)))}")
    overview = section_text(text, "今日看点")
    if len(overview) < 60:
        errors.append(f"{path.name}「今日看点」过短（{len(overview)} < 60 字）")
    entries = re.findall(r"^###\s+\d+\.", text, re.M)
    if expected_count and len(entries) != expected_count:
        errors.append(f"{path.name} 精选条目 {len(entries)} 篇，与状态中的 {expected_count} 篇不一致")
    tldr_lines = re.findall(r"^-\s+\*\*一句话\*\*：(.+)$", text, re.M)
    if expected_count and len(tldr_lines) != expected_count:
        errors.append(f"{path.name} 「一句话」行数 {len(tldr_lines)} 与 {expected_count} 不一致")
    for idx, line in enumerate(tldr_lines, 1):
        if len(clean_text(line)) < 10:
            errors.append(f"{path.name} 第 {idx} 篇「一句话」过短")
    return errors


def validate_scaffold(state: dict) -> list[str]:
    date = dt.date.fromisoformat(state["date"])
    errors = validate_digest(digest_path(date), len(state.get("selected") or []))
    for paper in state.get("selected") or []:
        errors.extend(validate_note(ROOT / paper["note_path"]))
    return errors


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _load_papers_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    path = papers_jsonl()
    if not path.exists():
        return index
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("id"):
            index[rec["id"]] = rec
    return index


def _write_papers_index(index: dict[str, dict]) -> None:
    lines = [json.dumps(index[k], ensure_ascii=False, sort_keys=True) for k in sorted(index)]
    atomic_write(papers_jsonl(), "\n".join(lines) + ("\n" if lines else ""))


def _set_status_line(path: Path, label: str) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(r"^> 状态：.*$", f"> 状态：{label}", text, count=1, flags=re.M)
    if count:
        atomic_write(path, new_text)


def _detect_mode(state: dict, text: str) -> str:
    for mode, label in MODE_LABELS.items():
        if label in text:
            return mode
    return "auto"


def update_seen(state: dict) -> None:
    seen = read_json(seen_path(), {"version": 1, "ids": {}, "titles": {}})
    seen.setdefault("ids", {})
    seen.setdefault("titles", {})
    for paper in state.get("selected") or []:
        seen["ids"][paper["id"]] = {
            "date": state["date"],
            "title": paper["title"],
            "topics": [paper.get("topic", "")],
            "venue": paper.get("venue", ""),
        }
        seen["titles"][norm_title(paper["title"])] = paper["id"]
    seen["updated_at"] = iso_now()
    write_json(seen_path(), seen)


def cmd_finalize(args) -> int:
    date = parse_date(args.date)
    ensure_dirs()
    state = read_json(state_path(date))
    if not state:
        die(f"{date} 没有 state 文件，请先运行 prepare", 2)
    digest = digest_path(date)
    if state.get("status") in ("final", "empty"):
        payload = {"status": "already-final", "date": date.isoformat(),
                   "digest": state.get("digest_path")}
        _emit(payload, args.json, f"{date} 已完成，无需重复 finalize")
        return 0

    errors = validate_scaffold(state)
    if errors:
        for err in errors:
            log(f"  ✗ {err}")
        payload = {"status": "invalid", "date": date.isoformat(), "errors": errors}
        _emit(payload, args.json, f"校验失败：{len(errors)} 处问题")
        return 1

    mode = args.summary_mode or state.get("summary_mode") or "pending"
    if mode not in MODE_LABELS or mode == "pending":
        mode = _detect_mode(state, digest.read_text(encoding="utf-8"))
    label = MODE_LABELS.get(mode, MODE_LABELS["auto"])

    # 写入数据索引
    index = _load_papers_index()
    for paper in state["selected"]:
        index[paper["id"]] = {
            "id": paper["id"],
            "title": paper["title"],
            "authors": paper.get("authors") or [],
            "published": paper.get("published", ""),
            "date": state["date"],
            "topic": paper.get("topic", ""),
            "venue": paper.get("venue", ""),
            "score": paper.get("score", 0),
            "abs_url": paper.get("abs_url", ""),
            "pdf_url": paper.get("pdf_url", ""),
            "html_url": paper.get("html_url", ""),
            "code_url": paper.get("code_url", ""),
            "hf_url": paper.get("hf_url", ""),
            "note_path": paper.get("note_path", ""),
            "digest_path": state["digest_path"],
            "summary_mode": mode,
        }
    _write_papers_index(index)

    update_seen(state)

    state["status"] = "final"
    state["summary_mode"] = mode
    state["finalized_at"] = iso_now()
    state["updated_at"] = iso_now()
    write_json(state_path(date), state)

    _set_status_line(digest, label)
    for paper in state["selected"]:
        _set_status_line(ROOT / paper["note_path"], label)

    rebuild_indexes()

    payload = {"status": "final", "date": date.isoformat(), "count": len(state["selected"]),
               "summary_mode": mode, "digest": state["digest_path"],
               "digest_url": f"https://github.com/hhxpoint/ai-paper-radar/blob/main/{state['digest_path']}"}
    _emit(payload, args.json, f"{date} 已完成（{label}，{len(state['selected'])} 篇）")

    if args.git:
        ok = git_commit_push(
            f"每日论文雷达：{date.isoformat()}（{len(state['selected'])} 篇，{label}）",
            dry_run=args.dry_run,
        )
        if not ok:
            return 3 if not args.dry_run else 0
    return 0


def git_commit_push(message: str, dry_run: bool = False) -> bool:
    def run(*cmd, check=True):
        return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

    if not (ROOT / ".git").exists():
        log("WARN: 不是 git 仓库，跳过提交")
        return False
    run("git", "add", "-A")
    status = run("git", "status", "--porcelain")
    if not (status.stdout or "").strip():
        log("没有需要提交的变更")
        return False
    if dry_run:
        log(f"[dry-run] 将提交：{message}")
        return True
    commit = run("git", "commit", "-m", message)
    if commit.returncode != 0:
        log(f"WARN: git commit 失败：{commit.stderr.strip()[:500]}")
        return False
    log(f"已提交：{message}")
    delays = [0, 10, 20, 30, 40]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        push = run("git", "push", "origin", "HEAD:main")
        if push.returncode == 0:
            log("已推送到 origin/main")
            return True
        log(f"WARN: push 失败（第 {attempt}/{len(delays)} 次）：{push.stderr.strip()[:300]}")
        pull = run("git", "pull", "--rebase", "origin", "main")
        if pull.returncode != 0:
            log(f"WARN: pull --rebase 失败（继续重试 push）：{pull.stderr.strip()[:300]}")
    # 网络到 github.com 被阻断时，退回 GitHub REST API 发布（api.github.com 通常仍可用）
    log("WARN: git push 连续失败，尝试 GitHub API 兜底发布")
    publish = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "publish_api.py"), "--json", "--message", message],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if publish.returncode == 0:
        log("已通过 GitHub API 发布成功")
        return True
    log(f"ERROR: API 兜底发布也失败：{(publish.stderr or publish.stdout).strip()[:500]}")
    return False


# ---------------------------------------------------------------------------
# 索引重建
# ---------------------------------------------------------------------------

def _mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode or "auto", MODE_LABELS["auto"])


def _load_all_states() -> list[dict]:
    states = []
    if not DAILY_DIR.exists():
        return states
    for path in sorted(DAILY_DIR.glob("*.json")):
        state = read_json(path)
        if state and state.get("date"):
            states.append(state)
    return states


def _topic_summary(state: dict) -> str:
    counts: dict[str, int] = {}
    for paper in state.get("selected") or []:
        topic = paper.get("topic") or "其他"
        counts[topic] = counts.get(topic, 0) + 1
    return " · ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))


def rebuild_indexes() -> None:
    states = _load_all_states()
    finished = [s for s in states if s.get("status") in ("final", "empty")]
    finished.sort(key=lambda s: s["date"], reverse=True)
    index = _load_papers_index()

    by_topic: dict[str, int] = {}
    by_venue: dict[str, int] = {}
    by_month: dict[str, int] = {}
    for rec in index.values():
        topic = rec.get("topic") or "其他"
        by_topic[topic] = by_topic.get(topic, 0) + 1
        venue = rec.get("venue") or ""
        if venue:
            key = venue
            by_venue[key] = by_venue.get(key, 0) + 1
        month = (rec.get("date") or "")[:7]
        if month:
            by_month[month] = by_month.get(month, 0) + 1

    stats = {
        "updated_at": iso_now(),
        "papers_total": len(index),
        "digests_total": len([s for s in finished if s.get("status") == "final"]),
        "empty_days": len([s for s in finished if s.get("status") == "empty"]),
        "by_topic": dict(sorted(by_topic.items(), key=lambda x: -x[1])),
        "by_venue": dict(sorted(by_venue.items(), key=lambda x: -x[1])),
        "by_month": dict(sorted(by_month.items(), reverse=True)),
        "last_digest": finished[0]["date"] if finished else "",
    }
    write_json(DATA_DIR / "stats.json", stats)

    # digests/index.md
    lines = [
        "# 每日论文雷达 · 归档",
        "",
        f"> 共 {stats['digests_total']} 期 · 累计 {stats['papers_total']} 篇 · "
        f"最近更新 {stats['last_digest'] or '—'}  ",
        "> 本页由 `scripts/paper_radar.py` 自动生成，请勿手动编辑。",
        "",
        "| 日期 | 精选 | 主题分布 | 模式 | 链接 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for state in finished:
        if state.get("status") != "final":
            continue
        name = state["digest_path"].split("/")[-1]
        lines.append(f"| {state['date']} | {len(state.get('selected') or [])} 篇 | "
                     f"{_topic_summary(state)} | {_mode_label(state.get('summary_mode', 'auto'))} | "
                     f"[{name}]({name}) |")
    if stats["empty_days"]:
        lines.append("")
        lines.append(f"> 另有 {stats['empty_days']} 天无新增候选（周末/假期），保留空记录。")
    lines.append("")
    atomic_write(DIGESTS_DIR / "index.md", "\n".join(lines))

    _update_readme(stats, finished)
    _write_venues_stats(stats, index)


def _update_readme(stats: dict, finished: list[dict]) -> None:
    readme = ROOT / "README.md"
    if not readme.exists():
        log("WARN: README.md 不存在，跳过最新一期区块更新")
        return
    text = readme.read_text(encoding="utf-8")
    start_marker = "<!-- LATEST:START -->"
    end_marker = "<!-- LATEST:END -->"
    if start_marker not in text or end_marker not in text:
        log("WARN: README 缺少 LATEST 标记，跳过更新")
        return
    rows = []
    latest = next((s for s in finished if s.get("status") == "final"), None)
    if latest:
        rows.append(f"### 最新一期：[{latest['date']} · 论文雷达]({latest['digest_path']})")
        rows.append("")
        rows.append(f"> {len(latest.get('selected') or [])} 篇 · {_topic_summary(latest)} · "
                    f"{_mode_label(latest.get('summary_mode', 'auto'))}")
        rows.append("")
    rows.append("| 日期 | 精选 | 主题分布 | 模式 |")
    rows.append("| --- | --- | --- | --- |")
    for state in finished[:7]:
        if state.get("status") != "final":
            continue
        name = state["digest_path"].split("/")[-1]
        rows.append(f"| [{state['date']}]({state['digest_path']}) | {len(state.get('selected') or [])} 篇 | "
                    f"{_topic_summary(state)} | {_mode_label(state.get('summary_mode', 'auto'))} |")
    rows.append("")
    rows.append(f"> 累计 {stats['papers_total']} 篇 · 归档：[digests/index.md](digests/index.md) · "
                f"自动更新于 {now_cn():%Y-%m-%d %H:%M} (UTC+8)")
    block = "\n".join([start_marker, "", *rows, "", end_marker])
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.S)
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        text = text.replace(start_marker, block)
    atomic_write(readme, text)


def _write_venues_stats(stats: dict, index: dict[str, dict]) -> None:
    if not VENUES_DIR.exists():
        return
    venues = stats.get("by_venue") or {}
    lines = [
        "# 顶会与主题统计（自动生成）",
        "",
        f"> 数据来源：`data/papers.jsonl` · 累计 {stats['papers_total']} 篇 · "
        f"更新于 {now_cn():%Y-%m-%d %H:%M} (UTC+8)  ",
        "> 本页由 `scripts/paper_radar.py` 自动生成，请勿手动编辑。",
        "",
        "## 顶会/期刊论文",
        "",
    ]
    if venues:
        lines.extend(["| Venue | 篇数 |", "| --- | --- |"])
        for name, cnt in venues.items():
            lines.append(f"| {name} | {cnt} |")
    else:
        lines.append("暂无（雷达会从 arXiv comment/journal_ref 中自动识别顶会录用）。")
    lines.extend([
        "",
        "## 主题分布",
        "",
        "| 主题 | 篇数 | 占比 |",
        "| --- | --- | --- |",
    ])
    total = max(1, stats.get("papers_total", 0))
    for name, cnt in (stats.get("by_topic") or {}).items():
        lines.append(f"| {name} | {cnt} | {cnt / total:.0%} |")
    lines.extend([
        "",
        "## 按月产出",
        "",
        "| 月份 | 篇数 |",
        "| --- | --- |",
    ])
    for month, cnt in (stats.get("by_month") or {}).items():
        lines.append(f"| {month} | {cnt} |")
    lines.append("")
    atomic_write(VENUES_DIR / "stats.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# status / check
# ---------------------------------------------------------------------------

def cmd_status(args) -> int:
    days = args.days or 14
    today = today_cn()
    rows = []
    for offset in range(days):
        date = today - dt.timedelta(days=offset)
        state = read_json(state_path(date))
        rows.append({
            "date": date.isoformat(),
            "weekday": "一二三四五六日"[date.weekday()],
            "status": (state or {}).get("status", "missing"),
            "count": len((state or {}).get("selected") or []),
            "summary_mode": (state or {}).get("summary_mode"),
            "digest": (state or {}).get("digest_path"),
        })
    states = _load_all_states()
    pending = [s["date"] for s in states if s.get("status") == "draft"]
    index = _load_papers_index()
    payload = {
        "today": today.isoformat(),
        "rows": rows,
        "pending_drafts": sorted(pending),
        "papers_total": len(index),
        "seen_total": len((read_json(seen_path(), {}) or {}).get("ids") or {}),
        "digests_total": len([s for s in states if s.get("status") == "final"]),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        log(f"近期状态（今天 {today}）")
        for row in rows:
            log(f"  {row['date']} 周{row['weekday']}  {row['status']:8s}  "
                f"{row['count']} 篇  {row['summary_mode'] or ''}")
        log(f"待精读草稿：{', '.join(sorted(pending)) or '无'}")
        log(f"累计：{len(index)} 篇论文 · {payload['digests_total']} 期雷达 · "
            f"{payload['seen_total']} 篇已读")
    return 0


def cmd_check(args) -> int:
    errors: list[str] = []
    warnings: list[str] = []

    readme = ROOT / "README.md"
    if not readme.exists():
        errors.append("缺少 README.md")
    else:
        text = readme.read_text(encoding="utf-8")
        for marker in ("<!-- LATEST:START -->", "<!-- LATEST:END -->"):
            if marker not in text:
                errors.append(f"README.md 缺少标记 {marker}")
    if not (DIGESTS_DIR / "index.md").exists():
        errors.append("缺少 digests/index.md（运行 finalize 或 index 生成）")

    index = _load_papers_index()
    note_paths: dict[str, str] = {}
    for pid, rec in index.items():
        note = ROOT / (rec.get("note_path") or "")
        if not rec.get("note_path") or not note.exists():
            errors.append(f"papers.jsonl 中 {pid} 的 note_path 不存在：{rec.get('note_path')}")
        else:
            if note.as_posix() in note_paths:
                errors.append(f"note 文件被多篇论文引用：{note_paths[note.as_posix()]} / {pid}")
            note_paths[note.as_posix()] = pid
        for key in ("abs_url", "pdf_url", "html_url"):
            url = rec.get(key) or ""
            if url and not url.startswith("https://"):
                errors.append(f"papers.jsonl 中 {pid} 的 {key} 不是 https：{url}")
        if not rec.get("title"):
            errors.append(f"papers.jsonl 中 {pid} 缺少标题")
    # 重复 id
    seen_ids: dict[str, int] = {}
    path = papers_jsonl()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"papers.jsonl 存在非法 JSON 行：{exc}")
                continue
            if rec.get("id") in seen_ids:
                errors.append(f"papers.jsonl 重复 id：{rec.get('id')}")
            seen_ids[rec.get("id", "")] = 1

    states = _load_all_states()
    seen = read_json(seen_path(), {"ids": {}}) or {}
    seen_ids2 = (seen.get("ids") or {})
    referenced_notes: set[str] = set(index[k].get("note_path") for k in index if index[k].get("note_path"))
    for state in states:
        for paper in state.get("selected") or []:
            if paper.get("note_path"):
                referenced_notes.add(paper["note_path"])
    for state in states:
        date = state.get("date", "")
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            errors.append(f"非法的 state 日期：{date}")
            continue
        status = state.get("status")
        if status == "final":
            errors.extend(validate_scaffold(state))
            for paper in state.get("selected") or []:
                if paper["id"] not in index:
                    errors.append(f"{date} 的 {paper['id']} 未写入 papers.jsonl")
                if paper["id"] not in seen_ids2:
                    errors.append(f"{date} 的 {paper['id']} 未写入 seen.json")
        elif status == "draft":
            warnings.append(f"{date} 仍是草稿（待精读补全）")
        elif status == "empty":
            if not digest_path(dt.date.fromisoformat(date)).exists():
                errors.append(f"{date} 空记录缺少 digest")
        else:
            errors.append(f"{date} 状态非法：{status}")
    for pid in seen_ids2:
        if pid not in index:
            errors.append(f"seen.json 中的 {pid} 不在 papers.jsonl")

    # 游离 note：papers/ 下存在但没有任何 state / papers.jsonl 引用（多为旧草稿重做后的残留）
    if PAPERS_DIR.exists():
        for md in sorted(PAPERS_DIR.glob("*/*.md")):
            rel = md.relative_to(ROOT).as_posix()
            if rel not in referenced_notes:
                errors.append(f"发现游离 note（未被任何 state/papers.jsonl 引用）：{rel}")

    # 历史 digest：占位符检查
    for md in sorted(DIGESTS_DIR.glob("*.md")):
        if md.name == "index.md":
            continue
        if PLACEHOLDER_RE.search(md.read_text(encoding="utf-8")):
            errors.append(f"{md.relative_to(ROOT)} 仍有未填占位符")
    weekly_dir = DIGESTS_DIR / "weekly"
    if weekly_dir.exists():
        for md in sorted(weekly_dir.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            if PLACEHOLDER_RE.search(text):
                errors.append(f"{md.relative_to(ROOT)} 仍有未填占位符")
            if len(section_text(text, "本周趋势与关联")) < 40:
                warnings.append(f"{md.relative_to(ROOT)} 的「本周趋势与关联」内容偏少")

    if errors:
        log(f"check 失败：{len(errors)} 个错误")
        for err in errors:
            log(f"  ✗ {err}")
        for warn in warnings:
            log(f"  ! {warn}")
        return 1
    log(f"check 通过：{len(index)} 篇论文 · {len(states)} 天状态 · "
        f"{len(warnings)} 个提醒")
    for warn in warnings:
        log(f"  ! {warn}")
    if args.strict and warnings:
        log("strict 模式下提醒视为失败")
        return 1
    return 0


# ---------------------------------------------------------------------------
# fulltext / download
# ---------------------------------------------------------------------------

def extract_article_text(raw: str) -> str:
    match = re.search(r"<article\b[^>]*>(.*?)</article>", raw, re.S | re.I)
    body = match.group(1) if match else raw
    body = re.sub(r"<(script|style|nav|header|footer)\b[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<section\b[^>]*ltx_bibliography[^>]*>.*?</section>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<h([1-6])\b[^>]*>", "\n\n## ", body, flags=re.I)
    body = re.sub(r"</h[1-6]>", "\n", body, flags=re.I)
    body = re.sub(r"<(p|div|section|li|tr|table|blockquote|figcaption)\b[^>]*>", "\n", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    lines = []
    for line in body.splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    text = re.split(r"\n##\s+(References|Bibliography|Acknowledg(e)?ments?)\b", text)[0]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def fetch_fulltext(cid: str, *, timeout: int = 90, retries: int = 2) -> tuple[str, str]:
    """抓取 arXiv HTML 全文并抽取纯文本，返回 (text, source_url)。失败抛异常。"""
    ensure_dirs()
    cache_dir = CACHE_DIR / "html"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cid}.html"
    if cache_file.exists() and cache_file.stat().st_size > 5000:
        return extract_article_text(cache_file.read_text(encoding="utf-8", errors="replace")), "cache"
    errors = []
    for url in (f"https://arxiv.org/html/{cid}", f"https://ar5iv.labs.arxiv.org/html/{cid}"):
        try:
            raw = http_get(url, timeout=timeout, retries=retries)
            if len(raw) < 5000:
                raise RuntimeError("页面过短，可能不是全文")
            atomic_write(cache_file, raw)
            return extract_article_text(raw), url
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
    raise RuntimeError("; ".join(errors))


def lookup_paper(cid: str) -> dict:
    index = _load_papers_index()
    if cid in index:
        return index[cid]
    for state in _load_all_states():
        for paper in state.get("selected") or []:
            if paper.get("id") == cid:
                return paper
    return {}


def cmd_fulltext(args) -> int:
    cid = norm_id(args.arxiv_id)
    if not cid:
        die("请提供 arXiv id", 2)
    try:
        text, source = fetch_fulltext(cid)
        total = len(text)
        chunk = text[args.offset:args.offset + args.chars]
        header = f"# arXiv:{cid} 全文节选（来源：{source}，总长 {total} 字符，偏移 {args.offset}）\n"
        print(header)
        print(chunk)
        if args.offset + args.chars < total:
            print(f"\n[paper-radar] 还有 {total - args.offset - args.chars} 字符未显示，"
                  f"可用 --offset {args.offset + args.chars} 继续读取。")
    except Exception as exc:  # noqa: BLE001
        paper = lookup_paper(cid)
        abstract = paper.get("abstract") or ""
        if not abstract:
            log(f"WARN: HTML 全文不可用（{exc}），且本地没有摘要记录")
            return 2
        print(f"# arXiv:{cid} 全文不可用（{exc}），以下为已记录的摘要：\n")
        print(abstract)
    return 0


def cmd_download(args) -> int:
    date = parse_date(args.date)
    state = read_json(state_path(date))
    if not state:
        die(f"{date} 没有 state 文件", 2)
    out_dir = ROOT / (args.out or "library") / f"{date:%Y-%m}"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for paper in state.get("selected") or []:
        target = out_dir / f"{paper['id']}-{slugify(paper['title'])}.pdf"
        if target.exists() and target.stat().st_size > 10_000:
            downloaded.append(str(target))
            continue
        try:
            raw = http_get(paper["pdf_url"], timeout=120, retries=3, binary=True)
            if len(raw) < 10_000:
                raise RuntimeError("PDF 过小")
            target.write_bytes(raw)
            downloaded.append(str(target))
            log(f"已下载：{target.name}（{len(raw) // 1024} KB）")
        except Exception as exc:  # noqa: BLE001
            log(f"WARN: 下载失败 {paper['id']}：{exc}")
    print(json.dumps({"date": date.isoformat(), "downloaded": downloaded}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# weekly
# ---------------------------------------------------------------------------

def iso_week_id(date: dt.date) -> str:
    year, week, _ = date.isocalendar()
    return f"{year}-W{week:02d}"


def cmd_weekly(args) -> int:
    date = parse_date(args.date) if getattr(args, "date", "") else today_cn()
    if args.week:
        if not re.fullmatch(r"\d{4}-W\d{2}", args.week):
            die("--week 需要 YYYY-Www 格式，如 2026-W48", 2)
        week_id = args.week
        year, week = int(week_id[:4]), int(week_id[-2:])
        try:
            monday = dt.date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            die(f"非法周编号 {week_id}：{exc}", 2)
    else:
        monday = date - dt.timedelta(days=date.weekday())
        week_id = iso_week_id(monday)
    sunday = monday + dt.timedelta(days=6)

    rows: list[dict] = []
    for offset in range(7):
        day = monday + dt.timedelta(days=offset)
        state = read_json(state_path(day))
        if not state or state.get("status") != "final":
            continue
        for paper in state.get("selected") or []:
            rows.append({"date": day.isoformat(), "paper": paper})
    if not rows:
        print(json.dumps({"status": "empty", "week": week_id}, ensure_ascii=False))
        return 0

    out = DIGESTS_DIR / "weekly" / f"{week_id}.md"
    if out.exists() and not args.force:
        print(json.dumps({"status": "exists", "week": week_id, "path": out.relative_to(ROOT).as_posix()},
                         ensure_ascii=False))
        return 0
    lines = [
        f"# {week_id} · 本周观点雷达",
        "",
        f"> 范围：{monday.isoformat()} ～ {sunday.isoformat()} · 收录 {len(rows)} 篇  ",
        "> 状态：草稿（待智能体填写）  ",
        "> 由 `scripts/paper_radar.py weekly` 生成骨架；自动生成内容可能有误。",
        "",
        "## 本周论文清单",
        "",
        "| 日期 | 论文 | 主题 | Venue | 链接 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        paper = row["paper"]
        lines.append(f"| {row['date']} | {paper['title']} | {paper.get('topic', '')} | "
                     f"{paper.get('venue', '') or '—'} | "
                     f"[笔记](../../{paper['note_path']}) · [arXiv]({paper['abs_url']}) |")
    lines.extend([
        "",
        "## 本周趋势与关联",
        "",
        "{{WEEKLY_OVERVIEW}}",
        "",
        "## 值得追踪的问题",
        "",
        "{{WEEKLY_QUESTIONS}}",
        "",
        "---",
        "",
        "> 周报由每日雷达汇总而成，聚焦「观点、矛盾、可追踪问题」，不做论文搬运。",
        "",
    ])
    atomic_write(out, "\n".join(lines))
    text = out.read_text(encoding="utf-8")
    print(json.dumps({"status": "draft", "week": week_id, "path": out.relative_to(ROOT).as_posix(),
                      "placeholders": sorted(set(PLACEHOLDER_RE.findall(text))),
                      "count": len(rows)}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        log(human)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper_radar.py",
                                     description="AI 论文雷达每日流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="抓取候选并生成草稿")
    p.add_argument("--date", default="", help="目标日期 YYYY-MM-DD（默认今天，UTC+8）")
    p.add_argument("--count", type=int, default=0, help="精选篇数（默认取 profile.daily_count）")
    p.add_argument("--offline", action="store_true", help="使用本地缓存候选，不联网")
    p.add_argument("--force", action="store_true", help="覆盖已有草稿（会覆盖已写的总结，慎用）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("finalize", help="校验总结并完成当日雷达")
    p.add_argument("--date", default="", help="目标日期（默认今天）")
    p.add_argument("--strict", action="store_true", help="严格模式（保留参数）")
    p.add_argument("--summary-mode", default="", choices=["", "auto", "llm", "fallback", "manual"],
                   help="记录摘要来源模式")
    p.add_argument("--git", action="store_true", help="完成后自动 commit & push")
    p.add_argument("--dry-run", action="store_true", help="配合 --git：只演练不提交")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("status", help="查看近期状态")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="仓库完整性检查")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fulltext", help="抓取并输出 arXiv HTML 正文")
    p.add_argument("arxiv_id")
    p.add_argument("--chars", type=int, default=14000)
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_fulltext)

    p = sub.add_parser("download", help="下载当日论文 PDF 到本地 library/")
    p.add_argument("--date", default="")
    p.add_argument("--out", default="library")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("weekly", help="生成《本周观点雷达》周报骨架")
    p.add_argument("--date", default="")
    p.add_argument("--week", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_weekly)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        log(f"未捕获异常：{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
