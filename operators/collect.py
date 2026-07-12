from __future__ import annotations

import atexit
import concurrent.futures
import glob
import os
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

from notellm.client import OnbError
from notellm.hooks import log

if TYPE_CHECKING:
    from notellm.ir import Context

_AR_DIR = os.path.expanduser("~/projects/agent-reach")
_AR_VENV = os.path.join(_AR_DIR, ".venv", "bin", "python3")
_PIPELINE_SCRIPT = os.path.expanduser("~/.hermes/scripts/douyin-pipeline.py")

_INVALID_PATH_CHARS = re.compile(r'[^-_.a-zA-Z0-9一-鿿/\s]')
_created_temp_files: list[str] = []


def _cleanup_temp_files():
    count = 0
    for fp in list(_created_temp_files):
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                count += 1
        except OSError:
            pass
    _created_temp_files.clear()
    if count:
        log("DEBUG", f"Cleaned {count} temp file(s)")


atexit.register(_cleanup_temp_files)

TOOL_SCHEMA = {
    "name": "collect",
    "description": "Collection pipeline: search topic across 8 channels: douyin, bilibili, youtube, reddit, twitter, xiaohongshu, exa_search (web). Use channel='exa_search' for web search, 'all' for all (default).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Topic/search keyword"},
            "notebook_id": {"type": "string", "description": "Optional existing notebook ID"},
            "channel": {"type": "string", "description": "Source filter: 'douyin', 'bilibili', 'youtube', 'reddit', 'twitter', 'xiaohongshu', 'exa_search', or 'all' (default all)"},
            "sec_uid": {"type": "string", "description": "Douyin sec_uid for deep collect (download + transcribe)"},
            "share_link": {"type": "string", "description": "Douyin share link (auto-extracts user sec_uid)"},
            "video_link": {"type": "string", "description": "Single-content direct link for any platform: Bilibili (BV号/b23.tv/bilibili.com), YouTube (youtube.com/youtu.be), Douyin (v.douyin.com/iesdouyin.com), Xiaohongshu (xiaohongshu.com/explore/xxx)"},
        },
        "required": [],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    topic = args.get("topic", "")
    notebook_id = args.get("notebook_id", "")
    sec_uid = args.get("sec_uid", "")
    share_link = args.get("share_link", "")
    video_link = args.get("video_link", "")
    channel = args.get("channel", "")

    slug_base = topic or video_link or "untitled"
    slug = re.sub(r'[^a-zA-Z0-9一-鿿_-]', '_', slug_base)[:40]
    msgs: list[str] = []
    warns: list[str] = []
    imported = 0
    nb_id = notebook_id

    _cleanup_stale_collection_dirs(ctx)

    if not nb_id:
        nbs = ctx.client.get("/api/notebooks")
        if isinstance(nbs, list):
            for nb in nbs:
                if topic.lower() in nb.get("name", "").lower():
                    nb_id = nb["id"]
                    msgs.append(f"Using notebook: {nb['name']}")
                    break
        if not nb_id:
            from . import create_notebook
            r = await create_notebook.run({"name": topic}, ctx)
            nb_id = r["id"]
            msgs.append(f"Created notebook: {topic}")

    # ── 1. Douyin: share_link → extract sec_uid ──
    if share_link and (not channel or channel in ("douyin", "all")):
        raw = _run_ar_tool("douyin", ["video", share_link], timeout=30)
        if raw:
            su = ""
            for line in raw.split("\n"):
                if "sec_uid" in line:
                    m = re.search(r'`([^`]+)`', line)
                    if m:
                        su = m.group(1)
                        break
            if su:
                msgs.append("Extracted sec_uid from link")
                sec_uid = su
            elif raw and "未找到" not in raw:
                content = _ensure_frontmatter(raw, f"{topic or share_link} - douyin", "douyin-video")
                fp = _save_temp_file(slug, "douyin-video", 1, content, ctx)
                from . import add_source
                r2 = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
                if "error" not in r2:
                    imported += 1
                else:
                    warns.append(f"Douyin import failed: {r2.get('error')}")
        else:
            warns.append("Share link extraction returned no data")

    # ── 2. video_link: single-content direct link for any supported platform ──
    _video_platforms = ("bilibili", "youtube", "douyin", "xiaohongshu")
    if video_link and (not channel or channel in _video_platforms + ("all",)):
        vlink = video_link.strip()
        # Detect bilibili: BV号, bilibili.com, b23.tv
        if vlink.startswith("BV") or "bilibili.com" in vlink or "b23.tv" in vlink:
            if channel in ("bilibili", "all", ""):
                raw = _run_ar_tool("bilibili", ["video", vlink], timeout=30)
                if raw and "❌" not in raw:
                    content = _ensure_frontmatter(raw, vlink, "bilibili-video")
                    # No subtitle API for Bilibili — directly ASR
                    log("INFO", f"No subtitle API for Bilibili, transcribing {vlink}")
                    asr_raw = _run_ar_tool("bilibili", ["transcribe", vlink], timeout=600)
                    if asr_raw and "❌" not in asr_raw and "转录失败" not in asr_raw:
                        asr_body = asr_raw
                        if asr_body.startswith("---"):
                            parts = asr_body.split("---", 2)
                            if len(parts) >= 3:
                                asr_body = parts[2].strip()
                        if asr_body:
                            content += f"\n\n## 视频全文\n\n{asr_body}\n"
                    else:
                        content += "\n\n> ⚠️ ASR 转写未成功\n"
                    fp = _save_temp_file(slug, "bilibili-video", 1, content, ctx)
                    from . import add_source
                    r2 = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
                    if "error" not in r2:
                        imported += 1
                        msgs.append(f"Bilibili video: {r2.get('title', '?')}")
                    else:
                        warns.append(f"Bilibili import failed: {r2.get('error')}")
                else:
                    warns.append(f"Bilibili video not found: {vlink}")
        # Detect YouTube: youtube.com, youtu.be
        elif "youtube.com" in vlink or "youtu.be" in vlink:
            if channel in ("youtube", "all", ""):
                raw = _run_ar_tool("youtube", ["video", vlink], timeout=45)
                if raw and "❌" not in raw:
                    # Step 1: get metadata
                    content = _ensure_frontmatter(raw, vlink, "youtube-video")
                    # Step 2: try subtitle first (fast path), fallback to ASR
                    transcript_text = ""
                    for sub_lang in ("zh", "zh-Hans", "zh-Hant", "en"):
                        sub_raw = _run_ar_tool("youtube", ["subtitle", vlink, "--lang", sub_lang], timeout=60)
                        if sub_raw and "❌" not in sub_raw and "未找到字幕" not in sub_raw:
                            # Strip subtitle frontmatter, keep body
                            sub_body = sub_raw
                            if sub_body.startswith("---"):
                                parts = sub_body.split("---", 2)
                                if len(parts) >= 3:
                                    sub_body = parts[2].strip()
                            transcript_text = sub_body
                            break
                    if not transcript_text:
                        # Step 3: no subtitle → ASR via transcribe
                        log("INFO", f"No subtitles for {vlink}, falling back to ASR transcription")
                        asr_raw = _run_ar_tool("youtube", ["transcribe", vlink], timeout=300)
                        if asr_raw and "❌" not in asr_raw and "转录失败" not in asr_raw:
                            asr_body = asr_raw
                            if asr_body.startswith("---"):
                                parts = asr_body.split("---", 2)
                                if len(parts) >= 3:
                                    asr_body = parts[2].strip()
                            if asr_body:
                                transcript_text = asr_body
                    if transcript_text:
                        content += f"\n\n## 视频全文\n\n{transcript_text}\n"
                    else:
                        content += "\n\n> ⚠️ 该视频无可用字幕，ASR 转写也未成功\n"
                    fp = _save_temp_file(slug, "youtube-video", 1, content, ctx)
                    from . import add_source
                    r2 = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
                    if "error" not in r2:
                        imported += 1
                        msgs.append(f"YouTube video: {r2.get('title', '?')}")
                    else:
                        warns.append(f"YouTube import failed: {r2.get('error')}")
                else:
                    warns.append(f"YouTube video not found: {vlink}")
        # Detect Douyin: douyin.com, v.douyin.com, iesdouyin.com
        elif "douyin.com" in vlink or "iesdouyin.com" in vlink:
            if channel in ("douyin", "all", ""):
                raw = _run_ar_tool("douyin", ["video", vlink], timeout=30)
                if raw and "❌" not in raw:
                    content = _ensure_frontmatter(raw, vlink, "douyin-video")
                    # No subtitle API for Douyin — directly ASR
                    log("INFO", f"No subtitle API for Douyin, transcribing {vlink}")
                    asr_raw = _run_ar_tool("douyin", ["transcribe", vlink], timeout=600)
                    if asr_raw and "❌" not in asr_raw and "转录失败" not in asr_raw:
                        asr_body = asr_raw
                        if asr_body.startswith("---"):
                            parts = asr_body.split("---", 2)
                            if len(parts) >= 3:
                                asr_body = parts[2].strip()
                        if asr_body:
                            content += f"\n\n## 视频全文\n\n{asr_body}\n"
                    else:
                        content += "\n\n> ⚠️ ASR 转写未成功\n"
                    fp = _save_temp_file(slug, "douyin-video", 1, content, ctx)
                    from . import add_source
                    r2 = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
                    if "error" not in r2:
                        imported += 1
                        msgs.append(f"Douyin video: {r2.get('title', '?')}")
                    else:
                        warns.append(f"Douyin import failed: {r2.get('error')}")
                else:
                    warns.append(f"Douyin video not found: {vlink}")
        # Detect Xiaohongshu: xiaohongshu.com, xhslink.com
        elif "xiaohongshu.com" in vlink or "xhslink.com" in vlink or "s.xiaohongshu.com" in vlink:
            if channel in ("xiaohongshu", "all", ""):
                raw = _run_ar_tool("xiaohongshu", ["note", vlink], timeout=30)
                if raw and "❌" not in raw:
                    content = _ensure_frontmatter(raw, vlink, "xiaohongshu-note")
                    fp = _save_temp_file(slug, "xiaohongshu-note", 1, content, ctx)
                    from . import add_source
                    r2 = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
                    if "error" not in r2:
                        imported += 1
                        msgs.append(f"Xiaohongshu note: {r2.get('title', '?')}")
                    else:
                        warns.append(f"Xiaohongshu import failed: {r2.get('error')}")
                else:
                    warns.append(f"Xiaohongshu note not found: {vlink}")
        else:
            warns.append(f"Unrecognized video_link format: {vlink}")

    # ── 3. Douyin deep pipeline (download + transcribe) ──
    if sec_uid and (not channel or channel in ("douyin", "all")):
        result = await _deep_collect(sec_uid, slug, topic, nb_id, ctx)
        return result

    # ── 4. Shallow collect: multi-channel search (PARALLEL) ──
    channels_list = ["douyin", "bilibili", "youtube", "reddit", "twitter", "xiaohongshu", "exa_search"]
    if channel and channel != "all":
        channels_list = [channel]

    def _search_channel(ch: str) -> tuple[int, str, str]:
        ch_timeout = 45 if ch in ("youtube",) else 30 if ch in ("twitter",) else 25
        raw = _run_ar_tool(ch, ["search", topic, "--limit", "5"], timeout=ch_timeout)
        if not raw or "未找到" in raw or "未认证" in raw:
            return (0, "", f"{ch} search: no results")
        labels = {
            "douyin": ("douyin", f"{topic} - douyin"),
            "bilibili": ("bilibili", f"{topic} - bilibili"),
            "youtube": ("youtube", f"{topic} - youtube"),
            "reddit": ("reddit", f"{topic} - reddit"),
            "twitter": ("twitter", f"{topic} - twitter"),
            "xiaohongshu": ("xiaohongshu", f"{topic} - 小红书"),
            "exa_search": ("exa_search", f"{topic} - 全网搜索"),
        }
        ch_label, title = labels.get(ch, (ch, topic))
        content = _ensure_frontmatter(raw, title, ch_label)
        fp = _save_temp_file(slug, ch, 1, content, ctx)
        try:
            from . import add_source
            import asyncio
            loop = asyncio.new_event_loop()
            r = loop.run_until_complete(add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx))
            loop.close()
            if "error" not in r:
                return (1, f"{ch_label}: {r.get('title', '?')}", "")
            return (0, "", f"{ch_label} import failed: {r['error']}")
        except OnbError as e:
            return (0, "", f"{ch_label} import error: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        fut = {executor.submit(_search_channel, ch): ch for ch in channels_list}
        for f in concurrent.futures.as_completed(fut, timeout=90):
            ch = fut[f]
            try:
                n, msg, warn = f.result()
                imported += n
                if msg:
                    msgs.append(msg)
                if warn:
                    warns.append(warn)
            except Exception as e:
                if "timed out" in str(e).lower():
                    warns.append(f"{ch} timed out")
                else:
                    warns.append(f"{ch} error: {e}")

    s = f"Imported {imported} source(s) into notebook"
    if warns:
        s += f" ({len(warns)} issue(s))"
    return {
        "notebook_id": nb_id, "notebook_name": topic,
        "sources_imported": imported, "details": msgs, "warnings": warns,
        "summary": s,
    }


async def _deep_collect(sec_uid: str, slug: str, topic: str, nb_id: str, ctx: Context) -> dict:
    msgs: list[str] = []
    warns: list[str] = []
    imported = 0

    out_dir = os.path.join(ctx.config.collection_dir, f"deep-{slug}")
    os.makedirs(out_dir, exist_ok=True)

    if os.path.isfile(_PIPELINE_SCRIPT):
        try:
            r = subprocess.run(
                [sys.executable, _PIPELINE_SCRIPT, sec_uid, "3", out_dir],
                capture_output=True, text=True, timeout=600,
                env={**os.environ, "NO_PROXY": "*", "PYTHONUNBUFFERED": "1"},
            )
            log("DEBUG", f"Pipeline stdout tail: {r.stdout[-300:]}")
            if r.stderr:
                log("DEBUG", f"Pipeline stderr tail: {r.stderr[-300:]}")
            if r.returncode != 0:
                warns.append(f"Pipeline exited with code {r.returncode}")
        except subprocess.TimeoutExpired:
            warns.append("Pipeline timed out (10 min)")
        except Exception as e:
            warns.append(f"Pipeline error: {e}")
    else:
        warns.append(f"Pipeline script not found at {_PIPELINE_SCRIPT}")

    from . import add_source
    for fp in sorted(glob.glob(os.path.join(out_dir, "*.md"))):
        try:
            r = await add_source.run({"notebook_id": nb_id, "file_path": fp}, ctx)
            if "error" not in r:
                imported += 1
                msgs.append(f"Imported: {r.get('title', os.path.basename(fp))}")
            else:
                warns.append(f"Import failed for {os.path.basename(fp)}: {r['error']}")
        except OnbError as e:
            warns.append(f"Import error for {os.path.basename(fp)}: {e}")

    _register_temp(out_dir)

    summary = f"Deep collected {imported} video(s) into notebook"
    if warns:
        summary += f" ({len(warns)} issue(s))"
    return {
        "notebook_id": nb_id, "notebook_name": topic,
        "sources_imported": imported, "details": msgs, "warnings": warns,
        "summary": summary,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _run_ar_tool(module: str, args: list[str], timeout: int = 60) -> str:
    if not os.path.isfile(_AR_VENV):
        return ""
    env = {**os.environ}
    if module == "douyin":
        env["NO_PROXY"] = "*"
    try:
        r = subprocess.run(
            [_AR_VENV, "-m", f"agent_reach.tools.{module}"] + args,
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return r.stdout or ""
    except (subprocess.TimeoutExpired, OSError) as e:
        log("WARN", f"AR tool {module} failed", error=str(e))
        return ""


def _ensure_frontmatter(content: str, title: str, channel: str) -> str:
    if content.strip().startswith("---"):
        return content
    title_esc = title.replace('"', "'")
    return f"---\ntitle: \"{title_esc}\"\nchannel: {channel}\n---\n\n{content}"


def _safe_path(prefix: str, name: str) -> str:
    safe = _INVALID_PATH_CHARS.sub("_", name)[:80]
    return os.path.join(prefix, f"{safe}.md")


def _save_temp_file(slug: str, ch: str, seq: int, content: str, ctx: Context) -> str:
    os.makedirs(ctx.config.collection_dir, exist_ok=True)
    fp = _safe_path(ctx.config.collection_dir, f"{slug}-{ch}-{seq:03d}")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    return _register_temp(fp)


def _register_temp(fp: str) -> str:
    _created_temp_files.append(fp)
    return fp


def _cleanup_stale_collection_dirs(ctx: Context) -> None:
    cdir = ctx.config.collection_dir
    if not os.path.isdir(cdir):
        return
    import time
    now = time.time()
    for entry in os.listdir(cdir):
        dp = os.path.join(cdir, entry)
        if os.path.isdir(dp) and (now - os.path.getmtime(dp)) > ctx.config.max_collection_age:
            try:
                shutil.rmtree(dp)
                log("INFO", f"Cleaned stale collection dir: {dp}")
            except OSError as e:
                log("WARN", f"Failed to clean {dp}: {e}")
