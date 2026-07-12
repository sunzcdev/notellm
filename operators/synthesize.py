"""Bailian (百炼) TTS: synthesize text to speech.

Supports two model families:
  - qwen3-tts-flash (default): Multimodal generation API
  - cosyvoice-v3-flash: SpeechSynthesizer API (supports 龙安欢 etc.)

Dialogue mode: text in `**Speaker**: content` format is parsed into turns,
synthesized in parallel (one API call per turn with the speaker's voice),
then concatenated into one WAV file.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

# ── Bailian workspace config (shared with transcribe) ──────────────
_DEFAULT_WORKSPACE_ID = "ws-yc0quah0ehum3g2m"
_DEFAULT_API_KEY = (
    "sk-ws-H.EMXMXEY.BERC.MEUCIQCrx8UP2RFmGGWsnwBeKfiZSBg45wc4okOu8ynXY0iVqg"
    "IgFCe60NhARe_JQwD2_w8Vyh6Lyfp2SRKhihcUyPrZ6uQ"
)

# ── Qwen3-TTS-Flash voices (non-realtime) ──────────────────────────
_QWEN_VOICES: dict[str, str] = {
    "Cherry": "女性 沉稳大气 知识分享/新闻播报",
    "Serena": "女性 温柔小姐姐 中文普通话",
    "Ethan": "男性 阳光温暖 标准普通话",
    "Chelsie": "女性 二次元虚拟女友",
    "Momo": "女性 撒娇搞怪",
    "Vivian": "女性 拽拽可爱小暴躁",
    "Moon": "男性 率性帅气",
    "Maia": "女性 知性温柔",
    "Kai": "男性 耳朵SPA",
    "Nofish": "男性 不会翘舌音的设计师",
    "Bella": "女性 萌宝萝莉",
    "Jennifer": "女性 品牌级美语女声",
    "Ryan": "男性 节奏拉满戏感炸裂",
    "Katerina": "女性 御姐音色",
    "Aiden": "男性 美语大男孩",
    # Dialect voices
    "Jada": "女性 上海话 阿珍",
    "Dylan": "男性 北京话 晓东",
    "Li": "男性 南京话 老李",
    "Marcus": "男性 陕西话 秦川",
    "Roy": "男性 闽南语 阿杰",
    "Peter": "男性 天津话 李彼得",
    "Sunny": "女性 四川话 晴儿",
    "Eric": "男性 四川话 程川",
    "Rocky": "男性 粤语 阿强",
    "Kiki": "女性 粤语 阿清",
}

# ── CosyVoice V3 voices ─────────────────────────────────────────────
_COSY_VOICES: dict[str, str] = {
    "longanyang": "男声 阳光开朗",
    "longtian": "男声 成熟稳重",
    "longguang": "男声 磁性低沉",
    "longxing": "男声 温柔亲切",
    "longxin": "女声 温柔甜美",
    "longhua": "女声 知性温婉",
    "longyuan": "女声 元气活泼",
    "longjuan": "女声 沉稳大气",
    "longanyue": "男声 欢脱粤语",
    "longanhuan_v3": "女声 欢脱元气 支持方言 20-30岁",
    "longanwen_v3": "女声 优雅知性 25-35岁",
    "longyingtao_v3": "女声 温柔淡定 25-30岁",
    "longanli_v3": "女声 利落从容 25-35岁",
    "longwanjun_v3": "女声 细腻柔声 20-30岁",
    "longyichen_v3": "男声 洒脱活力 20-30岁",
    "longyingxiao_v3": "女声 清甜推销 20-25岁",
    "longyingxun_v3": "男声 年轻青涩 20-25岁",
    "longxiaochun_v3": "女声 知性积极 25-30岁",
    "longyunqing_v3": "女声 温暖阳光",
}


TOOL_SCHEMA = {
    "name": "synthesize",
    "description": "Synthesize text to speech using Bailian TTS. "
                   "Default model: qwen3-tts-flash. Also supports cosyvoice-v3-flash "
                   "(for 龙安欢 etc.). Returns local WAV file path.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to synthesize. qwen3-tts-flash: ~600 chars."
            },
            "voice": {
                "type": "string",
                "description": "Voice name. Default: 'Cherry'. "
                               "Chinese: Cherry/Serena for qwen3-tts-flash. "
                               "CosyVoice: longanyang/longanhuan_v3 etc. "
                               "Use list_voices=true to see all."
            },
            "language_type": {
                "type": "string",
                "description": "Language hint: Auto(default), Chinese, English, Japanese..."
            },
            "model": {
                "type": "string",
                "description": "Model: 'qwen3-tts-flash'(default) or 'cosyvoice-v3-flash'"
            },
            "list_voices": {
                "type": "boolean",
                "description": "Set true to list all available voices"
            },
        },
        "required": ["text"],
    },
}


def _config() -> tuple[str, str]:
    wid = os.environ.get("BAILIAN_WORKSPACE_ID", _DEFAULT_WORKSPACE_ID)
    key = os.environ.get("BAILIAN_API_KEY", _DEFAULT_API_KEY)
    return wid, key


def _list_voices() -> dict:
    return {
        "note": "qwen3-tts-flash voices (use voice='Cherry', model='qwen3-tts-flash')",
        "qwen3_tts_flash": _QWEN_VOICES,
        "cosyvoice_v3_flash": {
            "note": "CosyVoice voices (use voice='longanyang', model='cosyvoice-v3-flash')",
            "voices": _COSY_VOICES,
        },
        "recommended": {
            "Chinese_general": ("qwen3-tts-flash", "Cherry"),
            "Chinese_male_broadcast": ("cosyvoice-v3-flash", "longanyang"),
            "Chinese_female_cute": ("cosyvoice-v3-flash", "longanhuan_v3"),
            "English_female": ("qwen3-tts-flash", "Cherry"),
            "English_male": ("qwen3-tts-flash", "Ethan"),
            "Shanghai_dialect": ("qwen3-tts-flash", "Jada"),
            "Cantonese_female": ("qwen3-tts-flash", "Kiki"),
        },
    }


def _download_audio(url: str, dest: str) -> str:
    try:
        urllib.request.urlretrieve(url, dest)
        if os.path.getsize(dest) > 0:
            return dest
        raise RuntimeError("Downloaded file is empty")
    except Exception as e:
        raise RuntimeError(f"Failed to download audio: {e}")


# ── Dialogue pattern ─────────────────────────────────────────────────
_DIALOGUE_PATTERN = re.compile(r'\*\*(.+?)\*\*:\s*(.*?)(?=\n\*\*|\Z)', re.DOTALL)


def _parse_dialogue_turns(text: str) -> list[tuple[str, str]]:
    """Parse dialogue text into (speaker, content) turns.

    Format: **SpeakerName**: spoken content here...
    Returns list of (speaker_name, text_content).
    """
    turns = []
    for m in _DIALOGUE_PATTERN.finditer(text):
        speaker = m.group(1).strip()
        content = m.group(2).strip()
        if content:
            turns.append((speaker, content))
    return turns


def _synthesize_turn(speaker: str, content: str, model: str,
                     wid: str, api_key: str, out_path: str) -> str:
    """Synthesize one dialogue turn. The speaker name IS the voice name.

    Returns the local WAV path on success, or raises RuntimeError.
    """
    if model == "cosyvoice-v3-flash":
        # CosyVoice voices use different naming; try direct mapping
        voice = speaker.lower()
        result = _cosyvoice_tts_raw(content, voice, wid, api_key)
    else:
        result = _qwen_tts_raw(content, speaker, "Auto", wid, api_key, model)

    if "error" in result:
        raise RuntimeError(result["error"])

    audio_url = result["audio_url"]
    _download_audio(audio_url, out_path)
    return out_path


def _synthesize_dialogue(turns: list[tuple[str, str]], model: str,
                         wid: str, api_key: str, output_dir: str,
                         max_workers: int = 3) -> list[str]:
    """Synthesize dialogue turns in parallel. Returns sorted WAV paths."""
    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {}
        for i, (speaker, content) in enumerate(turns):
            turn_path = os.path.join(output_dir, f"turn_{i:04d}.wav")
            fut = pool.submit(_synthesize_turn, speaker, content,
                              model, wid, api_key, turn_path)
            fut_map[fut] = i

        for f in as_completed(fut_map):
            idx = fut_map[f]
            try:
                results[idx] = f.result()
            except Exception as e:
                results[idx] = f"__ERROR_{e}__"

    # Verify no errors
    errors = [p for p in results.values() if p.startswith("__ERROR_")]
    if errors:
        raise RuntimeError(f"{len(errors)}/{len(turns)} turns failed: {errors[0]}")

    return [results[i] for i in sorted(results)]


def _concat_wav(input_paths: list[str], output_path: str) -> str:
    """Concatenate WAV files with ffmpeg. Returns output_path."""
    # Write concat file list
    list_path = os.path.join(os.path.dirname(output_path), "_concat_list.txt")
    with open(list_path, "w") as f:
        for p in input_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120, check=True,
        )
        return output_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg concat failed: {e.stderr.strip()[:300]}")
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


async def run(args: dict, ctx: Context) -> dict:
    text = args.get("text", "")
    voice = args.get("voice", "Cherry")
    language_type = args.get("language_type", "auto").capitalize()
    model = args.get("model", "qwen3-tts-flash")
    list_voices = args.get("list_voices", False)

    if list_voices:
        return _list_voices()

    if not text or not text.strip():
        return {"error": "text is required"}

    wid, api_key = _config()

    # Detect dialogue format → parallel per-turn synthesis
    turns = _parse_dialogue_turns(text)
    if len(turns) >= 2:
        work_dir = tempfile.mkdtemp(prefix="tts-dialogue-")
        try:
            wavs = _synthesize_dialogue(turns, model, wid, api_key, work_dir, max_workers=3)
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_dir = ctx.config.podcast_output_dir
            os.makedirs(out_dir, exist_ok=True)
            combined = os.path.join(out_dir, f"dialogue_{ts}.wav")
            _concat_wav(wavs, combined)
            return {
                "file_path": combined,
                "file_size": os.path.getsize(combined),
                "turns": len(turns),
                "model": model,
                "voices": list(dict.fromkeys(s for s, _ in turns)),
            }
        except RuntimeError as e:
            return {"error": str(e)}
        finally:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

    # Single voice path (existing behavior)
    if len(text) > 600 and model == "qwen3-tts-flash":
        return {"error": f"Text too long ({len(text)} chars). qwen3-tts-flash max: ~600 chars. Use dialogue format for long texts."}

    if model == "cosyvoice-v3-flash":
        return _cosyvoice_tts(text, voice, wid, api_key, ctx)
    else:
        return _qwen_tts(text, voice, language_type, wid, api_key, model, ctx)


def _call_tts_api(endpoint: str, body: dict, api_key: str, timeout: int = 120) -> dict:
    """POST JSON to Bailian TTS endpoint, return parsed response or error dict."""
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"Bailian TTS HTTP {e.code}: {body_text}"}
    except urllib.error.URLError as e:
        return {"error": f"Bailian TTS connection failed: {e.reason}"}


def _qwen_tts_raw(text: str, voice: str, language_type: str,
                  wid: str, api_key: str, model: str) -> dict:
    """Call qwen3-tts-flash, return dict with 'audio_url' or 'error'."""
    endpoint = (
        f"https://{wid}.cn-beijing.maas.aliyuncs.com"
        f"/api/v1/services/aigc/multimodal-generation/generation"
    )
    body = {
        "model": model,
        "input": {
            "text": text,
            "voice": voice,
            "language_type": language_type if language_type != "Auto" else "Auto",
        },
    }
    result = _call_tts_api(endpoint, body, api_key)

    if "output" in result and "audio" in result["output"] and "url" in result["output"]["audio"]:
        url = result["output"]["audio"]["url"]
        return {"audio_url": url, "audio_id": result["output"]["audio"].get("id", "")}
    if result.get("status_code", 0) not in (0, 200):
        msg = result.get("message", "unknown error")
        return {"error": f"Bailian TTS failed: [{result.get('code', '')}] {msg}"}
    if "error" in result or "code" in result:
        return {"error": f"Bailian TTS error: {result}"}
    try:
        url = result["output"]["audio"]["url"]
        return {"audio_url": url, "audio_id": result["output"]["audio"].get("id", "")}
    except (KeyError, TypeError):
        return {"error": f"Unexpected TTS response: {json.dumps(result, ensure_ascii=False)[:300]}"}


def _cosyvoice_tts_raw(text: str, voice: str, wid: str, api_key: str) -> dict:
    """Call cosyvoice-v3-flash, return dict with 'audio_url' or 'error'."""
    endpoint = (
        f"https://{wid}.cn-beijing.maas.aliyuncs.com"
        f"/api/v1/services/audio/tts/SpeechSynthesizer"
    )
    body = {
        "model": "cosyvoice-v3-flash",
        "input": {"text": text, "voice": voice, "format": "wav", "sample_rate": 24000},
    }
    result = _call_tts_api(endpoint, body, api_key, timeout=300)

    if result.get("status_code", 0) not in (0, 200):
        msg = result.get("message", "unknown error")
        return {"error": f"CosyVoice TTS failed ({result.get('status_code', 0)}): {msg}"}
    try:
        return {"audio_url": result["output"]["audio"]["url"]}
    except (KeyError, TypeError):
        return {"error": f"Unexpected CosyVoice response: {json.dumps(result, ensure_ascii=False)[:300]}"}


def _qwen_tts(text: str, voice: str, language_type: str,
              wid: str, api_key: str, model: str, ctx: Context) -> dict:
    """Call qwen3-tts-flash and download the resulting audio."""
    result = _qwen_tts_raw(text, voice, language_type, wid, api_key, model)
    if "error" in result:
        return result

    audio_url = result["audio_url"]
    audio_id = result.get("audio_id", "")

    out_path = _save_audio(audio_url, ctx)
    if "error" in out_path:
        return out_path

    return {
        "file_path": out_path["path"],
        "file_size": out_path["size"],
        "characters": len(text),
        "audio_id": audio_id,
        "voice": voice,
        "model": model,
        "text_preview": text[:80] + ("..." if len(text) > 80 else ""),
    }


def _cosyvoice_tts(text: str, voice: str, wid: str,
                   api_key: str, ctx: Context) -> dict:
    """Call cosyvoice-v3-flash and download the resulting audio."""
    result = _cosyvoice_tts_raw(text, voice, wid, api_key)
    if "error" in result:
        return result

    out_path = _save_audio(result["audio_url"], ctx)
    if "error" in out_path:
        return out_path

    return {
        "file_path": out_path["path"],
        "file_size": out_path["size"],
        "voice": voice,
        "model": "cosyvoice-v3-flash",
        "text_preview": text[:80] + ("..." if len(text) > 80 else ""),
    }


def _save_audio(url: str, ctx: Context, filename: str = None) -> dict:
    """Download audio URL to local file. Returns {path, size} or {error}."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = ctx.config.podcast_output_dir  # 使用专用播客输出目录
    os.makedirs(out_dir, exist_ok=True)

    if not filename:
        filename = f"tts_{ts}.wav"
    if not filename.endswith('.wav'):
        filename = f"{filename}.wav"

    out_path = os.path.join(out_dir, filename)

    try:
        _download_audio(url, out_path)
    except RuntimeError as e:
        return {"error": str(e)}

    return {"path": out_path, "size": os.path.getsize(out_path)}
