#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 GitHub REST API 发布本地提交（git push 无法连接 github.com 时的兜底）。

背景：部分网络环境到 github.com:22/443 的连接会被重置，但 api.github.com 可用。
本脚本把「本地领先 origin/main 的改动」打包成一个 API 提交并更新远端分支，
不依赖 git 协议，适合自动化流水线在推送失败后兜底。

用法：
    python scripts/publish_api.py [--message "提交信息"] [--branch main] [--dry-run] [--json]

认证：环境变量 GITHUB_TOKEN / GH_TOKEN，或本机已登录的 gh CLI。
限制：单次最多 300 个文件、50MB；只处理新增/修改/删除（重命名按删除+新增处理）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
MAX_FILES = 300
MAX_BYTES = 50 * 1024 * 1024


def log(msg: str = "") -> None:
    print(f"[publish-api] {msg}", file=sys.stderr, flush=True)


def run_git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.decode('utf-8', 'replace')[:500]}")
    return proc.stdout.decode("utf-8", "replace")


def run_git_bytes(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.decode('utf-8', 'replace')[:500]}")
    return proc.stdout


def get_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("未找到 GITHUB_TOKEN / GH_TOKEN，且 gh CLI 未登录") from exc


def api_via_gh(method: str, path: str, body: dict | None) -> dict:
    """优先用 gh CLI 走 GitHub API（认证与 TLS 由 gh 处理，避免本机 Python 证书问题）。"""
    cmd = ["gh", "api", "--method", method, path]
    payload = None
    if body is not None:
        cmd += ["--input", "-"]
        payload = json.dumps(body, ensure_ascii=False)
    proc = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                          encoding="utf-8", timeout=180, cwd=ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {method} {path} 失败：{(proc.stderr or '').strip()[:600]}")
    raw = (proc.stdout or "").strip()
    return json.loads(raw) if raw else {}


def _urlopen(req: urllib.request.Request, timeout: int):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        text = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
            log("WARN: TLS 证书校验失败，本次改用不校验证书的连接重试")
            return urllib.request.urlopen(req, timeout=timeout,
                                          context=ssl._create_unverified_context())
        raise


def api_via_urllib(method: str, path: str, token: str, body: dict | None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "paper-radar-publish/1.0",
    })
    last = ""
    for attempt in range(3):
        try:
            with _urlopen(req, timeout=90) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:800]
            except Exception:  # noqa: BLE001
                pass
            last = f"HTTP {exc.code} {detail}"
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"API {method} {path} -> {last}") from exc
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"API {method} {path} -> {last}")


def api_request(method: str, path: str, token: str, body: dict | None = None):
    """GitHub API 请求：有 gh CLI 走 gh（首选），否则用 urllib；两者互为兜底。"""
    errors = []
    if shutil.which("gh"):
        try:
            return api_via_gh(method, path, body)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gh: {exc}")
    if token:
        try:
            return api_via_urllib(method, path, token, body)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"urllib: {exc}")
    raise RuntimeError("; ".join(errors) or "没有可用的 GitHub API 通道（缺少 gh 与 token）")


def parse_repo() -> tuple[str, str]:
    url = run_git("remote", "get-url", "origin").strip()
    match = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", url)
    if not match:
        raise RuntimeError(f"无法从 origin 解析 owner/repo：{url}")
    return match.group(1), match.group(2)


def remote_head_sha(owner: str, repo: str, branch: str, token: str) -> str | None:
    try:
        data = api_request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
        return (data.get("object") or {}).get("sha")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def file_mode(path: str) -> str:
    try:
        out = run_git("ls-tree", "HEAD", "--", path).strip()
        if out:
            return out.split()[0]
    except Exception:  # noqa: BLE001
        pass
    return "100644"


def local_tree_entries() -> dict[str, tuple[str, str]]:
    """本地 HEAD 的 (path -> (mode, blob_sha))。"""
    out = run_git("ls-tree", "-r", "HEAD")
    entries: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3:
            entries[path] = (parts[0], parts[2])
    return entries


def remote_tree_entries(owner: str, repo: str, tree_sha: str, token: str) -> dict[str, tuple[str, str]]:
    """远端 tree（recursive）的 (path -> (mode, blob_sha))；blob sha 与本地 git sha 可比较。"""
    data = api_request("GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1", token)
    entries: dict[str, tuple[str, str]] = {}
    for item in data.get("tree") or []:
        if item.get("type") == "blob" and item.get("path") and item.get("sha"):
            entries[item["path"]] = (item.get("mode", "100644"), item["sha"])
    return entries


