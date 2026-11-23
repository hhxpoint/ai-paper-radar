#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub Models（Actions 内 GITHUB_TOKEN / 本地 gh token）为当日草稿生成中文速读摘要。

定位：GitHub Actions 云端的「兜底摘要器」。本地每日智能体会在此基础上做更深的精读改写。
失败策略：任何一篇失败都退化为确定性的「摘要速览」，保证流水线不产出空文件、不红。

用法：
    python scripts/summarize.py [--date YYYY-MM-DD] [--model ...] [--fallback-model ...]
                                [--max-fulltext-chars N] [--no-fulltext] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import paper_radar as pr  # 同目录模块


NOTE_KEYS = ["tldr", "why_read", "problem", "insight", "evidence", "critique", "relevance", "further"]
NOTE_PLACEHOLDERS = {
    "tldr": "TLDR", "why_read": "WHY_READ", "problem": "PROBLEM", "insight": "INSIGHT",
    "evidence": "EVIDENCE", "critique": "CRITIQUE", "relevance": "RELEVANCE", "further": "FURTHER",
}

SYSTEM_PROMPT = """你是一位资深 AI 研究员和论文审稿人，研究方向覆盖大语言模型推理、语音/音频、多模态。
你正在维护一份「有观点的论文雷达」，读者是 hhxpoint：AI 研究者，关注语音/音频（TTS、ASR、病理语音）、大模型与推理、多模态。

写作要求（严格遵守）：
1. 只依据给定的摘要与正文节选；严禁编造实验数字、数据集、对比结果或引用。正文没给的信息写「节选中未体现」。
2. 中文，专业、克制、可直接阅读。不要用「本文提出了一种……」这类空话开头，直接给结论和观点。
3. 观点优先：说清楚这篇论文改变了什么认知、哪里可能站不住，而不是复述摘要。
4. 具体：尽量引用论文中的关键数字、实验设置、对照基线；没有就说明。
5. 严禁输出 JSON 之外的内容。"""

USER_TEMPLATE = """【论文信息】
标题：{title}
arXiv：{arxiv}
作者：{authors}
发表：{published}
领域主题：{topic}
Venue：{venue}
arXiv comment：{comment}

【摘要】
{abstract}

【正文节选（可能被截断）】
{fulltext}

请输出一个 JSON 对象（全部值为中文字符串，不要 Markdown 代码块），键名与长度要求：
{{
  "tldr": "一句话结论，≤80字，包含方法要点与关键结果",
  "why_read": "为什么值得读、观点/反常识在哪，80-160字",
  "problem": "问题与动机（之前方法缺什么），60-140字",
  "insight": "方法与关键洞察（机制层面为什么可能 work），120-240字",
  "evidence": "证据与关键实验（必须带具体数字/实验设置/基线；没有就说明），60-160字",
  "critique": "可质疑之处/局限，至少 2 点且要具体，60-160字",
  "relevance": "对语音/音频、大模型推理、多模态研究或工程实践的关联，40-100字",
  "further": "延伸阅读或可追踪方向，≤80字"
}}"""

OVERVIEW_PROMPT = """下面是今天入选论文雷达的论文标题与一句话结论。请写一段「今日看点」（120-220 字，中文，纯文本）：
要求：指出这批论文的共同主题或互相矛盾之处，点出最值得先读的一篇及理由，不要逐篇罗列。

{items}"""


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def get_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


def load_llm_config():
    sources = pr.load_yaml(pr.CONFIG_DIR / "sources.yml")
    llm = sources.get("llm") or {}
    endpoint = llm.get("endpoint") or "https://models.github.ai/inference/chat/completions"
    return endpoint, int(llm.get("request_timeout_seconds", 120))


def chat(endpoint: str, token: str, model: str, messages: list[dict], *,
         max_tokens: int, temperature: float, timeout: int,
         json_mode: bool = True) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {token}"}
    resp = pr.http_post_json(endpoint, payload, headers=headers, timeout=timeout, retries=1)
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"模型返回格式异常：{exc}; {str(resp)[:200]}") from exc


def call_model(endpoint: str, token: str, models: list[str], messages: list[dict], *,
               max_tokens: int, temperature: float, timeout: int) -> tuple[str, str]:
    """依次尝试多个模型/参数组合，返回 (content, model_used)。全部失败抛异常。"""
    last = ""
    for model in models:
        for json_mode in (True, False):
            try:
                content = chat(endpoint, token, model, messages, max_tokens=max_tokens,
                               temperature=temperature, timeout=timeout, json_mode=json_mode)
                if content.strip():
                    return content, model
                last = "空响应"
            except Exception as exc:  # noqa: BLE001
                last = f"{model}: {exc}"
                pr.log(f"    WARN: {last}")
                time.sleep(2)
    raise RuntimeError(last or "所有模型均调用失败")


def parse_summary_json(content: str) -> dict:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("不是 JSON 对象")
    return data


# ---------------------------------------------------------------------------
# 兜底摘要（不依赖 LLM）
# ---------------------------------------------------------------------------

def fallback_summary(paper: dict) -> dict:
    abstract = pr.clean_text(paper.get("abstract") or "")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abstract) if s.strip()]
    tldr = sentences[0][:180] if sentences else ""
    if len(tldr) < 15:
        tldr = f"（自动速览）{paper.get('topic') or 'AI'} 方向新论文：{(paper.get('title') or '')[:120]}"
    evidence = "；".join(sentences[1:4])[:280] if len(sentences) > 1 else ""
    if len(evidence) < 30:
        evidence = "摘要中未给出具体实验数字；待精读后补充关键结果与对照设置。"
    insight = abstract[:300] if abstract else "摘要缺失；待精读正文后补充方法与机制。"
    if len(insight) < 60:
        insight = (insight + "；该论文的机制细节需阅读正文确认，当前仅依据摘要做结构化速览。")[:300]
    venue = paper.get("venue") or ""
    topic = paper.get("topic") or "AI"
    return {
        "tldr": tldr,
        "why_read": f"（自动速览）{topic} 方向新工作" + (f"，发表于 {venue}" if venue else "") +
                    "。入选原因：命中「有观点 / 顶会 / 兴趣画像」评分信号；具体观点分析待每日精读补全。",
        "problem": "自动速览：该论文的问题设定与动机需要精读摘要和引言确认。当前仅依据摘要做结构化速览，"
                   "不排除作者叙述与实际问题存在偏差。",
        "insight": insight,
        "evidence": evidence,
        "critique": "自动速览未做深入评估，建议精读时重点核查三点：① 数据规模、来源与潜在泄漏；"
                    "② 基线选择、算力与调参预算是否公平；③ 结论的适用范围与失效条件是否被夸大。",
        "relevance": f"与 {topic} 方向相关；对语音/音频、大模型推理或多模态研究的具体影响需精读后判断。",
        "further": "待精读补充相关论文与代码链接。",
        "_mode": "fallback",
    }


def normalize_summary(data: dict, paper: dict) -> dict:
    base = fallback_summary(paper)
    out = {}
    for key in NOTE_KEYS:
        value = pr.clean_text(data.get(key) or "")
        out[key] = value if len(value) >= 4 else base[key]
    out["_mode"] = "llm"
    return out


def digest_short(summary: dict, key: str, limit: int) -> str:
    text = pr.clean_text(summary.get(key) or "")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "；", "，", "."):
        idx = cut.rfind(sep)
        if idx >= limit * 0.6:
            return cut[:idx + 1]
    return cut


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def read_text(path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path, text: str) -> None:
    pr.atomic_write(path, text)