def diff_entries(local: dict[str, tuple[str, str]],
                 remote: dict[str, tuple[str, str]]) -> list[tuple[str, str, str]]:
    changed: list[tuple[str, str, str]] = []
    for path, (mode, sha) in local.items():
        if path not in remote:
            changed.append(("A", path, mode))
        elif remote[path][0] != mode or remote[path][1] != sha:
            changed.append(("M", path, mode))
    for path in remote:
        if path not in local:
            changed.append(("D", path, "100644"))
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="通过 GitHub API 发布本地提交")
    parser.add_argument("--message", default="", help="提交信息（默认取本地 HEAD 的提交信息）")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    owner, repo = parse_repo()
    token = get_token()
    local_sha = run_git("rev-parse", "HEAD").strip()
    message = (args.message or run_git("log", "-1", "--pretty=%B").strip() or "update")[:2000]

    remote_sha = remote_head_sha(owner, repo, args.branch, token)
    if remote_sha == local_sha:
        payload = {"status": "up-to-date", "repo": f"{owner}/{repo}", "sha": local_sha}
        print(json.dumps(payload, ensure_ascii=False) if args.json else "已是最新，无需发布")
        return 0

    if remote_sha:
        commit_info = api_request("GET", f"/repos/{owner}/{repo}/git/commits/{remote_sha}", token)
        parent_tree = commit_info["tree"]["sha"]
        remote_entries = remote_tree_entries(owner, repo, parent_tree, token)
        # 若能判断远端是否为本地祖先，给出更明确的错误提示（本地对象缺失时跳过）
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", remote_sha, "HEAD"],
                                  cwd=ROOT, capture_output=True)
        if ancestor.returncode == 1:
            raise RuntimeError(
                f"远端 {args.branch} 有本地不含的提交（{remote_sha[:10]}），拒绝通过 API 覆盖；"
                f"请先 git fetch 合并后再发布"
            )
    else:
        parent_tree = None
        remote_entries = {}
    changed = diff_entries(local_tree_entries(), remote_entries)

    if not changed:
        payload = {"status": "no-diff", "repo": f"{owner}/{repo}", "local": local_sha, "remote": remote_sha}
        print(json.dumps(payload, ensure_ascii=False) if args.json else "没有可发布的差异")
        return 0
    if len(changed) > MAX_FILES:
        raise RuntimeError(f"改动文件过多（{len(changed)} > {MAX_FILES}），请改用 relay 或其他发布方式")

    plan = []
    total_bytes = 0
    for status, path, mode in changed:
        size = 0
        if status != "D":
            size = len(run_git_bytes("show", f"HEAD:{path}"))
            total_bytes += size
        plan.append({"status": status, "path": path, "mode": mode, "bytes": size})
    if total_bytes > MAX_BYTES:
        raise RuntimeError(f"改动体积过大（{total_bytes / 1e6:.1f}MB > {MAX_BYTES / 1e6:.0f}MB），请改用 relay")

    # 构造 API tree entries
    tree_entries = []
    if not args.dry_run:
        for item in plan:
            if item["status"] == "D":
                tree_entries.append({"path": item["path"], "mode": "100644",
                                     "type": "blob", "sha": None})
                continue
            content = run_git_bytes("show", f"HEAD:{item['path']}")
            blob = api_request("POST", f"/repos/{owner}/{repo}/git/blobs", token, {
                "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
            tree_entries.append({"path": item["path"], "mode": item["mode"],
                                 "type": "blob", "sha": blob["sha"]})
            log(f"  blob {item['path']} ({len(content)} bytes)")

    if args.dry_run:
        print(json.dumps({"status": "dry-run", "repo": f"{owner}/{repo}", "local": local_sha,
                          "remote": remote_sha, "message": message,
                          "files": plan, "total_bytes": total_bytes}, ensure_ascii=False, indent=2))
        return 0

    tree_body: dict = {"tree": tree_entries}
    if parent_tree:
        tree_body["base_tree"] = parent_tree
    tree = api_request("POST", f"/repos/{owner}/{repo}/git/trees", token, tree_body)

    commit_body: dict = {"message": message, "tree": tree["sha"]}
    if remote_sha:
        commit_body["parents"] = [remote_sha]
    commit = api_request("POST", f"/repos/{owner}/{repo}/git/commits", token, commit_body)

    if remote_sha:
        api_request("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{args.branch}", token,
                    {"sha": commit["sha"], "force": False})
    else:
        api_request("POST", f"/repos/{owner}/{repo}/git/refs", token,
                    {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})

    # 同步本地 remote-tracking 引用，避免 git status 误判
    try:
        run_git("update-ref", f"refs/remotes/origin/{args.branch}", commit["sha"])
    except RuntimeError:
        pass

    payload = {"status": "published", "repo": f"{owner}/{repo}", "branch": args.branch,
               "commit": commit["sha"], "files": len(plan), "bytes": total_bytes,
               "message": message.splitlines()[0] if message else ""}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        log(f"已通过 GitHub API 发布到 {owner}/{repo}@{args.branch}：{commit['sha'][:10]}，"
            f"{len(plan)} 个文件，{total_bytes / 1024:.1f}KB")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: {exc}")
        raise SystemExit(2)