def fill(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="为当日论文雷达草稿生成中文摘要")
    parser.add_argument("--date", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--fallback-model", default="")
    parser.add_argument("--max-fulltext-chars", type=int, default=0)
    parser.add_argument("--no-fulltext", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    date = pr.parse_date(args.date)
    profile, sources = pr.load_config()
    summary_cfg = profile.get("summary") or {}
    model = args.model or summary_cfg.get("model") or "openai/gpt-4.1-mini"
    fallback_model = args.fallback_model or summary_cfg.get("fallback_model") or "openai/gpt-4o-mini"
    max_fulltext = args.max_fulltext_chars or int(summary_cfg.get("max_fulltext_chars", 9000))
    max_tokens = int(summary_cfg.get("max_output_tokens", 1800))
    temperature = float(summary_cfg.get("temperature", 0.2))
    endpoint, timeout = load_llm_config()

    state_file = pr.state_path(date)
    state = pr.read_json(state_file)
    if not state:
        pr.die(f"{date} 没有草稿，请先运行 prepare", 2)
    if state.get("status") in ("final", "empty"):
        payload = {"status": "already-final", "date": date.isoformat()}
        print(json.dumps(payload, ensure_ascii=False)) if args.json else pr.log(f"{date} 已完成，跳过摘要")
        return 0

    token = get_token()
    if not token:
        pr.die("未找到 GITHUB_TOKEN / GH_TOKEN（GitHub Models 需要 token）", 2)

    selected = state.get("selected") or []
    if not selected:
        pr.die("草稿中没有论文", 2)

    results: dict[str, dict] = {}
    failed: list[str] = []
    models = [model] + ([fallback_model] if fallback_model and fallback_model != model else [])

    for idx, paper in enumerate(selected, 1):
        cid = paper["id"]
        pr.log(f"[{idx}/{len(selected)}] 生成摘要：{paper['title'][:70]}")
        fulltext = ""
        if not args.no_fulltext:
            try:
                text, source = pr.fetch_fulltext(cid)
                fulltext = text[:max_fulltext]
                pr.log(f"    正文节选 {len(fulltext)} 字符（{source}）")
            except Exception as exc:  # noqa: BLE001
                pr.log(f"    WARN: 正文不可用，仅用摘要（{str(exc)[:120]}）")
        comment = paper.get("comment") or ""
        user = USER_TEMPLATE.format(
            title=paper.get("title", ""),
            arxiv=cid,
            authors="、".join(paper.get("authors") or []) or "未知",
            published=paper.get("published", ""),
            topic=paper.get("topic", ""),
            venue=paper.get("venue", "") or "预印本",
            comment=comment or "无",
            abstract=paper.get("abstract") or "（缺失）",
            fulltext=fulltext or "（无正文节选，仅根据摘要作答，并明确标注信息不足）",
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user}]
        try:
            content, model_used = call_model(endpoint, token, models, messages,
                                             max_tokens=max_tokens, temperature=temperature,
                                             timeout=timeout)
            data = parse_summary_json(content)
            summary = normalize_summary(data, paper)
            summary["_model"] = model_used
            pr.log(f"    OK（{model_used}）")
        except Exception as exc:  # noqa: BLE001
            pr.log(f"    WARN: 摘要生成失败，使用兜底速览（{str(exc)[:160]}）")
            summary = fallback_summary(paper)
            failed.append(cid)
        results[cid] = summary

    # 今日看点
    overview = ""
    items = "\n".join(f"{i}. {p['title']} —— {digest_short(results[p['id']], 'tldr', 100)}"
                      for i, p in enumerate(selected, 1))
    try:
        content, _ = call_model(endpoint, token, models,
                                [{"role": "system", "content": SYSTEM_PROMPT},
                                 {"role": "user", "content": OVERVIEW_PROMPT.format(items=items)}],
                                max_tokens=500, temperature=0.3, timeout=timeout)
        overview = pr.clean_text(parse_summary_json(content).get("overview") or content)
        if len(overview) < 40:
            overview = pr.clean_text(content)
    except Exception as exc:  # noqa: BLE001
        pr.log(f"WARN: 今日看点生成失败，使用确定性兜底（{str(exc)[:120]}）")
    if len(overview) < 40:
        topics = "、".join(sorted({p.get("topic") or "其他" for p in selected}))
        overview = (f"今日入选 {len(selected)} 篇，覆盖 {topics}。"
                    f"速读模式：以下摘要由模型基于摘要与正文节选自动生成，建议优先点开精读笔记核对关键实验。")

    # 回填 note 与 digest
    for idx, paper in enumerate(selected, 1):
        summary = results[paper["id"]]
        note = pr.ROOT / paper["note_path"]
        if note.exists():
            text = read_text(note)
            mapping = {NOTE_PLACEHOLDERS[key]: summary[key] for key in NOTE_KEYS}
            write_text(note, fill(text, mapping))
        digest = pr.digest_path(date)
        text = read_text(digest)
        text = fill(text, {
            f"TLDR_{idx}": digest_short(summary, "tldr", 120),
            f"WHY_{idx}": digest_short(summary, "why_read", 140),
        })
        if "{{OVERVIEW}}" in text:
            text = text.replace("{{OVERVIEW}}", overview)
        write_text(digest, text)

    mode = "llm" if not failed else "fallback"
    state["summary_mode"] = mode
    state["llm"] = {"model": model, "fallback_model": fallback_model,
                    "ok": len(selected) - len(failed), "failed": failed,
                    "at": pr.iso_now()}
    state["updated_at"] = pr.iso_now()
    pr.write_json(state_file, state)

    payload = {"status": "ok", "date": date.isoformat(), "mode": mode,
               "papers": len(selected), "llm_ok": len(selected) - len(failed),
               "failed": failed, "model": model}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        pr.log(f"摘要完成：{len(selected) - len(failed)}/{len(selected)} 篇由 LLM 生成（{model}），"
               f"失败 {len(failed)} 篇已兜底，模式={mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
