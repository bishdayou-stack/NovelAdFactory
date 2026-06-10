import sys
import os
import json
import base64
import shutil
import random
import time
import textwrap
import traceback
import threading
import asyncio
import requests
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field, model_validator
from PIL import Image, ImageDraw, ImageFont

try:
    from moviepy.editor import ImageSequenceClip
except ImportError:
    from moviepy.video.io.ImageSequenceClip import ImageSequenceClip

import subprocess
import imageio_ffmpeg

app = FastAPI()

# 数据看板模块
import database
import scraper
import analytics
import meta_api
import delivery
from fastapi import Query, Request

# 启动时初始化数据库
database.init_db()

@app.on_event("startup")
def _recover_incomplete_batches():
    """启动时扫描：有 _progress.json 但无 _meta.json 的批次标记为 interrupted"""
    if not OUTPUT_ROOT.exists():
        return
    for d in OUTPUT_ROOT.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        progress_file = d / "_progress.json"
        meta_file = d / "_meta.json"
        if progress_file.exists() and not meta_file.exists():
            try:
                prog = json.loads(progress_file.read_text(encoding="utf-8"))
                if prog.get("status") == "running":
                    prog["status"] = "interrupted"
                    prog["step"] = "服务重启，任务中断"
                    progress_file.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
                    (d / "_meta.json").write_text(json.dumps({
                        "batch_id": d.name,
                        "status": "interrupted",
                        "message": "服务重启导致任务中断",
                        "images": [],
                        "videos": [],
                        "progress": prog,
                    }, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

# 启动定时同步（默认3分钟，用户可调）
from apscheduler.schedulers.background import BackgroundScheduler
_scheduler = BackgroundScheduler()

def _get_sync_seconds():
    try:
        return database.get_sync_interval()
    except Exception:
        return 180

_scheduler.add_job(
    lambda: scraper.run_full_sync(),
    'interval',
    seconds=_get_sync_seconds(),
    id='auto_sync',
    max_instances=1,
)
_scheduler.start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_PATH = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_PATH / "config.json"

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}

def _save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[CONFIG] 保存配置失败: {e}")

# 启动时加载全局配置
_app_config = _load_config()

STATIC_PATH = BASE_PATH / "static"
PROMPTS_PATH = BASE_PATH / "prompts"
_OUTPUT_ROOT_DEFAULT = str(BASE_PATH / "output") if sys.platform != "win32" else r"D:\每日小说"
OUTPUT_ROOT = Path(os.getenv("NOVEL_OUTPUT_ROOT", _OUTPUT_ROOT_DEFAULT)).expanduser().resolve()
FONT_PATH = str(BASE_PATH / "ziti" / "corbelb.ttf")
MUSIC_PATH = BASE_PATH / "音乐"

os.makedirs(OUTPUT_ROOT, exist_ok=True)

# --- 加载爆款模板索引 ---
TEMPLATES_INDEX_PATH = BASE_PATH / "templates_index.json"
TEMPLATES_INDEX: List[dict] = []
if TEMPLATES_INDEX_PATH.exists():
    try:
        TEMPLATES_INDEX = json.loads(TEMPLATES_INDEX_PATH.read_text(encoding="utf-8"))
        print(f"[TEMPLATES] 已加载 {len(TEMPLATES_INDEX)} 条模板描述")
    except Exception as e:
        print(f"[TEMPLATES] 加载失败: {e}")


# ---- 模板内容匹配：小说关键词 vs 模板风格 ----
_GENRE_KEYWORDS = {
    "dark romance": ["dark", "shadow", "forbidden", "danger", "obsessed", "possessive", "cruel",
                     "heartless", "darkness", "pain", "suffer", "vengeance", "betray", "hate",
                     "enemy", "ruthless", "dominant", "submissive", "dangerous"],
    "sweet romance": ["sweet", "love", "tender", "soft", "gentle", "warm", "heart", "cherish",
                      "kiss", "hug", "romantic", "care", "gentle", "kind", "sweet", "innocent"],
    "billionaire romance": ["billionaire", "rich", "wealth", "luxury", "money", "power",
                            "empire", "ceo", "boss", "mansion", "penthouse", "wealthy",
                            "millionaire", "fortune", "business"],
    "werewolf romance": ["werewolf", "wolf", "alpha", "mate", "pack", "howl", "moon",
                          "claw", "fang", "shifter", "beast", "lunar", "transform"],
    "vampire romance": ["vampire", "blood", "immortal", "undead", "bite", "coffin",
                         "castle", "eternal", "fangs", "nocturnal", "drain", "vampire"],
    "thriller suspense": ["thriller", "suspense", "mystery", "murder", "kill", "death",
                           "crime", "detective", "danger", "threat", "secret", "investigate",
                           "evidence", "stalk", "chase"],
    "fantasy magic": ["magic", "magical", "spell", "wizard", "witch", "dragon", "sword",
                       "kingdom", "queen", "prince", "enchant", "sorcery", "prophecy"],
    "historical regency": ["regency", "victorian", "medieval", "castle", "lord", "lady",
                            "duke", "countess", "era", "period", "century", "throne", "noble"],
    "urban drama": ["city", "street", "urban", "ghetto", "gang", "struggle", "survive",
                     "concrete", "hood", "project", "crime lord", "mafia", "cartel"],
    "contemporary drama": ["love", "relationship", "modern", "college", "office",
                            "apartment", "friend", "dating", "couple", "breakup"],
}

# ---- 钩子类型分类 —— 6大爆款钩子的关键词检测 ----
_HOOK_KEYWORDS = {
    "power_reversal": [
        "kneel", "kneeling", "kneels", "beg", "begging", "begged", "mercy",
        "crown", "throne", "shattered", "humiliation", "humiliate", "humiliated",
        "public disgrace", "standing over", "heel on", "smirk", "defiant",
        "power stripped", "triumph", "victorious", "look down", "looking down",
        "beneath", "underfoot", "stepped on", "crushed", "revenge", "payback",
        "finally won", "turned the tables", "bowed", "bowing", "grovel",
    ],
    "secret_exposure": [
        "secret", "secrets", "exposed", "expose", "exposure", "revelation",
        "revealed", "truth", "lied", "lying", "liar", "deceived", "deception",
        "betray", "betrayal", "betrayed", "phone screen", "text message",
        "evidence", "proof", "caught", "witnessed", "overheard", "letter",
        "envelope", "projector", "screenshot", "recording", "video footage",
        "wedding interrupted", "interrupted", "gasp", "gasps", "stunned",
        "confronted", "confrontation", "exposed in front of", "everyone saw",
    ],
    "forbidden_intimacy": [
        "forbidden", "taboo", "dangerous desire", "pinned", "pinning",
        "against the wall", "necklace snapped", "grabbed", "grabbing",
        "pulled by", "wrist", "waist", "trapped", "hovering", "almost",
        "inches apart", "breath", "lips", "kiss", "tension", "chemistry",
        "electric", "silk", "buttons undone", "crimson lipstick", "smeared",
        "possessive", "possessiveness", "jealous", "jealousy", "claim",
        "marked", "belongs to", "mine", "taken", "owned",
    ],
    "impossible_choice": [
        "choice", "choose", "chose", "between", "two options", "gun on table",
        "trigger", "door closing", "two doors", "deadline", "ultimatum",
        "either", "sacrifice", "trade", "decide", "decision", "hesitating",
        "hesitates", "finger hovering", "button", "detonator", "document",
        "contract", "sign", "signing", "papers", "divorce papers",
        "custody", "life or death", "seconds left", "ticking clock",
    ],
    "identity_transformation": [
        "transformation", "transformed", "makeover", "reveal", "revealed",
        "stunning", "shocking transformation", "new look", "unrecognizable",
        "stepping out", "helicopter", "limousine", "luxury car", "entourage",
        "designer gown", "couture", "red carpet", "cameras flashing",
        "paparazzi", "entrance", "grand entrance", "walked in", "arrived",
        "appearance", "stares", "frozen in disbelief", "who is that",
        "can't believe", "jaw dropped", "stunned silence", "heir", "heiress",
    ],
    "innocence_vs_corruption": [
        "innocent", "innocence", "child", "baby", "pure", "purity",
        "corrupt", "corrupted", "stained", "darkness", "sin", "sins",
        "warm light", "cold shadow", "contrast", "good", "evil",
        "protected", "protect", "protecting", "shielding", "guard",
        "danger", "threatened", "threat", "menace", "looming",
        "broken", "shattered", "ruined", "destroy", "destroyed",
    ],
}


def _classify_novel_hooks(novel_text: str) -> List[str]:
    """检测小说中最强烈的钩子类型（返回前2个）"""
    text_lower = novel_text.lower()
    scores = {}
    for hook_name, keywords in _HOOK_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > 0:
            scores[hook_name] = count
    # 按匹配数降序排列，返回前2个
    ranked = sorted(scores.keys(), key=lambda k: -scores[k])
    return ranked[:2]


def _classify_template_hooks(template: dict) -> List[str]:
    """判断模板最适合的钩子类型（返回最多2个）"""
    text = (template.get("style", "") + " " + template.get("mood", "") + " " +
            template.get("key_elements", "") + " " + template.get("description", "")).lower()
    scores = {}
    for hook_name, keywords in _HOOK_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > 0:
            scores[hook_name] = count
    ranked = sorted(scores.keys(), key=lambda k: -scores[k])
    return ranked[:2]


def _score_template_for_novel(template: dict, novel_lower: str, novel_hooks: List[str] | None = None) -> float:
    """计算模板与小说内容的匹配度（含视觉冲击力 + 钩子类型匹配）"""
    style = (template.get("style", "") + " " + template.get("mood", "") + " " +
             template.get("key_elements", "")).lower()

    score = 0.0

    # 1. 流派关键词匹配（高权重）
    for genre, keywords in _GENRE_KEYWORDS.items():
        n_in_novel = sum(1 for kw in keywords if kw in novel_lower)
        n_in_template = sum(1 for kw in keywords if kw in style)
        if n_in_novel > 0 and n_in_template > 0:
            score += n_in_novel * 2.0 * min(1.0, n_in_template / 3.0)

    # 2. 通用词汇重叠（低权重）
    novel_words = set(w for w in novel_lower.split() if len(w) > 3)
    style_words = set(w for w in style.split() if len(w) > 3)
    overlap = len(novel_words & style_words)
    score += overlap * 0.5

    # 3. 情感/氛围匹配
    mood_keywords = {
        "intense": ["intense", "passionate", "fierce", "urgent", "dramatic"],
        "tender": ["tender", "gentle", "sweet", "warm", "soft"],
        "mysterious": ["mystery", "secret", "hidden", "shadow"],
        "melancholic": ["sad", "tear", "cry", "sorrow", "grief", "loss", "lonely"],
        "hopeful": ["hope", "dream", "believe", "future"],
        "dangerous": ["danger", "threat", "deadly", "fatal"],
        "joyful": ["joy", "happy", "laugh", "smile", "delight"],
    }
    for mood, keywords in mood_keywords.items():
        if any(kw in novel_lower for kw in keywords):
            if mood in style:
                score += 1.0

    # 4. 视觉冲击力评分（viral_factor）— 加权 25%
    _VIRAL_KEYWORDS = [
        "intense", "dramatic", "striking", "explosive", "cinematic", "extreme",
        "contrast", "dynamic", "bold", "dark", "moody", "sensual", "dangerous",
        "forbidden", "powerful", "stunning", "arresting", "haunting", "urgent",
        "visceral", "gritty", "sharp", "stark", "glowing", "ethereal", "shocking",
        "rapturous", "fierce", "brutal", "primal", "vivid", "deep", "electric",
    ]
    viral_count = sum(1 for kw in _VIRAL_KEYWORDS if kw in style)
    viral_score = viral_count * 1.5

    # 5. 钩子类型匹配（最高权重 — 40%）
    hook_score = 0.0
    if novel_hooks:
        template_hooks = _classify_template_hooks(template)
        for nh in novel_hooks:
            if nh in template_hooks:
                hook_score += 4.0  # 每个匹配上的钩子类型 +4.0
    # 如果模板的钩子就是小说的主钩子，额外奖励
    if novel_hooks and template_hooks and novel_hooks[0] == template_hooks[0]:
        hook_score += 3.0

    # 综合评分：内容匹配 35% + 视觉冲击力 25% + 钩子匹配 40%
    result = score * 0.35 + viral_score * 0.25 + hook_score * 0.40
    return result


def _pick_templates_for_novel(
    novel_content: str,
    square_count: int, scroll_count: int, n: int = 1
) -> str:
    """精选 2 个匹配构图原型 + 1 个高分模板，紧凑格式"""
    if not TEMPLATES_INDEX and not _ARCHETYPES:
        return ""

    novel_lower = (novel_content or "").lower()
    novel_hooks = _classify_novel_hooks(novel_lower)
    lines = []

    # 第1层：2 个匹配原型（紧凑单行）
    if _ARCHETYPES:
        seen = set()
        matched_archs = []
        for hook in novel_hooks:
            if hook in _ARCHETYPES:
                for a in _ARCHETYPES[hook]:
                    if a["title"] not in seen:
                        seen.add(a["title"])
                        matched_archs.append(a)
        # 不足2个时补其他类型
        if len(matched_archs) < 2:
            for hook_name, arch_list in _ARCHETYPES.items():
                for a in arch_list:
                    if a["title"] not in seen:
                        seen.add(a["title"])
                        matched_archs.append(a)
                        if len(matched_archs) >= 2:
                            break
                if len(matched_archs) >= 2:
                    break
        matched_archs = matched_archs[:2]

        if matched_archs:
            lines.append("Visual DNA Blueprints (visual grammar — apply to novel):")
            for i, arch in enumerate(matched_archs):
                title = arch["title"].split("] ", 1)[-1] if "] " in arch["title"] else arch["title"]
                p = arch["params"]
                parts = [f"cam:{p.get('camera','')}", f"lit:{p.get('light','')}", f"col:{p.get('color','')}",
                         f"cmp:{p.get('composition','')}", f"nrg:{p.get('energy','')}"]
                lines.append(f"  A{i+1}[{title}] {' | '.join(parts)}")

    # 第2层：1 个模板（最高分）
    if TEMPLATES_INDEX:
        square_templates = [t for t in TEMPLATES_INDEX if t.get("ratio") == "1:1"]
        portrait_templates = [t for t in TEMPLATES_INDEX if t.get("ratio") == "9:16"]
        best = None
        if len(novel_lower) > 20:
            candidates = square_templates + portrait_templates
            if candidates:
                scored = [(t, _score_template_for_novel(t, novel_lower, novel_hooks)) for t in candidates]
                scored.sort(key=lambda x: -x[1])
                best = scored[0][0] if scored else None
        elif square_templates:
            best = random.Random().choice(square_templates)

        if best:
            st = best.get("style", "") or ""
            md = best.get("mood", "") or ""
            lines.append(f"\nTemplate ref (mood only): {st} | mood:{md}")

    if not lines:
        return ""

    lines.append("FUSE: archetypes=visual skeleton, novel=flesh, template=mood only. Never copy scenes.")
    return "\n".join(lines)


def _pick_random_music() -> Optional[Path]:
    """从音乐文件夹随机选择一首"""
    if not MUSIC_PATH.exists():
        return None
    files = [f for f in os.listdir(str(MUSIC_PATH)) if f.endswith(".mp4")]
    return MUSIC_PATH / random.choice(files) if files else None


def _merge_music_to_video(video_path: Path) -> None:
    """用 ffmpeg 将随机背景音乐合成到视频中（音量 30%，循环匹配视频时长）"""
    music_file = _pick_random_music()
    if not music_file:
        return
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    tmp_path = video_path.with_suffix(".tmp.mp4")
    cmd = [
        ffmpeg,
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(music_file),
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-af", "volume=0.3",
        "-y",
        str(tmp_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        tmp_path.replace(video_path)
        print(f"[MUSIC] 已合成音乐: {music_file.name} -> {video_path.name}")
    except subprocess.CalledProcessError as e:
        print(f"[MUSIC] ffmpeg 合成失败: {e.stderr.decode(errors='replace')[:500]}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# --- 从 prompts/ 目录加载提示词 ---
def _load_prompt(filename: str) -> str:
    path = PROMPTS_PATH / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _load_archetypes() -> Tuple[Dict[str, list], str]:
    """Parse composition_archetypes.txt into {hook_type: [archetype_dict, ...]} + footer text."""
    text = _load_prompt("composition_archetypes.txt")
    if not text:
        return {}, ""
    archetypes: Dict[str, list] = {}
    footer_lines = []
    current_hook = None
    current_title = ""
    current_params = {}
    in_footer = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Universal composition notes"):
            in_footer = True
            if current_hook and current_params:
                archetypes.setdefault(current_hook, []).append({"title": current_title, "params": current_params})
            current_hook = None
            current_params = {}
            footer_lines.append(line)
            continue
        if in_footer:
            footer_lines.append(line)
            continue
        if stripped.startswith("[") and "]" in stripped:
            if current_hook and current_params:
                archetypes.setdefault(current_hook, []).append({"title": current_title, "params": current_params})
            header = stripped
            hook_part = header[header.index("[") + 1:header.index("]")]
            parts = hook_part.split()
            current_hook = parts[-1] if parts else hook_part
            current_title = header
            current_params = {}
        elif stripped and current_hook:
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                current_params[key.strip().lower()] = val.strip()
    if current_hook and current_params:
        archetypes.setdefault(current_hook, []).append({"title": current_title, "params": current_params})
    footer = "\n".join(footer_lines) if footer_lines else ""
    return archetypes, footer


SYSTEM_PROMPT_TEMPLATE = _load_prompt("system_prompt.txt")

# 按需加载规则文件（根据图片类型组装，减少 token 浪费）
_RULES_CORE = _load_prompt("rules_core.txt")
_RULES_SHARED = _load_prompt("rules_shared_modules.txt")
_RULES_TEXT_SINGLE = _load_prompt("rules_text_single.txt")
_RULES_SCROLL = _load_prompt("rules_scroll.txt")
_RULES_LR_SPLIT = _load_prompt("rules_lr_split.txt")
_RULES_TB_SPLIT = _load_prompt("rules_tb_split.txt")
_RULES_VIDEO_SCRIPT = _load_prompt("rules_video_script.txt")

# B层：加载视觉基因蓝图（构图原型，纯视觉参数，不含具体场景）
_ARCHETYPES, _ARCHETYPES_FOOTER = _load_archetypes()

# 完整提示词文件（用户手动维护的单一规则文件，作为默认绘图规则）
_FULL_RULES_PATH = BASE_PATH / "最新提示词.txt"
_FULL_RULES = _FULL_RULES_PATH.read_text(encoding="utf-8").strip() if _FULL_RULES_PATH.exists() else ""

# 兼容旧代码：完整规则 = core，实际使用时按需组装
NOVEL_PROMPT_RULES = _RULES_CORE


def _build_rules_text(user_prompt: str, text_single: int, lr: int, tb: int, scroll: int) -> str:
    """返回绘图规则：用户自定义优先 → 完整提示词文件（最新提示词.txt）→ 按需组装"""
    if user_prompt and user_prompt.strip():
        return user_prompt.strip()
    if _FULL_RULES:
        return _FULL_RULES
    parts = [_RULES_CORE, _RULES_SHARED]
    if text_single > 0:
        parts.append(_RULES_TEXT_SINGLE)
    if scroll > 0:
        parts.append(_RULES_SCROLL)
    if lr > 0:
        parts.append(_RULES_LR_SPLIT)
    if tb > 0:
        parts.append(_RULES_TB_SPLIT)
    return "\n\n".join(p for p in parts if p)

# 后缀常量
_SUFFIX_CONFIG = {}
_suffix_path = PROMPTS_PATH / "suffix_prompts.txt"
if _suffix_path.exists():
    for line in _suffix_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        _SUFFIX_CONFIG[key.strip()] = val.strip()

_TEXT_SINGLE_SUFFIX = _SUFFIX_CONFIG.get("TEXT_SINGLE_SUFFIX", "1:1 square composition")
_SCROLL_VISUAL_SUFFIX = _SUFFIX_CONFIG.get("SCROLL_VISUAL_SUFFIX", "9:16 vertical portrait composition")
_LR_SPLIT_SUFFIX = _SUFFIX_CONFIG.get("LR_SPLIT_SUFFIX",
    "VERTICAL LEFT-RIGHT SPLIT SCREEN ONLY, "
    "single straight vertical divider line in center, "
    "left panel and right panel side by side, "
    "forbidden top-bottom layout")
_TB_SPLIT_SUFFIX = _SUFFIX_CONFIG.get("TB_SPLIT_SUFFIX",
    "HORIZONTAL TOP-BOTTOM SPLIT SCREEN ONLY, "
    "single straight horizontal divider line in center, "
    "top panel above bottom panel, "
    "forbidden left-right layout")

# --- 并发控制 / 进度跟踪 ---
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_BATCH_CANCEL_EVENTS: Dict[int, threading.Event] = {}
_BATCH_CANCEL_LOCK = threading.Lock()
_BATCH_PROGRESS: Dict[int, dict] = {}
_BATCH_PROGRESS_LOCK = threading.Lock()

# SSE 事件队列
import queue
_SSE_QUEUES: Dict[int, List[queue.Queue]] = {}
_SSE_LOCK = threading.Lock()
_MAX_SSE_QUEUE = 2000


def _set_batch_cancel(batch_id: int) -> None:
    with _BATCH_CANCEL_LOCK:
        event = _BATCH_CANCEL_EVENTS.get(batch_id)
        if event:
            event.set()


def _is_batch_cancelled(batch_id: int) -> bool:
    with _BATCH_CANCEL_LOCK:
        event = _BATCH_CANCEL_EVENTS.get(batch_id)
        return event.is_set() if event else False


def _register_batch(batch_id: int) -> None:
    with _BATCH_CANCEL_LOCK:
        _BATCH_CANCEL_EVENTS[batch_id] = threading.Event()


def _deregister_batch(batch_id: int) -> None:
    with _BATCH_CANCEL_LOCK:
        _BATCH_CANCEL_EVENTS.pop(batch_id, None)
    with _BATCH_PROGRESS_LOCK:
        _BATCH_PROGRESS.pop(batch_id, None)
    with _SSE_LOCK:
        _SSE_QUEUES.pop(batch_id, None)


def _update_progress(batch_id: int, percent: int, step: str, status: str = "running") -> None:
    now = datetime.now().isoformat()
    progress = {
        "batch_id": batch_id,
        "percent": percent,
        "step": step,
        "status": status,
        "updated_at": now,
    }
    with _BATCH_PROGRESS_LOCK:
        _BATCH_PROGRESS[batch_id] = progress
    try:
        batch_dir = OUTPUT_ROOT / str(batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "_progress.json").write_text(
            json.dumps(progress, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    # 推送 SSE 进度事件（异常不影响主流程）
    try:
        _push_sse_event(batch_id, {
            "type": "progress",
            "batch_id": batch_id,
            "percent": percent,
            "step": step,
            "status": status,
            "updated_at": now,
        })
    except Exception:
        pass


def _get_progress(batch_id: int) -> Optional[dict]:
    with _BATCH_PROGRESS_LOCK:
        p = _BATCH_PROGRESS.get(batch_id)
        if p:
            return dict(p)
    try:
        fp = OUTPUT_ROOT / str(batch_id) / "_progress.json"
        if fp.exists():
            return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _get_all_running_progress() -> Dict[int, dict]:
    with _BATCH_PROGRESS_LOCK:
        return {k: dict(v) for k, v in _BATCH_PROGRESS.items()}


def _push_sse_event(batch_id: int, data: dict) -> None:
    """将事件推送到所有等待的 SSE 连接（线程安全）"""
    with _SSE_LOCK:
        queues = list(_SSE_QUEUES.get(int(batch_id), []))
    for q in queues:
        try:
            q.put(data, timeout=0.5)
        except queue.Full:
            pass


def _push_image_ready(batch_id: int, image_url: str, label: str) -> None:
    """通知前端一张图片已生成完毕"""
    _push_sse_event(batch_id, {
        "type": "image_ready",
        "batch_id": batch_id,
        "image_url": image_url,
        "label": label,
    })


def allocate_batch_id(output_root: Path) -> int:
    output_root.mkdir(parents=True, exist_ok=True)
    counter_path = output_root / "_batch_counter.json"
    n = 1
    if counter_path.exists():
        try:
            data = json.loads(counter_path.read_text(encoding="utf-8"))
            n = max(1, int(data.get("next", 1)))
        except Exception:
            n = 1
    batch_id = n
    counter_path.write_text(
        json.dumps({"next": batch_id + 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    return batch_id


class GenerateRequest(BaseModel):
    api_key: str = ""
    api_url: str = ""
    chat_model_name: str = ""
    image_model_name: str = ""
    novel_content: str = ""
    novel_id: str = ""            # 小说ID（通过ID获取时传入）
    prompt: str = ""
    video_text: str = ""
    text_single_count: int = 0
    lr_split_count: int = 0
    tb_split_count: int = 0
    scroll_count: int = 0
    popup_count: int = 0
    ai_scroll_count: int = 0   # AI 滚屏（AI 生成文案）
    ai_popup_count: int = 0     # AI 弹屏（AI 生成文案）
    scroll_style: dict = {}
    popup_style: dict = {}
    use_templates: bool = False  # 默认不参考爆款模板
    text_single_text_enabled: bool = True   # 单帧图是否叠加文字
    lr_split_text_enabled: bool = True      # 左右分屏是否叠加文字
    tb_split_text_enabled: bool = True      # 上下分屏是否叠加文字
    concurrency: int = 2                     # 并发数，默认 2

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "text_single_count" not in d and "single_count" in d:
            d["text_single_count"] = d.get("single_count", 0)
        return d





def _dedup_prompt(p: str) -> str:
    """移除重复关键词：含完全匹配去重 + 子串合并（保留较长版本）"""
    parts = [x.strip() for x in p.split(",") if x.strip()]
    # 第一轮：精确去重（忽略大小写）
    seen = {}
    for x in parts:
        low = x.lower()
        if low not in seen:
            seen[low] = x
    unique = list(seen.values())
    # 第二轮：如果短词是长词的子串（忽略大小写），去掉短词
    result = []
    for i, a in enumerate(unique):
        a_low = a.lower()
        is_sub = False
        for j, b in enumerate(unique):
            if i != j and a_low in b.lower() and a_low != b.lower():
                is_sub = True
                break
        if not is_sub:
            result.append(a)
    return ", ".join(result)


def finalize_square_prompt(kind: str, core: str, base_fallback: str) -> str:
    base = (core or "").strip() or (base_fallback or "").strip()

    if kind == "text_single":
        result = f"{base}, {_TEXT_SINGLE_SUFFIX}"
    elif kind == "lr":
        result = f"{base}, {_LR_SPLIT_SUFFIX}"
    elif kind == "tb":
        result = f"{base}, {_TB_SPLIT_SUFFIX}"
    else:
        result = base

    return _dedup_prompt(result)


def finalize_scroll_visual_prompt(core: str, base_fallback: str) -> str:
    base = (core or "").strip() or (base_fallback or "").strip()
    return _dedup_prompt(f"{base}, {_SCROLL_VISUAL_SUFFIX}")


def _norm_prompt_list(x) -> List[str]:
    if not isinstance(x, list):
        return []
    return [str(i).strip() for i in x]


def _split_legacy_square_prompts(
    flat: List[str], text_single_count: int, lr_count: int, tb_count: int
) -> Tuple[List[str], List[str], List[str]]:
    need = text_single_count + lr_count + tb_count
    buf = [str(x).strip() for x in flat]
    while len(buf) < need:
        buf.append("")
    i = 0
    ts = buf[i : i + text_single_count]
    i += text_single_count
    lr = buf[i : i + lr_count]
    i += lr_count
    tb = buf[i : i + tb_count]
    return ts, lr, tb


def _api_error_snippet(r: requests.Response) -> str:
    try:
        j = r.json()
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or err)[:800]
            if err is not None:
                return str(err)[:800]
        return json.dumps(j, ensure_ascii=False)[:800]
    except Exception:
        return (r.text or "")[:800]




def _repair_json(raw: str) -> dict:
    """修复 AI 返回的不标准 JSON"""
    import re
    # 1. 移除尾部逗号（常见于数组/对象末尾）
    raw = re.sub(r',\s*(\n\s*[}\]])', r'\1', raw)
    # 2. 修复属性名的单引号
    raw = re.sub(r"'([^']*)'(?=\s*:)", r'"\1"', raw)
    # 3. 如果还不合法，用正则提取各字段值然后重建
    if not raw.strip().startswith("{"):
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 4. 最终手段：逐个提取已知 key 的值，重建合法 JSON
    keys = ["text_single_prompts", "lr_split_prompts", "tb_split_prompts",
            "scroll_visual_prompts", "scroll_prompts", "square_prompts"]
    result = {}
    for key in keys:
        # 匹配 "key": [ ... ] 或 "key": [ ... ] （最外层数组）
        pattern = r'"' + re.escape(key) + r'"\s*:\s*\[([\s\S]*?)\](?=\s*[,}])'
        m = re.search(pattern, raw)
        if m:
            array_raw = "[" + m.group(1) + "]"
            # 修复数组元素内的未转义内容
            try:
                val = json.loads(array_raw)
            except json.JSONDecodeError:
                # 元素太多导致格式问题，跳过这个 key
                val = []
            result[key] = val
        # 也尝试匹配 null/空
        if key not in result:
            result[key] = []
    if result:
        print(f"[CHAT API] JSON修复成功，提取到 {len(result)} 个字段")
        return result
    raise ValueError(f"无法修复 JSON，前500字符: {raw[:500]}")


def request_image_prompt_plan(
    api_url: str,
    api_key: str,
    chat_model_name: str,
    novel_content: str,
    user_prompt: str,
    text_single_count: int,
    lr_split_count: int,
    tb_split_count: int,
    scroll_visual_count: int,
    use_templates: bool = True,
) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = api_url.rstrip("/") + "/chat/completions"
    n_square = text_single_count + lr_split_count + tb_split_count
    system = SYSTEM_PROMPT_TEMPLATE.format(
        text_single_count=text_single_count,
        scroll_visual_count=scroll_visual_count,
        lr_split_count=lr_split_count,
        tb_split_count=tb_split_count,
        n_square=n_square,
    )
    # 用户消息 = 按需组装规则 + 小说内容 + （可选）模板参考
    rules_text = _build_rules_text(user_prompt, text_single_count, lr_split_count, tb_split_count, scroll_visual_count)
    novel_text = (novel_content or "").strip()
    total_images = text_single_count + lr_split_count + tb_split_count + scroll_visual_count
    user = (
        f"绘图规则：\n{rules_text}\n\n"
        f"小说节选：\n{novel_text}\n\n"
        f"数量：text_single={text_single_count}, lr={lr_split_count}, tb={tb_split_count}, scroll={scroll_visual_count}"
    )
    if total_images >= 6:
        user += "\n\n【重要】共{0}张图，每张必须对应小说中不同的爆款瞬间或不同的情绪切面。严禁重复同一场景。详见系统提示词 Step 3 变体策略。".format(total_images)
    if use_templates:
        template_ref = _pick_templates_for_novel(novel_text, n_square, scroll_visual_count)
        if template_ref:
            user += f"\n{template_ref}"
    payload = {
        "model": chat_model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.85,
        "max_tokens": 8192,
    }
    print(f"[CHAT API] 请求模型={chat_model_name}, 系统提示词长度={len(system)}, 用户消息长度={len(user)}")
    print(f"[CHAT API] 消息分解: rules={len(rules_text)}, novel={len(novel_text)}, templates={len(template_ref) if use_templates else 0}")
    r = requests.post(url, json=payload, headers=headers, timeout=300)
    print(f"[CHAT API] 响应状态={r.status_code}")
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {_api_error_snippet(r)}")
    j = r.json()
    raw = j["choices"][0]["message"]["content"].strip()
    print(f"[CHAT API] 原始响应({len(raw)}字符): {raw}")
    # 清理 markdown 包裹
    raw_clean = raw.strip()
    if raw_clean.startswith("```"):
        lines = raw_clean.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw_clean = "\n".join(lines).strip()
    # 尝试提取 JSON（有时 AI 会在 JSON 前后加说明文字）
    if not raw_clean.startswith("{"):
        import re
        m = re.search(r'\{[\s\S]*\}', raw_clean)
        if m:
            raw_clean = m.group(0)
    try:
        data = json.loads(raw_clean)
    except json.JSONDecodeError as je:
        print(f"[CHAT API] JSON解析失败(line {je.lineno} col {je.colno}): {je}")
        print(f"[CHAT API] 尝试解析的内容前500字符: {raw_clean[:500]}")
        # 容错修复：尝试用 json5 风格修复常见格式问题
        try:
            data = _repair_json(raw_clean)
            print(f"[CHAT API] JSON修复后解析成功, keys={list(data.keys())}")
        except Exception as je2:
            print(f"[CHAT API] JSON修复也失败: {je2}")
            raise je  # 抛出原始错误
    print(f"[CHAT API] 解析成功, keys={list(data.keys())}")

    def _extract(key, *aliases):
        for k in [key] + list(aliases):
            items = _norm_item_list(data.get(k))
            if items:
                return items
        return []

    scroll = _extract("scroll_visual_prompts", "scroll_prompts")
    ts = _extract("text_single_prompts", "single_square_prompts", "single_prompts")
    lr = _extract("lr_split_prompts")
    tb = _extract("tb_split_prompts")
    legacy = data.get("square_prompts")
    if legacy and not ts and not lr and not tb:
        ts_legacy, lr_legacy, tb_legacy = _split_legacy_square_prompts(
            _norm_prompt_list(legacy), text_single_count, lr_split_count, tb_split_count
        )
        ts = [{"image_prompt": s} for s in ts_legacy]
        lr = [{"image_prompt": s} for s in lr_legacy]
        tb = [{"image_prompt": s} for s in tb_legacy]

    # 统计有效 prompt（image_prompt 非空）
    valid_count = lambda items: sum(1 for it in items if isinstance(it, dict) and str(it.get("image_prompt", "")).strip())
    print(f"[CHAT API] 有效prompt数: text_single={valid_count(ts)}, lr={valid_count(lr)}, tb={valid_count(tb)}, scroll={valid_count(scroll)}")
    return ts, lr, tb, scroll


def request_image_prompt_plan_batched(
    api_url: str,
    api_key: str,
    chat_model_name: str,
    novel_content: str,
    user_prompt: str,
    text_single_count: int,
    lr_split_count: int,
    tb_split_count: int,
    scroll_visual_count: int,
    use_templates: bool = True,
    batch_size: int = 4,
) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    """分批调用 Chat API，每 batch_size 张图调用一次"""
    slots = (
        ["text_single"] * text_single_count
        + ["lr_split"] * lr_split_count
        + ["tb_split"] * tb_split_count
        + ["scroll_visual"] * scroll_visual_count
    )
    total = len(slots)
    if total == 0:
        return [], [], [], []
    num_batches = (total + batch_size - 1) // batch_size
    print(f"[CHAT API] 总计 {total} 张图，分 {num_batches} 批次调用（每批 ≤{batch_size} 张）")
    all_ts, all_lr, all_tb, all_sv = [], [], [], []
    batch_errors = []
    novel_text = (novel_content or "").strip()
    for bi in range(num_batches):
        batch_slots = slots[bi * batch_size : (bi + 1) * batch_size]
        ts_c = sum(1 for s in batch_slots if s == "text_single")
        lr_c = sum(1 for s in batch_slots if s == "lr_split")
        tb_c = sum(1 for s in batch_slots if s == "tb_split")
        sv_c = sum(1 for s in batch_slots if s == "scroll_visual")
        print(f"[CHAT API] 批次 {bi+1}/{num_batches}: text_single={ts_c}, lr={lr_c}, tb={tb_c}, scroll={sv_c}")
        try:
            ts_p, lr_p, tb_p, sv_p = request_image_prompt_plan(
                api_url, api_key, chat_model_name, novel_text, user_prompt,
                ts_c, lr_c, tb_c, sv_c,
                use_templates=use_templates,
            )
            all_ts.extend(ts_p)
            all_lr.extend(lr_p)
            all_tb.extend(tb_p)
            all_sv.extend(sv_p)
        except Exception as e:
            err_msg = f"批次{bi+1}/{num_batches}: {e}"
            batch_errors.append(err_msg)
            print(f"[CHAT API] {err_msg}")
            traceback.print_exc()
    if batch_errors:
        raise RuntimeError(f"Chat API 分批调用失败 ({len(batch_errors)}/{num_batches} 批次出错): {'; '.join(batch_errors)}")
    print(f"[CHAT API] 分批汇总: text_single={len(all_ts)}, lr={len(all_lr)}, tb={len(all_tb)}, scroll={len(all_sv)}")
    return all_ts, all_lr, all_tb, all_sv


# ====== AI 视频文案生成 ======

VIDEO_SCRIPT_SYSTEM_PROMPT = """\
You are a viral short-video copywriter for Facebook ads targeting American women aged 25-65. Your scripts drive massive engagement for billionaire romance, rebirth/revenge, second-chance love, and dark family saga vertical dramas.

Given a novel excerpt and the AI-generated image prompts that will become video backgrounds, write a high-impact English narration script (~500 words) for each image prompt.

Rules:
- ~500 English words per script
- Match the core conflict, character emotions, key props, and scene atmosphere of the corresponding image prompt
- Style: emotional, suspenseful, conversational yet literary — perfect for short-video voiceover or captions
- Tone: fits billionaires, rebirth counterattack, chasing wife to crematorium, wealthy family revenge genres
- Include internal monologue, conflict background, twist reveals where appropriate
- No compliance violations, no excessive gore

Output pure JSON array, no markdown, no explanation:
["script text 1", "script text 2", ...]

Each string is the full ~500 word narration for one video."""


def request_video_scripts(
    api_url: str,
    api_key: str,
    model: str,
    novel_content: str,
    image_prompts: List[str],
    count: int,
    video_type: str,
) -> List[str]:
    """调 LLM 批量生成 AI 视频文案，返回与 image_prompts 一一对应的脚本文本列表"""
    if not image_prompts or count <= 0:
        return []
    prompts_for_llm = image_prompts[:count]
    prompt_list = "\n".join(f"{i+1}. {p}" for i, p in enumerate(prompts_for_llm))
    user_msg = (
        f"Novel excerpt:\n{novel_content[:2000]}\n\n"
        f"Image prompts for {video_type} videos:\n{prompt_list}\n\n"
        f"Write {len(prompts_for_llm)} video script(s), each ~500 words, as a JSON array."
    )
    messages = [
        {"role": "system", "content": VIDEO_SCRIPT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    url = api_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.8, "max_tokens": 4000}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=180)
        if r.status_code >= 400:
            print(f"  [VIDEO SCRIPT] HTTP {r.status_code}: {r.text[:300]}")
            return []
        raw = r.json()["choices"][0]["message"]["content"].strip()
        # Extract JSON array
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            scripts = json.loads(raw[start:end + 1])
            if isinstance(scripts, list):
                return [str(s) for s in scripts]
    except Exception as e:
        print(f"  [VIDEO SCRIPT] 异常: {e}")
    return []


def pad_prompts(prompts: List[str], n: int, filler: str) -> List[str]:
    out: List[str] = []
    for i in range(n):
        if i < len(prompts) and str(prompts[i]).strip():
            out.append(str(prompts[i]).strip())
        else:
            out.append(filler)
    return out


def pad_items(items: List[dict], n: int, filler_image_prompt: str) -> List[dict]:
    out: List[dict] = []
    for i in range(n):
        if i < len(items) and isinstance(items[i], dict) and str(items[i].get("image_prompt", "")).strip():
            out.append(items[i])
        else:
            out.append({"image_prompt": filler_image_prompt})
    return out


def _norm_item_list(x) -> List[dict]:
    if not isinstance(x, list):
        return []
    result: List[dict] = []
    for item in x:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"image_prompt": item})
    return result


def composite_text_on_image(
    image_path: Path,
    text_bottom: str = "",
    text_left: str = "",
    text_right: str = "",
    text_overlay: str = "",
    font_path: str = FONT_PATH,
    max_font_size: int = 48,
) -> None:
    img = Image.open(str(image_path)).convert("RGBA")
    w, h = img.size

    all_texts = [t for t in [text_bottom, text_left, text_right, text_overlay] if t.strip()]
    if not all_texts:
        return

    def _make_font(size):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            return ImageFont.load_default()

    def _pick_font_size(txt, max_w, max_fs, min_fs=42):
        for fs in range(max_fs, min_fs - 1, -1):
            f = _make_font(fs)
            words = txt.split()
            longest_word = max(words, key=len) if words else txt
            bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), longest_word, font=f)
            if (bbox[2] - bbox[0]) <= max_w:
                return fs, f
        return min_fs, _make_font(min_fs)

    # 底部/中间文本可用宽度：左右各留4字符
    if text_overlay:
        main_max_w = int(w * 0.90)
    elif text_left or text_right:
        main_max_w = int(w * 0.90)
    else:
        main_max_w = int(w * 0.90)

    # 左右短语：半屏减去左右各2字符
    lr_max_w = int(w * 0.50 - 2 * 48 * 2)  # 半屏减左右margin

    main_text = text_bottom or text_overlay or ""
    if main_text:
        font_size, font = _pick_font_size(main_text, main_max_w, max_font_size)
    else:
        font_size, font = max_font_size, _make_font(max_font_size)

    lr_font_size, font_lr = font_size, font
    if text_left or text_right:
        lr_text = max([t for t in [text_left, text_right] if t.strip()], key=len, default="")
        if lr_text:
            lr_font_size, font_lr = _pick_font_size(lr_text, lr_max_w, max_font_size)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # margin = 2个字符宽度
    margin = int(font_size * 1.0)
    lr_margin = int(lr_font_size * 1.0)
    center = w // 2

    def _outlined(x, y, txt, fnt):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    ov_draw.text((x + dx, y + dy), txt, font=fnt, fill=(0, 0, 0, 255))
        ov_draw.text((x, y), txt, font=fnt, fill=(255, 255, 255, 255))

    def _wrap_lines(txt, max_w, fnt):
        words = txt.split()
        lines = []
        cur = ""
        for wd in words:
            test = wd if not cur else cur + " " + wd
            bbox = ov_draw.textbbox((0, 0), test, font=fnt)
            if (bbox[2] - bbox[0]) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines

    def _draw_lines(lines, y_start, max_w, fnt, zone_left, zone_right):
        """在 [zone_left, zone_right] 区间内居中绘制多行文字"""
        lh = fnt.size + 8
        zone_w = zone_right - zone_left
        for i, line in enumerate(lines):
            bbox = ov_draw.textbbox((0, 0), line, font=fnt)
            tw = bbox[2] - bbox[0]
            x = zone_left + (zone_w - tw) // 2
            _outlined(x, y_start + i * lh, line, fnt)

    def _draw_with_margin(txt, y_start, max_w, fnt, zone_left, zone_right):
        lines = _wrap_lines(txt, max_w, fnt)
        _draw_lines(lines, y_start, max_w, fnt, zone_left, zone_right)
        return len(lines) * (fnt.size + 8)

    # 底部文字栏边界：图片底部 15% 区域
    bar_y = int(h * 0.85)
    bar_center_y = bar_y + (h - bar_y) // 2

    # --- 单图底部 ---
    if text_bottom and not text_left and not text_right and not text_overlay:
        max_w = w - 2 * margin
        bt_lines = _wrap_lines(text_bottom, max_w, font)
        bt_total_h = len(bt_lines) * (font.size + 8)
        bt_y = bar_center_y - bt_total_h // 2
        _draw_lines(bt_lines, bt_y, max_w, font, margin, w - margin)

    # --- 左右分屏 ---
    if text_left or text_right:
        # 左右面板文字：位于水平分隔线上方
        lr_text_h = lr_font_size + 8
        lr_y = bar_y - lr_text_h - 50

        # 底部叙事文字：位于水平分隔线下方栏位内居中
        if text_bottom:
            bt_max_w = w - 2 * margin
            bt_lines = _wrap_lines(text_bottom, bt_max_w, font)
            bt_total_h = len(bt_lines) * (font.size + 8)
            bt_y = bar_center_y - bt_total_h // 2
            _draw_lines(bt_lines, bt_y, bt_max_w, font, margin, w - margin)

        if text_left:
            left_zone = (lr_margin, center - lr_margin)
            l_lines = _wrap_lines(text_left, left_zone[1] - left_zone[0], font_lr)
            _draw_lines(l_lines, lr_y, left_zone[1] - left_zone[0], font_lr, left_zone[0], left_zone[1])
        if text_right:
            right_zone = (center + lr_margin, w - lr_margin)
            r_lines = _wrap_lines(text_right, right_zone[1] - right_zone[0], font_lr)
            _draw_lines(r_lines, lr_y, right_zone[1] - right_zone[0], font_lr, right_zone[0], right_zone[1])

    # --- 上下分屏 ---
    if text_overlay:
        max_w = w - 2 * margin
        ov_lines = _wrap_lines(text_overlay, max_w, font)
        ov_total_h = len(ov_lines) * (font.size + 8)
        ov_y = h // 2 - ov_total_h // 2
        _draw_lines(ov_lines, ov_y, max_w, font, margin, w - margin)

    result = Image.alpha_composite(img, overlay)
    result = result.convert("RGB")
    result.save(str(image_path), "PNG")


def draw_text_with_spacing(draw, text, position, font, fill, char_spacing):
    x, y = position
    for char in text:
        # Black stroke (outline): draw char at 8 surrounding offset positions
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), char, font=font, fill=(0, 0, 0, 255))
        # White fill on top
        draw.text((x, y), char, font=font, fill=fill)
        char_w = draw.textlength(char, font=font)
        x += char_w + char_spacing


def wrap_text_precisely(draw_obj, text, font, max_width, char_spacing):
    paragraphs = text.split("\n")
    lines = []
    for p in paragraphs:
        if p.strip() == "":
            lines.append("")
            continue
        words = p.split(" ")
        current_line = ""
        for word in words:
            test_line = word if current_line == "" else current_line + " " + word
            w = draw_obj.textlength(test_line, font=font) + char_spacing * max(0, len(test_line) - 1)
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return lines


def pre_render_text(text, target_width, font_path, font_size, text_color, line_spacing, char_spacing,
                    align="居中对齐", bg_type="full_bar", bg_color="#000000", bg_opacity=150):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    test_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wrapped_lines = wrap_text_precisely(test_draw, text, font, target_width, char_spacing)
    line_h = font_size + line_spacing
    img_h = len(wrapped_lines) * line_h + 100
    text_canvas = Image.new("RGBA", (target_width, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_canvas)
    # Parse bg_color
    hx = bg_color.lstrip("#")
    if len(hx) >= 6:
        bg_rgb = tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4))
    else:
        bg_rgb = (0, 0, 0)
    current_y = 0
    for line in wrapped_lines:
        if line == "":
            current_y += line_h
            continue
        line_width = draw.textlength(line, font=font) + (char_spacing * max(0, len(line) - 1))
        x = (target_width - line_width) // 2 if align == "居中对齐" else 0
        # 贴字背景：每行文字画独立的圆角矩形，紧贴文字
        if bg_type == "hug_text":
            pad_x, pad_y = 12, 6
            draw.rounded_rectangle(
                [x - pad_x, current_y - pad_y, x + line_width + pad_x, current_y + line_h + pad_y],
                radius=10, fill=(*bg_rgb, bg_opacity)
            )
        draw_text_with_spacing(draw, line, (x, current_y), font, text_color, char_spacing)
        current_y += line_h
    return text_canvas


def split_text_smartly(full_text: str, max_chars_per_line: int) -> List[str]:
    raw = " ".join((full_text or "").split())
    if not raw:
        return []
    sentences = [s.strip() + "." for s in raw.split(".") if s.strip()]
    final_segments: List[str] = []
    current_chunk = ""
    for sentence in sentences:
        test_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence
        wrapped = textwrap.wrap(test_chunk, width=max_chars_per_line)
        line_count = len(wrapped)
        if line_count > 10:
            if current_chunk:
                final_segments.append(current_chunk)
                current_chunk = sentence
            else:
                final_segments.append(sentence)
                current_chunk = ""
        else:
            current_chunk = test_chunk
    if current_chunk:
        final_segments.append(current_chunk)
    return final_segments


def create_popup_frame(
    text: str,
    bg_image: Image.Image,
    font_path: str,
    font_size: int,
    text_color: str,
    bg_color_hex: str,
    opacity: int,
    line_spacing: int,
    char_spacing: int,
    bg_type: str = "full_bar",
) -> Image.Image:
    img = bg_image.copy().convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    effective_char_w = (font_size * 0.5) + char_spacing
    max_chars_per_line = max(8, int((w * 0.85) / max(effective_char_w, 1e-6)))
    wrapped_lines = textwrap.wrap(text, width=max_chars_per_line)
    if not wrapped_lines:
        wrapped_lines = [text[: max_chars_per_line * 3]]
    line_h = font_size + line_spacing
    box_h = len(wrapped_lines) * line_h + 60
    box_y = h * 0.6
    hx = bg_color_hex.lstrip("#")
    if len(hx) >= 6:
        bg_rgb = tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4))
    else:
        bg_rgb = (0, 0, 0)
    if bg_type == "hug_text":
        # 贴字背景：每行独立圆角矩形，宽度随该行文字变化
        pad_x, pad_y = 24, 12
        current_y_bg = box_y + 30
        for line in wrapped_lines:
            lw_bg = sum(draw_ov.textlength(c, font=font) for c in line) + (char_spacing * max(0, len(line) - 1))
            lx = (w - lw_bg) // 2 - pad_x
            ly = current_y_bg - pad_y
            draw_ov.rounded_rectangle(
                [lx, ly, lx + lw_bg + pad_x * 2, ly + line_h + pad_y * 2],
                radius=14, fill=(*bg_rgb, opacity)
            )
            current_y_bg += line_h
    else:
        # full_bar：全幅横条（圆角）
        draw_ov.rounded_rectangle([40, box_y, w - 40, box_y + box_h], radius=10, fill=(*bg_rgb, opacity))
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw_final = ImageDraw.Draw(img)
    current_y = box_y + 30
    for line in wrapped_lines:
        line_width = sum(draw_final.textlength(c, font=font) for c in line) + (
            char_spacing * max(0, len(line) - 1)
        )
        start_x = (w - line_width) // 2
        draw_text_with_spacing(draw_final, line, (start_x, current_y), font, text_color, char_spacing)
        current_y += line_h
    return img


def create_popup_video_on_bg(
    text: str,
    bg_image: Image.Image,
    output_file: Path,
    style: dict,
    font_path: str,
    speed: float = 1.0,
) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    t_hex = style.get("text_color", "#FFFFFF")
    bar_hex = style.get("bg_color", "#000000")
    opacity = style.get("bg_opacity", 150)
    bg_type = style.get("bg_type", "full_bar")
    f_size = style.get("font_size", 45)
    line_spacing = 8
    char_spacing = 0
    fps = 30
    wpm = int(420 * speed)
    bg = bg_image.convert("RGB")
    effective_char_w = (f_size * 0.5) + char_spacing
    w0, _ = bg.size
    max_chars_per_line = max(8, int((w0 * 0.85) / max(effective_char_w, 1e-6)))
    segments = split_text_smartly(text, max_chars_per_line)
    if not segments:
        segments = textwrap.wrap(text, width=max_chars_per_line) or [text]
    frames: List[np.ndarray] = []
    durations: List[float] = []
    for seg in segments:
        words = len(seg.split())
        sec = max(2.5, (words / wpm) * 60)
        frame_img = create_popup_frame(
            seg, bg, font_path, f_size, t_hex, bar_hex, opacity, line_spacing, char_spacing, bg_type
        )
        frames.append(np.array(frame_img))
        durations.append(sec)
    if not frames:
        return None
    clip = None
    try:
        try:
            clip = ImageSequenceClip(frames, durations=durations)
        except TypeError:
            expanded: List[np.ndarray] = []
            for arr, d in zip(frames, durations):
                n = max(1, int(d * fps))
                for _ in range(n):
                    expanded.append(arr)
            clip = ImageSequenceClip(expanded, fps=fps)
        clip.write_videofile(str(output_file), fps=fps, codec="libx264", audio=False)
    finally:
        if clip is not None:
            clip.close()
    _merge_music_to_video(output_file)
    return f"popup_{bg_type}"


# 图像模型合规过滤：自动替换触发安全过滤器的词（模块级常量）
_COMPLIANCE_MAP = {
    "pinned against wall": "standing inches apart near a wall",
    "pinned against": "standing close to",
    "pinned to wall": "leaning near a wall",
    "grabbed by waist": "hand hovering near waist",
    "pulled by waist": "hand hovering near waist",
    "wrist trapped": "hand resting on wrist",
    "wrist grabbed": "hand reaching for wrist",
    "lips hovering": "face leaning close",
    "lips almost touching": "faces inches apart, almost-kiss",
    "barely contained hunger": "barely restrained emotion",
    "barely contained desire": "barely restrained longing",
    "fingers digging into silk": "fingers resting on silk fabric",
    "fingers digging into": "fingers gently touching",
    "buttons undone": "collar slightly loosened",
    "crimson lipstick smeared": "lipstick slightly smudged",
    "smeared lipstick": "slightly smudged lipstick",
    "necklace snapped": "necklace unclasped",
    "snapped mid-grab": "unclasped in tension",
    "heel on chest": "standing tall above",
    "knee on back": "standing over",
    "breath between lips": "breath caught in throat",
    "forbidden touch": "forbidden glance",
    "gaze burning through": "gaze lingering intensely",
    "space between them vanishes": "space between them crackles with tension",
    "body pressed": "standing near each other",
    "pressed against body": "standing close together",
    "exposed skin": "elegant attire",
    "torn lingerie": "disheveled elegant dress",
}


# ====== 小说内容获取 ======
from html.parser import HTMLParser

class _NovelContentParser(HTMLParser):
    """从 HTML 中提取 <p> 标签内容，保留章节标题格式"""
    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self._current_tag = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._current_tag = "p"

    def handle_endtag(self, tag):
        if tag == "p":
            self._current_tag = None

    def handle_data(self, data):
        if self._current_tag == "p":
            text = data.strip()
            if text:
                self.paragraphs.append(text)


def _fetch_novel_content(novel_id: str) -> dict:
    """从外部 API 获取小说内容并返回纯文本"""
    try:
        from urllib.parse import quote
        url = f"https://hw.manage.api.pingykj.com/novel/novel/getChaptersContent?novelId={quote(novel_id, safe='')}&viewFree=false"
        r = requests.get(url, timeout=30)
        if r.status_code >= 400:
            return {"status": "error", "error": f"小说服务返回 HTTP {r.status_code}"}
        parser = _NovelContentParser()
        parser.feed(r.text)
        if not parser.paragraphs:
            return {"status": "error", "error": "未获取到小说内容，请检查小说ID"}
        content = "\n\n".join(parser.paragraphs)
        return {"status": "success", "content": content}
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "获取小说内容超时，请检查网络"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "error": "无法连接到小说服务，请稍后重试"}
    except Exception as e:
        return {"status": "error", "error": f"获取失败: {str(e)}"}


def run_full_generation(body: GenerateRequest, batch_id: Optional[int] = None) -> dict:
    warnings: List[str] = []
    errors: List[str] = []
    generated_images: List[str] = []
    used_prompts: List[dict] = []
    chat_status = "skipped"
    scroll_video_urls: List[str] = []

    if batch_id is None:
        batch_id = allocate_batch_id(OUTPUT_ROOT)
    batch_dir = OUTPUT_ROOT / str(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    _register_batch(batch_id)

    # 加载视频样式：预设 + 前端传入覆盖
    scroll_style = {**DEFAULT_SCROLL_STYLE, **body.scroll_style}
    popup_style = {**DEFAULT_POPUP_STYLE, **body.popup_style}

    def _resolve_font(style: dict) -> str:
        fn = style.get("font", "arial.ttf")
        fp = BASE_PATH / "ziti" / fn
        return str(fp) if fp.exists() else FONT_PATH

    total_square = body.text_single_count + body.lr_split_count + body.tb_split_count
    scroll_visual_total = body.scroll_count + body.popup_count + body.ai_scroll_count + body.ai_popup_count
    total_needed = total_square + scroll_visual_total
    total_images_expected = total_square + scroll_visual_total

    _update_progress(batch_id, 0, "正在初始化...", "running")

    text_single_prompts: List[dict] = []
    lr_prompts: List[dict] = []
    tb_prompts: List[dict] = []
    scroll_prompts: List[dict] = []

    if body.api_key.strip() and body.api_url.strip() and total_needed > 0:
        try:
            print(f"[CHAT API] 单次调用模式，共 {total_needed} 张图（text_single={body.text_single_count}, lr={body.lr_split_count}, tb={body.tb_split_count}, scroll={scroll_visual_total}）")
            text_single_prompts, lr_prompts, tb_prompts, scroll_prompts = request_image_prompt_plan(
                body.api_url,
                body.api_key,
                body.chat_model_name,
                body.novel_content,
                body.prompt,
                body.text_single_count,
                body.lr_split_count,
                body.tb_split_count,
                scroll_visual_total,
                use_templates=body.use_templates,
            )
            chat_status = "success"
            valid_ts = sum(1 for it in text_single_prompts if isinstance(it, dict) and str(it.get("image_prompt", "")).strip())
            valid_lr = sum(1 for it in lr_prompts if isinstance(it, dict) and str(it.get("image_prompt", "")).strip())
            valid_tb = sum(1 for it in tb_prompts if isinstance(it, dict) and str(it.get("image_prompt", "")).strip())
            valid_sc = sum(1 for it in scroll_prompts if isinstance(it, dict) and str(it.get("image_prompt", "")).strip())
            total_valid = valid_ts + valid_lr + valid_tb + valid_sc
            warnings.append(f"[OK] Chat API 返回: text_single={len(text_single_prompts)}(有效{valid_ts}), lr={len(lr_prompts)}(有效{valid_lr}), tb={len(tb_prompts)}(有效{valid_tb}), scroll={len(scroll_prompts)}(有效{valid_sc})")
            if total_valid == 0 and total_needed > 0:
                _push_sse_event(batch_id, {"type": "error", "status": "failed", "message": "对话模型返回了空的提示词，请检查小说内容或 API 配置"})
                _update_progress(batch_id, 0, "Chat API 返回空 prompt", "failed")
                _set_batch_cancel(batch_id)
                return {
                    "status": "failed",
                    "batch_id": batch_id,
                    "message": "Chat API 返回了空的提示词，请检查小说内容或 API 配置",
                    "warnings": ["[FAIL] Chat API 返回空 prompt，任务已自动取消。"],
                }
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[CHAT API FAIL] 完整错误:\n{tb}")
            err_msg = str(e)[:300]
            _push_sse_event(batch_id, {"type": "error", "status": "failed", "message": f"对话模型解析失败: {err_msg}"})
            _update_progress(batch_id, 0, f"Chat API 调用失败: {err_msg}", "failed")
            _set_batch_cancel(batch_id)
            return {
                "status": "failed",
                "batch_id": batch_id,
                "message": f"Chat API 调用失败: {err_msg}",
                "warnings": [f"[FAIL] 对话模型调用失败，任务已自动取消。错误：{err_msg}"],
            }

    _update_progress(batch_id, 5, "Chat API 完成，开始生成图片...", "running")

    base_style = (body.prompt or "").strip()
    scroll_base = f"{base_style}, 9:16 vertical portrait, no text, no subtitles, no letters" if base_style else ""

    text_single_prompts = pad_items(text_single_prompts, body.text_single_count, base_style)
    lr_prompts = pad_items(lr_prompts, body.lr_split_count, base_style)
    tb_prompts = pad_items(tb_prompts, body.tb_split_count, base_style)
    scroll_prompts = pad_items(scroll_prompts, scroll_visual_total, scroll_base)

    headers = {"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"}
    base_url = body.api_url.rstrip("/") + "/images/generations"
    image_seq = 0

    def _sanitize_image_prompt(p: str) -> str:
        """自动替换可能触发图像模型安全过滤器的违规词"""
        result = p
        warnings = []
        for banned, safe in _COMPLIANCE_MAP.items():
            if banned in result:
                result = result.replace(banned, safe)
                warnings.append(banned)
        if warnings:
            print(f"[COMPLIANCE] Auto-fixed risky terms in prompt: {', '.join(warnings)}")
        return result

    def fetch_image(prompt: str, size: str, label: str) -> Optional[str]:
        nonlocal image_seq
        if _is_batch_cancelled(batch_id):
            return None
        if not body.api_key.strip():
            errors.append(f"{label}：未配置 API 密钥")
            return None
        prompt_send = (prompt or "").strip()
        prompt_send = _sanitize_image_prompt(prompt_send)
        payload = {"model": body.image_model_name, "prompt": prompt_send, "size": size, "n": 1}
        try:
            r = requests.post(base_url, json=payload, headers=headers, timeout=120)
            if r.status_code >= 400:
                errors.append(f"{label} HTTP {r.status_code}: {_api_error_snippet(r)}")
                return None
            j = r.json()
            data = j.get("data")
            if not isinstance(data, list) or not data:
                errors.append(f"{label} 无 data：{json.dumps(j, ensure_ascii=False)[:400]}")
                return None
            item = data[0]
            img_bytes = None
            if isinstance(item, dict):
                if item.get("url"):
                    ir = requests.get(item["url"], timeout=120)
                    img_bytes = ir.content if ir.status_code < 400 else None
                elif item.get("b64_json"):
                    img_bytes = base64.b64decode(item["b64_json"])
            if not img_bytes:
                errors.append(f"{label} 无法获取图片数据")
                return None
            with seq_lock:
                image_seq += 1
                fname = f"{batch_id}-{image_seq}.png"
            fpath = batch_dir / fname
            with open(fpath, "wb") as f:
                f.write(img_bytes)
            return fname
        except Exception as e:
            errors.append(f"{label} {e}")
            return None

    square_jobs: List[Tuple[str, int, str]] = []
    for i in range(body.text_single_count):
        square_jobs.append(("text_single", i, f"带文字单图{i + 1}"))
    for i in range(body.lr_split_count):
        square_jobs.append(("lr", i, f"左右分屏{i + 1}"))
    for i in range(body.tb_split_count):
        square_jobs.append(("tb", i, f"上下分屏{i + 1}"))

    # 方图生成（单任务辅助函数）
    def _generate_square_job(kind, idx, lab):
        """单个方图生成任务"""
        item = (
            text_single_prompts[idx] if kind == "text_single"
            else lr_prompts[idx] if kind == "lr"
            else tb_prompts[idx]
        )
        core = item.get("image_prompt", "")
        final_p = finalize_square_prompt(kind, core, base_style)
        name = fetch_image(final_p, "1024x1024", lab)
        prompt_dict = {"label": lab, "type": kind, "prompt": final_p}
        return (kind, idx, lab, name, prompt_dict)

    concurrency = max(1, min(getattr(body, 'concurrency', 2) or 2, 16))
    seq_lock = threading.Lock()
    if concurrency > 1 and len(square_jobs) > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as sq_executor:
            futures = {sq_executor.submit(_generate_square_job, k, i, l): (k, i, l) for k, i, l in square_jobs}
            for future in as_completed(futures):
                if _is_batch_cancelled(batch_id):
                    break
                try:
                    kind, idx, lab, name, prompt_dict = future.result()
                    if name:
                        used_prompts.append(prompt_dict)
                        fpath = batch_dir / name
                        # Extract text fields from the original item
                        item = (
                            text_single_prompts[idx] if kind == "text_single"
                            else lr_prompts[idx] if kind == "lr"
                            else tb_prompts[idx]
                        )
                        tb_text = item.get("text_bottom", "")
                        tl_text = item.get("text_left", "")
                        tr_text = item.get("text_right", "")
                        to_text = item.get("text_overlay", "")
                        text_enabled = (
                            body.text_single_text_enabled if kind == "text_single"
                            else body.lr_split_text_enabled if kind == "lr"
                            else body.tb_split_text_enabled
                        )
                        if text_enabled and (tb_text or tl_text or tr_text or to_text):
                            try:
                                composite_text_on_image(
                                    fpath, text_bottom=tb_text, text_left=tl_text, text_right=tr_text, text_overlay=to_text
                                )
                            except Exception as e:
                                warnings.append(f"{lab} 文字合成失败：{e}")
                        generated_images.append(f"/static/output/{batch_id}/{name}")
                        _push_image_ready(batch_id, f"/static/output/{batch_id}/{name}", lab)
                except Exception as e:
                    warnings.append(f"生成失败: {e}")
                if total_images_expected > 0:
                    pct = int(5 + (image_seq / total_images_expected) * 70)
                    _update_progress(batch_id, min(pct, 75), f"生成图片 {image_seq}/{total_images_expected}...", "running")
    else:
        # 单图或并发关闭时使用顺序生成
        for kind, idx, lab in square_jobs:
            if _is_batch_cancelled(batch_id):
                break

            item = (
                text_single_prompts[idx]
                if kind == "text_single"
                else lr_prompts[idx]
                if kind == "lr"
                else tb_prompts[idx]
            )
            core = item.get("image_prompt", "")

            final_p = finalize_square_prompt(kind, core, base_style)

            name = fetch_image(final_p, "1024x1024", lab)

            if name:
                used_prompts.append({"label": lab, "type": kind, "prompt": final_p})
                fpath = batch_dir / name
                tb_text = item.get("text_bottom", "")
                tl_text = item.get("text_left", "")
                tr_text = item.get("text_right", "")
                to_text = item.get("text_overlay", "")
                text_enabled = (
                    body.text_single_text_enabled if kind == "text_single"
                    else body.lr_split_text_enabled if kind == "lr"
                    else body.tb_split_text_enabled
                )
                if text_enabled and (tb_text or tl_text or tr_text or to_text):
                    try:
                        composite_text_on_image(
                            fpath,
                            text_bottom=tb_text,
                            text_left=tl_text,
                            text_right=tr_text,
                            text_overlay=to_text,
                        )
                    except Exception as e:
                        warnings.append(f"{lab} 文字合成失败：{e}")
                generated_images.append(f"/static/output/{batch_id}/{name}")
                _push_image_ready(batch_id, f"/static/output/{batch_id}/{name}", lab)
                if total_images_expected > 0:
                    pct = int(5 + (image_seq / total_images_expected) * 70)
                    _update_progress(batch_id, min(pct, 75), f"生成图片 {image_seq}/{total_images_expected}...", "running")

    # 滚屏竖图生成（单任务辅助函数）
    def _generate_scroll_job(i):
        """单个滚屏竖图生成任务"""
        item = scroll_prompts[i] if i < len(scroll_prompts) else {"image_prompt": scroll_base}
        p = finalize_scroll_visual_prompt(item.get("image_prompt", ""), scroll_base)
        if "9:16" not in p.lower():
            p += ", 9:16 vertical portrait composition"
        if i < body.scroll_count:
            lab = f"滚屏单图{i + 1}"
        elif i < body.scroll_count + body.popup_count:
            lab = f"弹屏底图{i - body.scroll_count + 1}"
        elif i < body.scroll_count + body.popup_count + body.ai_scroll_count:
            lab = f"AI滚屏底图{i - body.scroll_count - body.popup_count + 1}"
        else:
            lab = f"AI弹屏底图{i - body.scroll_count - body.popup_count - body.ai_scroll_count + 1}"
        name = fetch_image(p, "768x1344", lab)
        return (i, name, lab, p)

    scroll_png_paths: List[Path] = []

    if concurrency > 1 and scroll_visual_total > 1:
        scroll_results: Dict[int, Tuple[str, str, str]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as sc_executor:
            futures = {sc_executor.submit(_generate_scroll_job, i): i for i in range(scroll_visual_total)}
            for future in as_completed(futures):
                if _is_batch_cancelled(batch_id):
                    break
                try:
                    i, name, lab, p = future.result()
                    scroll_results[i] = (name, lab, p)
                except Exception as e:
                    warnings.append(f"生成失败: {e}")
                # 每个滚屏图完成后更新进度
                if total_images_expected > 0:
                    pct = int(5 + (image_seq / total_images_expected) * 70)
                    _update_progress(batch_id, min(pct, 75), f"生成图片 {image_seq}/{total_images_expected}...", "running")
        # 按原始顺序重建 scroll_png_paths
        for i in range(scroll_visual_total):
            if i in scroll_results:
                name, lab, p = scroll_results[i]
                if name:
                    used_prompts.append({"label": lab, "type": "scroll", "prompt": p})
                    scroll_png_paths.append(batch_dir / name)
    else:
        # 单图或并发关闭时使用顺序生成
        for i in range(scroll_visual_total):
            if _is_batch_cancelled(batch_id):
                break
            i, name, lab, p = _generate_scroll_job(i)
            if name:
                used_prompts.append({"label": lab, "type": "scroll", "prompt": p})
                scroll_png_paths.append(batch_dir / name)
                if total_images_expected > 0:
                    pct = int(5 + (image_seq / total_images_expected) * 70)
                    _update_progress(batch_id, min(pct, 75), f"生成图片 {image_seq}/{total_images_expected}...", "running")

    # 滚屏视频生成 (每张滚屏底图生成一个视频)
    video_source_paths = [str(p) for p in scroll_png_paths[: body.scroll_count]]
    scroll_font_path = _resolve_font(scroll_style)

    _update_progress(batch_id, 75, "图片生成完成，开始合成视频...", "running")

    if not _is_batch_cancelled(batch_id) and video_source_paths and body.video_text.strip():
        for vid_idx, src_path in enumerate(video_source_paths):
            if _is_batch_cancelled(batch_id):
                break
            try:
                base_img = Image.open(src_path).convert("RGB")
                W, H = base_img.size
                n_shrink, fps, l_spacing = 1.23, 30, 7
                wpm = int(360 * body.scroll_style.get("speed", 1.0))
                f_size = scroll_style.get("font_size", 46)
                t_color = scroll_style.get("text_color", "#FFFFFF")
                bg_type = scroll_style.get("bg_type", "full_bar")
                bg_color = scroll_style.get("bg_color", "#000000")
                bg_opacity = scroll_style.get("bg_opacity", 150)
                ov_w, ov_h = int(W / n_shrink), int(H / n_shrink)
                ov_x, ov_y = (W - ov_w) // 2, (H - ov_h) // 2
                text_canvas = pre_render_text(body.video_text, ov_w - 40, scroll_font_path, f_size, t_color, l_spacing, 0,
                                              bg_type=bg_type, bg_color=bg_color, bg_opacity=bg_opacity)
                text_h = text_canvas.size[1]
                # full_bar: 全幅半透明背景；hug_text: 透明（文字自带贴字背景）
                if bg_type == "hug_text":
                    overlay_pic = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
                else:
                    hx = bg_color.lstrip("#")
                    if len(hx) >= 6:
                        bg_rgb = tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))
                    else:
                        bg_rgb = (0, 0, 0)
                    overlay_pic = Image.new("RGBA", (ov_w, ov_h), (*bg_rgb, bg_opacity))
                base_frame = base_img.copy().convert("RGBA")
                base_frame.paste(overlay_pic, (ov_x, ov_y), overlay_pic)
                y_start, y_end = int(ov_h / 2), ov_h - text_h - 30
                valid_words = len([w for w in body.video_text.split() if w.strip()])
                duration = max(3.0, (valid_words / wpm) * 60)
                frames = []
                scroll_frames = max(1, int(duration * fps))
                y_offsets = np.linspace(y_start, y_end, scroll_frames)

                def make_frame(y_off):
                    box = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
                    box.paste(text_canvas, (20, int(y_off)), text_canvas)
                    out = base_frame.copy()
                    out.paste(box, (ov_x, ov_y), box)
                    rgb = out.convert("RGB")
                    return np.asarray(rgb, dtype=np.uint8)

                start_img = make_frame(y_start)
                for _ in range(fps * 2):
                    if _is_batch_cancelled(batch_id):
                        break
                    frames.append(start_img)
                for y in y_offsets:
                    if _is_batch_cancelled(batch_id):
                        break
                    frames.append(make_frame(y))
                end_img = make_frame(y_end)
                for _ in range(fps * 3):
                    if _is_batch_cancelled(batch_id):
                        break
                    frames.append(end_img)

                if not _is_batch_cancelled(batch_id) and frames:
                    suffix = f"-{vid_idx + 1}" if len(video_source_paths) > 1 else ""
                    v_name = f"{batch_id}-scroll-video{suffix}.mp4"
                    v_path = batch_dir / v_name
                    clip = ImageSequenceClip(frames, fps=fps)
                    try:
                        clip.write_videofile(str(v_path), fps=fps, codec="libx264", audio=False)
                    finally:
                        clip.close()
                    _merge_music_to_video(v_path)
                    scroll_video_urls.append(f"/static/output/{batch_id}/{v_name}")
            except Exception as e:
                warnings.append(f"滚屏视频{vid_idx + 1}合成失败：{e}")

    # AI 滚屏视频生成 (AI 写文案，复用滚屏合成逻辑)
    ai_scroll_urls: List[str] = []
    if not _is_batch_cancelled(batch_id) and body.ai_scroll_count > 0:
        # 取 AI 滚屏的底图
        ai_scroll_start = body.scroll_count + body.popup_count
        ai_scroll_end = ai_scroll_start + body.ai_scroll_count
        ai_scroll_pngs = [str(p) for p in scroll_png_paths[ai_scroll_start:ai_scroll_end]]
        # 取 AI 滚屏的 image_prompts
        ai_scroll_prompts = [sp.get("image_prompt", "") for sp in scroll_prompts[ai_scroll_start:ai_scroll_end]]
        ai_scripts = request_video_scripts(
            body.api_url, body.api_key, body.chat_model_name,
            body.novel_content, ai_scroll_prompts, body.ai_scroll_count, "AI scroll"
        )
        if ai_scripts:
            for vid_idx, (src_path, script_text) in enumerate(zip(ai_scroll_pngs, ai_scripts)):
                if _is_batch_cancelled(batch_id):
                    break
                if not script_text.strip():
                    continue
                try:
                    base_img = Image.open(src_path).convert("RGB")
                    W, H = base_img.size
                    n_shrink, fps, l_spacing = 1.23, 30, 7
                    wpm = int(360 * body.scroll_style.get("speed", 1.0))
                    f_size = scroll_style.get("font_size", 46)
                    t_color = scroll_style.get("text_color", "#FFFFFF")
                    bg_type = scroll_style.get("bg_type", "full_bar")
                    bg_color = scroll_style.get("bg_color", "#000000")
                    bg_opacity = scroll_style.get("bg_opacity", 150)
                    ov_w, ov_h = int(W / n_shrink), int(H / n_shrink)
                    ov_x, ov_y = (W - ov_w) // 2, (H - ov_h) // 2
                    text_canvas = pre_render_text(script_text, ov_w - 40, scroll_font_path, f_size, t_color, l_spacing, 0,
                                                  bg_type=bg_type, bg_color=bg_color, bg_opacity=bg_opacity)
                    text_h = text_canvas.size[1]
                    if bg_type == "hug_text":
                        overlay_pic = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
                    else:
                        hx = bg_color.lstrip("#")
                        if len(hx) >= 6:
                            bg_rgb = tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))
                        else:
                            bg_rgb = (0, 0, 0)
                        overlay_pic = Image.new("RGBA", (ov_w, ov_h), (*bg_rgb, bg_opacity))
                    base_frame = base_img.copy().convert("RGBA")
                    base_frame.paste(overlay_pic, (ov_x, ov_y), overlay_pic)
                    y_start, y_end = int(ov_h / 2), ov_h - text_h - 30
                    valid_words = len([w for w in script_text.split() if w.strip()])
                    duration = max(3.0, (valid_words / wpm) * 60)
                    frames = []
                    scroll_frames = max(1, int(duration * fps))
                    y_offsets = np.linspace(y_start, y_end, scroll_frames)

                    def make_frame_ai(y_off):
                        box = Image.new("RGBA", (ov_w, ov_h), (0, 0, 0, 0))
                        box.paste(text_canvas, (20, int(y_off)), text_canvas)
                        out = base_frame.copy()
                        out.paste(box, (ov_x, ov_y), box)
                        return np.asarray(out.convert("RGB"), dtype=np.uint8)

                    start_img = make_frame_ai(y_start)
                    for _ in range(fps * 2):
                        if _is_batch_cancelled(batch_id):
                            break
                        frames.append(start_img)
                    for y in y_offsets:
                        if _is_batch_cancelled(batch_id):
                            break
                        frames.append(make_frame_ai(y))
                    end_img = make_frame_ai(y_end)
                    for _ in range(fps * 3):
                        if _is_batch_cancelled(batch_id):
                            break
                        frames.append(end_img)

                    if not _is_batch_cancelled(batch_id) and frames:
                        suffix = f"-{vid_idx + 1}" if len(ai_scroll_pngs) > 1 else ""
                        v_name = f"{batch_id}-ai-scroll{suffix}.mp4"
                        v_path = batch_dir / v_name
                        clip = ImageSequenceClip(frames, fps=fps)
                        try:
                            clip.write_videofile(str(v_path), fps=fps, codec="libx264", audio=False)
                        finally:
                            clip.close()
                        _merge_music_to_video(v_path)
                        ai_scroll_urls.append(f"/static/output/{batch_id}/{v_name}")
                except Exception as e:
                    warnings.append(f"AI滚屏视频{vid_idx + 1}合成失败：{e}")
        else:
            warnings.append("AI滚屏文案生成失败，跳过AI滚屏视频合成")

    # 弹屏视频生成
    popup_urls: List[str] = []
    if not _is_batch_cancelled(batch_id) and body.popup_count > 0 and body.video_text.strip():
        for i in range(body.popup_count):
            if _is_batch_cancelled(batch_id):
                break
            bg_idx = body.scroll_count + i
            if bg_idx >= len(scroll_png_paths):
                continue
            try:
                bg_img = Image.open(scroll_png_paths[bg_idx]).convert("RGB")
                out = batch_dir / f"{batch_id}-popup-{i + 1}.mp4"
                tag = create_popup_video_on_bg(body.video_text, bg_img, out, popup_style, _resolve_font(popup_style), speed=body.popup_style.get("speed", 1.0))
                if tag:
                    u = f"/static/output/{batch_id}/{out.name}"
                    generated_images.append(u)
                    _push_image_ready(batch_id, u, f"弹屏视频{i + 1}")
                    popup_urls.append(u)
            except Exception as e:
                errors.append(f"弹屏视频{i + 1}：{e}")

    # AI 弹屏视频生成 (AI 写文案，复用弹屏合成逻辑)
    ai_popup_urls: List[str] = []
    if not _is_batch_cancelled(batch_id) and body.ai_popup_count > 0:
        ai_popup_start = body.scroll_count + body.popup_count + body.ai_scroll_count
        ai_popup_end = ai_popup_start + body.ai_popup_count
        ai_popup_pngs = [str(p) for p in scroll_png_paths[ai_popup_start:ai_popup_end]]
        ai_popup_prompts = [sp.get("image_prompt", "") for sp in scroll_prompts[ai_popup_start:ai_popup_end]]
        ai_popup_scripts = request_video_scripts(
            body.api_url, body.api_key, body.chat_model_name,
            body.novel_content, ai_popup_prompts, body.ai_popup_count, "AI popup"
        )
        if ai_popup_scripts:
            for i, script_text in enumerate(ai_popup_scripts):
                if _is_batch_cancelled(batch_id):
                    break
                if not script_text.strip():
                    continue
                bg_idx = body.scroll_count + body.popup_count + body.ai_scroll_count + i
                if bg_idx >= len(scroll_png_paths):
                    continue
                try:
                    bg_img = Image.open(scroll_png_paths[bg_idx]).convert("RGB")
                    out = batch_dir / f"{batch_id}-ai-popup-{i + 1}.mp4"
                    tag = create_popup_video_on_bg(script_text, bg_img, out, popup_style, _resolve_font(popup_style), speed=body.popup_style.get("speed", 1.0))
                    if tag:
                        u = f"/static/output/{batch_id}/{out.name}"
                        generated_images.append(u)
                        _push_image_ready(batch_id, u, f"AI弹屏视频{i + 1}")
                        ai_popup_urls.append(u)
                except Exception as e:
                    errors.append(f"AI弹屏视频{i + 1}：{e}")
        else:
            warnings.append("AI弹屏文案生成失败，跳过AI弹屏视频合成")

    # 清理 9:16 滚屏底图（已合成视频的底图不再保留）
    if not _is_batch_cancelled(batch_id) and (body.video_text.strip() or body.ai_scroll_count > 0 or body.ai_popup_count > 0):
        for p in scroll_png_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    _update_progress(batch_id, 90, "视频合成完成，正在保存...", "running")

    # 最终状态判定
    if _is_batch_cancelled(batch_id):
        status, message = "cancelled", "任务已被用户手动取消。"
    else:
        expected = total_square + scroll_visual_total
        got_png = image_seq
        if expected == 0 and body.popup_count == 0 and body.ai_popup_count == 0:
            status, message = "success", "未请求出图或弹屏。"
        elif got_png == 0 and expected > 0:
            status, message = "failed", "出图全部失败。"
        elif got_png < expected:
            status, message = "partial", f"图片完成 {got_png}/{expected}。"
        else:
            status, message = "success", "任务完成。"

    final_pct = 100 if status == "success" else (90 if status == "partial" else 0)
    _update_progress(batch_id, final_pct, message, status)
    _deregister_batch(batch_id)

    all_video_urls = [u for u in generated_images if u.endswith(".mp4")] + scroll_video_urls + ai_scroll_urls
    dl_names = [Path(u).name for u in generated_images if u.endswith(".png")]
    for u in scroll_video_urls + ai_scroll_urls:
        dl_names.append(Path(u).name)
    dl_names.extend(Path(u).name for u in popup_urls + ai_popup_urls)

    return {
        "status": status,
        "batch_id": batch_id,
        "batch_folder": str(batch_id),
        "output_root": str(OUTPUT_ROOT),
        "novel_id": body.novel_id if hasattr(body, 'novel_id') else "",
        "images": [u for u in generated_images if not u.endswith(".mp4")],
        "videos": all_video_urls,
        "video": scroll_video_urls[0] if scroll_video_urls else "",
        "scroll_videos": scroll_video_urls,
        "popup_videos": popup_urls,
        "ai_scroll_videos": ai_scroll_urls,
        "ai_popup_videos": ai_popup_urls,
        "message": message,
        "warnings": warnings,
        "errors": errors,
        "download_filenames": dl_names,
        "chat_status": chat_status,
        "used_prompts": used_prompts,
    }


def _background_generation(body: GenerateRequest, batch_id: int) -> None:
    """后台线程执行生成，完成后保存元数据"""
    try:
        result = run_full_generation(body, batch_id=batch_id)
    except Exception as e:
        result = {
            "status": "failed",
            "batch_id": batch_id,
            "message": f"后台任务异常: {e}",
            "novel_id": body.novel_id if hasattr(body, 'novel_id') else "",
            "images": [],
            "videos": [],
            "popup_videos": [],
            "errors": [str(e)],
            "chat_status": "failed",
        }
        _update_progress(batch_id, 0, f"任务失败: {e}", "failed")
    finally:
        _deregister_batch(batch_id)
        _save_batch_meta(result)


@app.post("/api/generate")
def api_generate(body: GenerateRequest):
    """提交生产任务到后台线程，立即返回 batch_id"""
    batch_id = allocate_batch_id(OUTPUT_ROOT)
    (OUTPUT_ROOT / str(batch_id)).mkdir(parents=True, exist_ok=True)
    _register_batch(batch_id)
    _update_progress(batch_id, 0, "任务已提交，正在启动...", "running")
    _EXECUTOR.submit(_background_generation, body, batch_id)
    return {
        "status": "submitted",
        "batch_id": batch_id,
        "message": f"任务 #{batch_id} 已提交到后台处理",
    }


@app.post("/generate")
def generate_alias(body: GenerateRequest):
    return api_generate(body)


# --- 取消接口 ---
@app.post("/api/cancel")
def api_cancel(batch_id: Optional[int] = None):
    """取消任务：指定 batch_id 取消单个，否则取消全部"""
    if batch_id is not None:
        _set_batch_cancel(batch_id)
        _update_progress(batch_id, 0, "用户已取消", "cancelled")
        return {"status": "cancelling", "message": f"正在取消任务 #{batch_id}...", "batch_id": batch_id}
    else:
        with _BATCH_CANCEL_LOCK:
            for bid in list(_BATCH_CANCEL_EVENTS.keys()):
                _BATCH_CANCEL_EVENTS[bid].set()
                _update_progress(bid, 0, "用户已取消全部", "cancelled")
        return {"status": "cancelling", "message": "正在取消所有运行中的任务..."}


# --- 进度接口 ---
@app.get("/api/progress")
def api_progress_all():
    """返回所有运行中任务的进度"""
    running = _get_all_running_progress()
    return {"running": list(running.values()), "count": len(running)}


@app.get("/api/progress/{batch_id}")
def api_progress_detail(batch_id: int):
    """返回单个批次进度"""
    progress = _get_progress(batch_id)
    if progress is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 无进度信息")
    return progress


@app.get("/api/prompt-rules")
def api_prompt_rules():
    """返回最新提示词.txt 内容，供前端预填"""
    return {"content": ""}


@app.get("/api/templates")
def api_templates():
    """返回模板索引状态"""
    square = [t for t in TEMPLATES_INDEX if t.get("ratio") == "1:1"]
    portrait = [t for t in TEMPLATES_INDEX if t.get("ratio") == "9:16"]
    return {
        "loaded": len(TEMPLATES_INDEX) > 0,
        "total": len(TEMPLATES_INDEX),
        "square_count": len(square),
        "portrait_count": len(portrait),
        "message": f"已加载 {len(TEMPLATES_INDEX)} 条模板描述（1:1={len(square)}, 9:16={len(portrait)}）"
        if TEMPLATES_INDEX
        else "未找到 templates_index.json，请先运行 scripts/build_template_index.py 生成模板索引",
    }


VIDEO_STYLES_PATH = BASE_PATH / "video_styles.json"
DEFAULT_SCROLL_STYLE = {"font":"corbelb.ttf","font_size":46,"text_color":"#FFFFFF","bg_type":"full_bar","bg_color":"#000000","bg_opacity":150,"weight":1}
DEFAULT_POPUP_STYLE = {"font":"corbelb.ttf","font_size":45,"text_color":"#FFFFFF","bg_type":"full_bar","bg_color":"#000000","bg_opacity":150,"weight":1}

def _create_default_library() -> dict:
    """创建默认样式库"""
    t = int(time.time() * 1000)
    return {
        "version": 2,
        "styles": [
            {"id": f"s{t}", "name": "默认滚屏", "type": "scroll", **DEFAULT_SCROLL_STYLE},
            {"id": f"s{t+1}", "name": "默认弹屏", "type": "popup", **DEFAULT_POPUP_STYLE},
        ],
        "lastUsed": {"scroll": f"s{t}", "popup": f"s{t+1}"},
    }

def _save_video_styles(data: dict) -> None:
    """保存样式库到文件"""
    VIDEO_STYLES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_video_styles() -> dict:
    """加载样式库，自动迁移旧格式"""
    if VIDEO_STYLES_PATH.exists():
        try:
            data = json.loads(VIDEO_STYLES_PATH.read_text(encoding="utf-8"))
            if data.get("version") == 2 and "styles" in data:
                return data
            # 旧格式迁移
            if "scroll" in data or "popup" in data:
                t = int(time.time() * 1000)
                styles = []
                if "scroll" in data:
                    styles.append({"id": f"s{t}", "name": "默认滚屏", "type": "scroll", **{k: v for k, v in data["scroll"].items() if k in DEFAULT_SCROLL_STYLE}})
                if "popup" in data:
                    styles.append({"id": f"s{t+1}", "name": "默认弹屏", "type": "popup", **{k: v for k, v in data["popup"].items() if k in DEFAULT_POPUP_STYLE}})
                migrated = {"version": 2, "styles": styles, "lastUsed": {"scroll": f"s{t}", "popup": f"s{t+1}"}}
                _save_video_styles(migrated)
                return migrated
        except Exception:
            pass
    return _create_default_library()


@app.get("/api/fonts")
def api_fonts():
    """返回 ziti/ 文件夹中可用字体列表"""
    fonts_dir = BASE_PATH / "ziti"
    fonts = []
    if fonts_dir.exists():
        for f in sorted(fonts_dir.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf"):
                fonts.append(f.name)
    return {"fonts": fonts}


@app.get("/api/video-styles")
def api_get_video_styles():
    """返回当前视频样式配置"""
    return _load_video_styles()


@app.post("/api/video-styles")
def api_save_video_styles(body: dict):
    """保存整个样式库"""
    if "styles" not in body:
        raise HTTPException(status_code=400, detail="缺少 styles 字段")
    _save_video_styles(body)
    return {"status": "ok", "message": "样式库已保存"}


def _save_batch_meta(result: dict) -> None:
    """保存批次元数据到 batch 目录"""
    try:
        batch_id = result.get("batch_id")
        if not batch_id:
            return
        batch_dir = OUTPUT_ROOT / str(batch_id)
        if not batch_dir.exists():
            return
        now = datetime.now().isoformat()
        meta = {
            "batch_id": batch_id,
            "status": result.get("status", "unknown"),
            "message": result.get("message", ""),
            "novel_id": result.get("novel_id", ""),
            "images": result.get("images", []),
            "videos": result.get("videos", []),
            "popup_videos": result.get("popup_videos", []),
            "image_count": len(result.get("images", [])),
            "video_count": len(result.get("videos", [])),
            "warnings": result.get("warnings", []),
            "errors": result.get("errors", []),
            "chat_status": result.get("chat_status", "unknown"),
            "used_prompts": result.get("used_prompts", []),
            "progress": _get_progress(batch_id),
            "created_at": result.get("created_at", now),
            "updated_at": now,
        }
        (batch_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@app.get("/api/history")
def api_history(
    limit: int = 20,
    offset: int = 0,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    has_images: str = "",
    has_videos: str = "",
):
    """返回分页的生成历史（含运行中任务）"""
    items = []
    running_batch_ids = set()
    # 1. 收集运行中任务
    running_progress = _get_all_running_progress()
    for bid, prog in running_progress.items():
        running_batch_ids.add(str(bid))
        items.append({
            "batch_id": str(bid),
            "novel_id": "",
            "status": "running",
            "message": prog.get("step", ""),
            "images": 0,
            "videos": 0,
            "chat_status": "",
            "progress": {"percent": prog.get("percent", 0), "step": prog.get("step", "")},
            "created_at": "",
            "updated_at": "",
        })
    # 2. 收集已完成任务
    try:
        for d in sorted(OUTPUT_ROOT.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0, reverse=True):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            if d.name in running_batch_ids:
                continue
            meta_path = d / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            else:
                pngs = list(d.glob("*.png"))
                mp4s = list(d.glob("*.mp4"))
                meta = {"batch_id": d.name, "images": len(pngs), "videos": len(mp4s), "status": "unknown"}
            imgs = meta.get("images", 0)
            vids = meta.get("videos", 0)
            img_count = len(imgs) if isinstance(imgs, list) else (imgs or 0)
            vid_count = len(vids) if isinstance(vids, list) else (vids or 0)
            items.append({
                "batch_id": meta.get("batch_id", d.name),
                "novel_id": meta.get("novel_id", ""),
                "status": meta.get("status", "unknown"),
                "message": meta.get("message", ""),
                "images": img_count,
                "videos": vid_count,
                "chat_status": meta.get("chat_status", ""),
                "progress": None,
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            })
    except Exception:
        pass

    # 3. 应用过滤器
    filtered = []
    for item in items:
        if status and item.get("status") != status:
            continue
        it_created = item.get("created_at", "")
        if date_from and it_created < date_from:
            continue
        if date_to and it_created > date_to:
            continue
        if has_images == "true" and not item.get("images", 0):
            continue
        if has_videos == "true" and not item.get("videos", 0):
            continue
        filtered.append(item)

    total = len(filtered)
    filtered = filtered[offset:offset + limit]
    return {"history": filtered, "total": total}


@app.get("/api/stats")
def api_stats():
    """返回仪表盘统计数据"""
    total = 0
    success = 0
    total_images = 0
    total_videos = 0
    try:
        for d in OUTPUT_ROOT.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            total += 1
            total_images += len(list(d.glob("*.png")))
            total_videos += len(list(d.glob("*.mp4")))
            meta_path = d / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("status") == "success":
                        success += 1
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "total_jobs": total,
        "success_jobs": success,
        "total_images": total_images,
        "total_videos": total_videos,
        "output_root": str(OUTPUT_ROOT),
    }


@app.get("/api/history/{batch_id}")
def api_history_detail(batch_id: str):
    """返回单个批次的详细信息（图片、视频、提示词）"""
    batch_dir = OUTPUT_ROOT / batch_id
    if not batch_dir.exists() or not batch_dir.is_dir():
        raise HTTPException(status_code=404, detail="批次不存在")

    meta_path = batch_dir / "_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}

    # 如果没有 meta，从文件系统推断
    if not meta or "batch_id" not in meta:
        pngs = sorted([p.name for p in batch_dir.glob("*.png")])
        mp4s = sorted([p.name for p in batch_dir.glob("*.mp4")])
        meta = {"batch_id": batch_id, "images": pngs, "videos": mp4s, "status": "unknown"}

    # 确保 images 是完整路径列表（兼容旧 int 格式）
    imgs = meta.get("images", [])
    if isinstance(imgs, list) and imgs and not str(imgs[0]).startswith("/"):
        imgs = [f"/static/output/{batch_id}/{name}" for name in imgs]
    elif not isinstance(imgs, list):
        pngs = sorted([p.name for p in batch_dir.glob("*.png")])
        imgs = [f"/static/output/{batch_id}/{name}" for name in pngs]

    # 确保 videos 是完整路径列表
    vids = meta.get("videos", [])
    if isinstance(vids, list) and vids and not str(vids[0]).startswith("/"):
        vids = [f"/static/output/{batch_id}/{name}" for name in vids]
    elif not isinstance(vids, list):
        mp4s = sorted([p.name for p in batch_dir.glob("*.mp4")])
        vids = [f"/static/output/{batch_id}/{name}" for name in mp4s]

    # 弹屏视频路径
    popups = meta.get("popup_videos", [])
    if isinstance(popups, list) and popups and not str(popups[0]).startswith("/"):
        popups = [f"/static/output/{batch_id}/{name}" for name in popups]

    return {
        "batch_id": meta.get("batch_id", batch_id),
        "novel_id": meta.get("novel_id", ""),
        "status": meta.get("status", "unknown"),
        "message": meta.get("message", ""),
        "images": imgs,
        "videos": vids,
        "popup_videos": popups,
        "warnings": meta.get("warnings", []),
        "errors": meta.get("errors", []),
        "chat_status": meta.get("chat_status", "unknown"),
        "used_prompts": meta.get("used_prompts", []),
        "image_count": len(imgs),
        "video_count": len(vids) + len(popups),
        "progress": _get_progress(int(batch_id)),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
    }


@app.delete("/api/history/{batch_id}")
def api_delete_history(batch_id: str):
    """删除指定批次的所有文件和元数据"""
    batch_dir = OUTPUT_ROOT / batch_id
    if not batch_dir.exists() or not batch_dir.is_dir():
        raise HTTPException(status_code=404, detail="批次不存在")

    # 统计要删除的内容
    pngs = sorted(batch_dir.glob("*.png"))
    mp4s = sorted(batch_dir.glob("*.mp4"))
    img_count = len(pngs)
    vid_count = len(mp4s)
    total_files = img_count + vid_count + 2  # + _meta.json + _progress.json

    try:
        # 删除整个批次目录
        shutil.rmtree(batch_dir, ignore_errors=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")

    # 清理运行中的进度缓存
    _deregister_batch(int(batch_id))

    return {
        "status": "deleted",
        "batch_id": batch_id,
        "images_deleted": img_count,
        "videos_deleted": vid_count,
        "total_files_deleted": total_files,
    }


@app.post("/api/history/batch-delete")
def api_batch_delete_history(body: Dict[str, List[str]]):
    """批量删除批次"""
    batch_ids = body.get("batch_ids", [])
    if not batch_ids:
        raise HTTPException(status_code=400, detail="未提供批次 ID")

    results = []
    for bid in batch_ids:
        try:
            batch_dir = OUTPUT_ROOT / bid
            if not batch_dir.exists():
                results.append({"batch_id": bid, "status": "skipped", "reason": "不存在"})
                continue
            pngs = sorted(batch_dir.glob("*.png"))
            mp4s = sorted(batch_dir.glob("*.mp4"))
            shutil.rmtree(batch_dir, ignore_errors=False)
            _deregister_batch(int(bid))
            results.append({"batch_id": bid, "status": "deleted", "images": len(pngs), "videos": len(mp4s)})
        except Exception as e:
            results.append({"batch_id": bid, "status": "failed", "reason": str(e)})

    return {"results": results}


# ====== 配置持久化 API ======

@app.get("/api/config")
def api_get_config():
    """返回保存的 API 配置"""
    return {
        "api_key": _app_config.get("api_key", ""),
        "api_url": _app_config.get("api_url", ""),
        "chat_model_name": _app_config.get("chat_model_name", ""),
        "image_model_name": _app_config.get("image_model_name", ""),
        "analysis_prompt": _app_config.get("analysis_prompt", ""),
        "concurrency": _app_config.get("concurrency", 2),
    }


class ConfigUpdateRequest(BaseModel):
    api_key: str = ""
    api_url: str = ""
    chat_model_name: str = ""
    image_model_name: str = ""
    analysis_prompt: str = ""
    concurrency: int = Field(default=2, ge=1, le=64)


@app.post("/api/config")
def api_save_config(body: ConfigUpdateRequest):
    """保存 API 配置"""
    global _app_config
    _app_config = {
        "api_key": body.api_key,
        "api_url": body.api_url,
        "chat_model_name": body.chat_model_name,
        "image_model_name": body.image_model_name,
        "analysis_prompt": body.analysis_prompt,
        "concurrency": body.concurrency,
    }
    _save_config(_app_config)
    return {"status": "ok", "message": "配置已保存"}


# ====== 数据看板 API ======

class ScraperLoginRequest(BaseModel):
    username: str
    password: str
    captcha: str = ""
    check_key: str = ""


@app.get("/api/scraper/captcha")
def api_scraper_captcha():
    """获取登录验证码，返回 data_uri 和 check_key"""
    data_uri, check_key, err = scraper.fetch_captcha()
    if err:
        raise HTTPException(status_code=502, detail=err)
    return {"image": data_uri, "check_key": check_key}


@app.post("/api/scraper/login")
def api_scraper_login(body: ScraperLoginRequest):
    """通过 API 直接登录，获取 token"""
    success, message = scraper.login_via_api(
        body.username, body.password,
        captcha=body.captcha, check_key=body.check_key
    )
    return {"status": "ok" if success else "failed", "message": message}


@app.post("/api/scraper/sync")
def api_scraper_sync():
    """触发数据同步"""
    result = scraper.run_full_sync()
    return result


@app.get("/api/scraper/session-status")
def api_session_status():
    """检查登录会话是否有效"""
    valid = scraper.check_session_valid()
    return {"valid": valid, "message": "会话有效" if valid else "会话已过期，请重新登录"}


@app.post("/api/scraper/logout")
def api_scraper_logout():
    """登出：清除 token"""
    scraper.do_logout()
    return {"status": "ok", "message": "已登出"}


@app.get("/api/scraper/sync-interval")
def api_get_sync_interval():
    """获取自动同步间隔（秒）"""
    secs = database.get_sync_interval()
    return {"seconds": secs, "text": _format_interval(secs)}


@app.post("/api/scraper/sync-interval")
def api_set_sync_interval(seconds: int = Query(default=180)):
    """设置自动同步间隔（秒），合法值：60/180/600/1800/3600"""
    allowed = {60, 180, 600, 1800, 3600}
    if seconds not in allowed:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "error", "message": "无效间隔，可选: 60/180/600/1800/3600"}, status_code=400)
    database.set_sync_interval(seconds)
    try:
        _scheduler.reschedule_job('auto_sync', trigger='interval', seconds=seconds)
    except Exception:
        pass
    return {"status": "ok", "seconds": seconds, "message": f"同步间隔已设为 {_format_interval(seconds)}"}


def _format_interval(secs: int) -> str:
    if secs < 120:
        return f"{secs}秒"
    elif secs < 3600:
        return f"{secs // 60}分钟"
    else:
        return f"{secs // 3600}小时"


@app.get("/api/dashboard/account-aliases")
def api_get_account_aliases():
    """获取账户别名列表"""
    return database.get_account_aliases()


@app.post("/api/dashboard/account-aliases")
def api_set_account_alias(account_id: str = Query(...), alias: str = Query(...)):
    """设置账户别名"""
    database.set_account_alias(account_id, alias)
    return {"status": "ok", "account_id": account_id, "alias": alias}


@app.delete("/api/dashboard/account-aliases")
def api_delete_account_alias(account_id: str = Query(...)):
    """删除账户别名"""
    database.delete_account_alias(account_id)
    return {"status": "ok", "account_id": account_id}


# ====== 小说管理 API ======

@app.get("/api/novels/list")
def api_novel_books_list(
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    keyword: str = Query(default=None),
    status: str = Query(default=None),
):
    """分页查询书籍列表"""
    return database.get_novel_books(page=page, page_size=page_size, keyword=keyword, status_filter=status)


@app.get("/api/novels/{novel_id}")
def api_novel_book_detail(novel_id: str):
    """查询书籍详情"""
    book = database.get_novel_book(novel_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    return book


@app.get("/api/novels/{novel_id}/chapters")
def api_novel_chapters_list(
    novel_id: str,
    page: int = Query(default=1),
    page_size: int = Query(default=50),
):
    """分页查询某书的章节列表"""
    return database.get_novel_chapters(novel_id, page=page, page_size=page_size)


@app.get("/api/novels/chapters/{chapter_id}")
def api_novel_chapter_detail(chapter_id: int):
    """查询单章内容"""
    chapter = database.get_novel_chapter(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


class SyncNovelContentBody(BaseModel):
    novel_id: str = ""


@app.post("/api/novels/sync-books")
def api_novel_sync_books():
    """手动触发书籍列表同步"""
    if not scraper.check_session_valid():
        raise HTTPException(status_code=401, detail="请先在数据看板登录")
    count, err = scraper.sync_novel_books()
    if err:
        return {"status": "ok", "count": count, "warning": err}
    return {"status": "ok", "count": count, "message": f"已同步 {count} 本书"}


@app.post("/api/novels/sync-content")
def api_novel_sync_content(body: SyncNovelContentBody = SyncNovelContentBody()):
    """手动触发章节内容同步"""
    if not scraper.check_session_valid():
        raise HTTPException(status_code=401, detail="请先在数据看板登录")
    result = scraper.sync_all_novel_content(novel_id=body.novel_id or None)
    return result


@app.get("/api/dashboard/summary")
def api_dashboard_summary(
    start: str = Query(default=None),
    end: str = Query(default=None),
    account: str = Query(default=None),
    keyword: str = Query(default=None),
):
    """KPI 汇总"""
    return analytics.get_summary(start_date=start, end_date=end, account=account, keyword=keyword)


@app.get("/api/dashboard/daily-stats")
def api_dashboard_daily_stats(
    start: str = Query(default=None),
    end: str = Query(default=None),
    account: str = Query(default=None),
    keyword: str = Query(default=None),
    order_by: str = Query(default="date"),
    page: int = Query(default=1),
    page_size: int = Query(default=20),
):
    """按日期+账户的日报明细"""
    return analytics.get_daily_stats(start_date=start, end_date=end, account=account, keyword=keyword,
                                      order_by=order_by, page=page, page_size=page_size)


@app.get("/api/dashboard/accounts")
def api_dashboard_accounts():
    """广告账户列表"""
    return analytics.get_accounts()


@app.get("/api/dashboard/trend")
def api_dashboard_trend(
    days: int = Query(default=30),
    account: str = Query(default=None),
    keyword: str = Query(default=None),
):
    """趋势数据"""
    return analytics.get_trend(days=days, account=account, keyword=keyword)


@app.get("/api/dashboard/orders")
def api_dashboard_orders(
    start: str = Query(default=None),
    end: str = Query(default=None),
    keyword: str = Query(default=None),
    page: int = Query(default=1),
    page_size: int = Query(default=15),
):
    """订单列表"""
    return analytics.get_orders(start_date=start, end_date=end, keyword=keyword, page=page, page_size=page_size)


@app.get("/api/dashboard/account-ranking")
def api_dashboard_account_ranking(
    start: str = Query(default=None),
    end: str = Query(default=None),
    keyword: str = Query(default=None),
    page: int = Query(default=1),
    page_size: int = Query(default=20),
):
    """账户排名"""
    return analytics.get_account_ranking(start_date=start, end_date=end, keyword=keyword, page=page, page_size=page_size)


@app.get("/api/dashboard/anomalies")
def api_dashboard_anomalies(days: int = Query(default=30)):
    """消耗异常检测"""
    return analytics.detect_anomalies(days=days)


@app.get("/api/dashboard/novel-stats")
def api_dashboard_novel_stats(
    start: str = Query(default=None),
    end: str = Query(default=None),
    keyword: str = Query(default=None),
):
    """小说订单汇总：按 novelId + novelName 分组统计"""
    return analytics.get_novel_stats(start_date=start, end_date=end, keyword=keyword)


app.mount("/static/output", StaticFiles(directory=str(OUTPUT_ROOT)), name="novel_output")
app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


@app.get("/")
def read_root():
    idx = STATIC_PATH / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"error": "缺少 static/index.html"}


@app.get("/api/generate/stream/{batch_id}")
async def api_stream_generation(batch_id: int):
    """SSE 端点：实时推送生成进度和已完成的图片"""
    q: queue.Queue = queue.Queue(maxsize=_MAX_SSE_QUEUE)

    with _SSE_LOCK:
        if batch_id not in _SSE_QUEUES:
            _SSE_QUEUES[batch_id] = []
        _SSE_QUEUES[batch_id].append(q)

    loop = asyncio.get_running_loop()

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        loop.run_in_executor(None, q.get), timeout=35
                    )
                    yield {"event": data["type"], "data": json.dumps(data, ensure_ascii=False)}
                    if data.get("status") in ("success", "failed", "cancelled", "partial"):
                        break
                except (asyncio.TimeoutError, Exception):
                    yield {"event": "ping", "data": "{}"}
        finally:
            with _SSE_LOCK:
                queues = _SSE_QUEUES.get(batch_id, [])
                if q in queues:
                    queues.remove(q)
                if not queues:
                    _SSE_QUEUES.pop(batch_id, None)

    return EventSourceResponse(event_generator())


# ====== 小说内容获取 API ======

class FetchNovelRequest(BaseModel):
    novel_id: str = ""


# ====== 从小说分析结果异步生成图片 API ======

class AnalysisGenerateRequest(BaseModel):
    api_key: str = ""
    api_url: str = ""
    image_model_name: str = ""
    novel_id: str = ""            # 小说ID（通过ID获取时传入）
    text_single_prompts: List[dict] = []
    text_single_text_enabled: bool = True
    lr_split_prompts: List[dict] = []
    lr_split_text_enabled: bool = True
    tb_split_prompts: List[dict] = []
    tb_split_text_enabled: bool = True
    concurrency: int = 2


def _run_analysis_generation(body: AnalysisGenerateRequest, batch_id: int) -> dict:
    """后台线程：从已分析的提示词直接生成图片（跳过 Chat API）"""
    warnings: list = []
    errors: list = []
    generated_images: list = []
    used_prompts: list = []
    image_seq = [0]  # mutable counter for thread-safe increment

    batch_dir = OUTPUT_ROOT / str(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    _register_batch(batch_id)
    _update_progress(batch_id, 0, "开始生成图片...", "running")

    def _fetch_image(prompt: str, size: str, label: str) -> Optional[str]:
        nonlocal image_seq
        if _is_batch_cancelled(batch_id):
            return None
        headers = {"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"}
        url = body.api_url.rstrip("/") + "/images/generations"
        clean_prompt = (prompt or "").strip()
        # 合规过滤
        for banned, safe in _COMPLIANCE_MAP.items():
            if banned in clean_prompt:
                clean_prompt = clean_prompt.replace(banned, safe)
        payload = {"model": body.image_model_name, "prompt": clean_prompt, "size": size, "n": 1}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            if r.status_code >= 400:
                errors.append(f"{label} HTTP {r.status_code}: {_api_error_snippet(r)}")
                return None
            j = r.json()
            data = j.get("data", [])
            if not data:
                errors.append(f"{label} 无返回数据")
                return None
            item = data[0]
            img_bytes = None
            if isinstance(item, dict):
                if item.get("url"):
                    ir = requests.get(item["url"], timeout=120)
                    img_bytes = ir.content if ir.status_code < 400 else None
                elif item.get("b64_json"):
                    img_bytes = base64.b64decode(item["b64_json"])
            if not img_bytes:
                errors.append(f"{label} 无法获取图片数据")
                return None
            fname = f"{batch_id}-{image_seq[0] + 1}.png"
            with open(batch_dir / fname, "wb") as f:
                f.write(img_bytes)
            image_seq[0] += 1
            return fname
        except Exception as e:
            errors.append(f"{label} {e}")
            return None

    # Build square jobs
    square_jobs: list = []
    for i, item in enumerate(body.text_single_prompts):
        square_jobs.append(("text_single", i, f"单帧图{i+1}", item))
    for i, item in enumerate(body.lr_split_prompts):
        square_jobs.append(("lr", i, f"左右分屏{i+1}", item))
    for i, item in enumerate(body.tb_split_prompts):
        square_jobs.append(("tb", i, f"上下分屏{i+1}", item))

    total = len(square_jobs)
    if total == 0:
        _update_progress(batch_id, 100, "无图片需要生成", "success")
        _deregister_batch(batch_id)
        return {"status": "success", "batch_id": batch_id, "images": [], "videos": []}

    def _do_square_job(kind: str, idx: int, lab: str, item: dict):
        if _is_batch_cancelled(batch_id):
            return None
        core = item.get("image_prompt", "")
        final_p = finalize_square_prompt(kind, core, "")
        fname = _fetch_image(final_p, "1024x1024", lab)
        if fname:
            prompt_dict = {"label": lab, "type": kind, "prompt": final_p}
            fpath = batch_dir / fname
            tb = item.get("text_bottom", "")
            tl = item.get("text_left", "")
            tr = item.get("text_right", "")
            to = item.get("text_overlay", "")
            txt_on = (
                body.text_single_text_enabled if kind == "text_single"
                else body.lr_split_text_enabled if kind == "lr"
                else body.tb_split_text_enabled
            )
            if txt_on and (tb or tl or tr or to):
                try:
                    composite_text_on_image(fpath, text_bottom=tb, text_left=tl, text_right=tr, text_overlay=to)
                except Exception as e:
                    warnings.append(f"{lab} 文字合成失败：{e}")
            generated_images.append(f"/static/output/{batch_id}/{fname}")
            _push_image_ready(batch_id, f"/static/output/{batch_id}/{fname}", lab)
            return prompt_dict
        return None

    concurrency = max(1, min(body.concurrency or 2, 8))
    completed = 0

    if concurrency > 1 and total > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(_do_square_job, k, i, l, it): (k, i, l) for k, i, l, it in square_jobs}
            for future in as_completed(futures):
                if _is_batch_cancelled(batch_id):
                    break
                try:
                    pd = future.result()
                    if pd:
                        used_prompts.append(pd)
                except Exception as e:
                    errors.append(f"生成异常: {e}")
                completed += 1
                pct = int(completed / total * 90)
                _update_progress(batch_id, pct, f"生成图片 {completed}/{total}...", "running")
    else:
        for kind, idx, lab, item in square_jobs:
            if _is_batch_cancelled(batch_id):
                break
            pd = _do_square_job(kind, idx, lab, item)
            if pd:
                used_prompts.append(pd)
            completed += 1
            pct = int(completed / total * 90)
            _update_progress(batch_id, pct, f"生成图片 {completed}/{total}...", "running")

    got = len(generated_images)
    if got == 0 and total > 0:
        status, message = "failed", "出图全部失败。"
    elif got < total:
        status, message = "partial", f"图片完成 {got}/{total}。"
    else:
        status, message = "success", "任务完成。"

    _update_progress(batch_id, 100, message, status)
    _deregister_batch(batch_id)

    result = {
        "status": status,
        "batch_id": batch_id,
        "message": message,
        "novel_id": body.novel_id if hasattr(body, 'novel_id') else "",
        "images": generated_images,
        "videos": [],
        "errors": errors,
        "warnings": warnings,
        "used_prompts": used_prompts,
        "chat_status": "skipped",
    }
    _save_batch_meta(result)
    return result


@app.post("/api/fetch-novel")
def api_fetch_novel(body: FetchNovelRequest):
    """代理获取小说章节内容（HTML → 纯文本）"""
    if not body.novel_id.strip():
        raise HTTPException(status_code=400, detail="缺少小说ID")
    result = _fetch_novel_content(body.novel_id.strip())
    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/generate-from-analysis")
def api_generate_from_analysis(body: AnalysisGenerateRequest):
    """从小说分析结果异步生成图片"""
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="缺少 API Key")
    batch_id = allocate_batch_id(OUTPUT_ROOT)
    (OUTPUT_ROOT / str(batch_id)).mkdir(parents=True, exist_ok=True)
    _register_batch(batch_id)
    _update_progress(batch_id, 0, "任务已提交，正在启动...", "running")
    _EXECUTOR.submit(_run_analysis_generation, body, batch_id)
    return {
        "status": "submitted",
        "batch_id": batch_id,
        "message": f"任务 #{batch_id} 已提交到后台处理",
    }


# ====== 从提示词直接生成图片 API ======

class PromptGenerateRequest(BaseModel):
    api_key: str = ""
    api_url: str = ""
    image_model_name: str = ""
    prompts: List[str] = []
    sizes: List[str] = []
    with_text: bool = False
    text_bottom_list: List[str] = []
    text_left_list: List[str] = []
    text_right_list: List[str] = []

@app.post("/api/generate-from-prompts")
def api_generate_from_prompts(body: PromptGenerateRequest):
    """根据提示词直接生成图片，同步返回结果"""
    batch_id = allocate_batch_id(OUTPUT_ROOT)
    batch_dir = OUTPUT_ROOT / str(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    if not body.api_key.strip() or not body.prompts:
        raise HTTPException(status_code=400, detail="缺少 API Key 或提示词")

    headers = {"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"}
    base_url = body.api_url.rstrip("/") + "/images/generations"

    results = []
    errors = []
    used_prompts = []

    for i, prompt in enumerate(body.prompts):
        if not prompt.strip():
            continue
        size = body.sizes[i] if i < len(body.sizes) else "1024x1024"
        clean_prompt = prompt.strip()

        payload = {"model": body.image_model_name, "prompt": clean_prompt, "size": size, "n": 1}
        try:
            r = requests.post(base_url, json=payload, headers=headers, timeout=120)
            if r.status_code >= 400:
                errors.append(f"第{i+1}张 HTTP {r.status_code}: {_api_error_snippet(r)}")
                continue
            j = r.json()
            data = j.get("data", [])
            if not data:
                errors.append(f"第{i+1}张无返回数据")
                continue
            item = data[0]
            img_bytes = None
            if isinstance(item, dict):
                if item.get("url"):
                    ir = requests.get(item["url"], timeout=120)
                    img_bytes = ir.content if ir.status_code < 400 else None
                elif item.get("b64_json"):
                    img_bytes = base64.b64decode(item["b64_json"])
            if not img_bytes:
                errors.append(f"第{i+1}张无法获取图片数据")
                continue

            fname = f"{batch_id}-{i+1}.png"
            fpath = batch_dir / fname
            with open(fpath, "wb") as f:
                f.write(img_bytes)

            if body.with_text:
                tb = body.text_bottom_list[i] if i < len(body.text_bottom_list) else ""
                tl = body.text_left_list[i] if i < len(body.text_left_list) else ""
                tr = body.text_right_list[i] if i < len(body.text_right_list) else ""
                if tb or tl or tr:
                    try:
                        composite_text_on_image(fpath, text_bottom=tb, text_left=tl, text_right=tr)
                    except Exception as e:
                        errors.append(f"第{i+1}张文字合成失败: {e}")

            results.append(f"/static/output/{batch_id}/{fname}")
            used_prompts.append({"label": f"提示词{i+1}", "type": "prompt", "prompt": prompt.strip()})
        except Exception as e:
            errors.append(f"第{i+1}张异常: {e}")

    meta = {
        "batch_id": batch_id,
        "status": "success" if results else "failed",
        "message": f"生成 {len(results)}/{len(body.prompts)} 张",
        "images": results,
        "videos": [],
        "errors": errors,
        "used_prompts": used_prompts,
        "chat_status": "skipped",
    }
    _save_batch_meta(meta)
    return meta


# ====== 小说分析 API ======

class AnalyzeNovelRequest(BaseModel):
    api_key: str = ""
    api_url: str = ""
    chat_model_name: str = ""
    novel_content: str = ""
    analysis_prompt: str = ""
    text_single_count: int = 0
    lr_split_count: int = 0
    tb_split_count: int = 0
    scroll_count: int = 0
    popup_count: int = 0
    use_templates: bool = False

@app.post("/api/analyze-novel")
def api_analyze_novel(body: AnalyzeNovelRequest):
    """分析小说内容，返回生成的图片提示词（不实际生成图片）"""
    if not body.api_key.strip() or not body.novel_content.strip():
        raise HTTPException(status_code=400, detail="缺少 API Key 或小说内容")

    # 用户提示词优先
    if body.analysis_prompt.strip():
        analysis_rules = body.analysis_prompt.strip()
    else:
        analysis_rules = _build_rules_text("", body.text_single_count, body.lr_split_count, body.tb_split_count, 0)

    scroll_total = body.scroll_count + body.popup_count
    n_square = body.text_single_count + body.lr_split_count + body.tb_split_count

    system = SYSTEM_PROMPT_TEMPLATE.format(
        text_single_count=body.text_single_count,
        scroll_visual_count=scroll_total,
        lr_split_count=body.lr_split_count,
        tb_split_count=body.tb_split_count,
        n_square=max(n_square, 1),
    )

    user_msg = (
        f"绘图规则：\n{analysis_rules}\n\n"
        f"小说节选：\n{body.novel_content[:5000]}\n\n"
        f"数量：text_single={body.text_single_count}, lr={body.lr_split_count}, tb={body.tb_split_count}, scroll={scroll_total}"
        f"\n\n【重要】只输出 JSON，不要生成图片。"
    )

    headers = {"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"}
    url = body.api_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": body.chat_model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.85,
        "max_tokens": 8192,
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=300)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Chat API 错误: {r.text[:500]}")
        raw = r.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        data = json.loads(raw)
        return {"status": "success", "data": data}
    except json.JSONDecodeError as e:
        return {"status": "failed", "error": f"JSON 解析失败: {e}", "raw": raw[:500]}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ---- Meta 账户管理 API ----

class MetaAccountBody(BaseModel):
    act_id: str
    act_name: str = ""
    access_token: str = ""
    pingykj_account: str = ""

@app.get("/api/meta/accounts")
def _get_meta_accounts():
    return database.get_meta_accounts()

@app.post("/api/meta/accounts")
def _add_meta_account(body: MetaAccountBody):
    database.upsert_meta_account(
        body.act_id, body.act_name, body.access_token, body.pingykj_account
    )
    return {"success": True}

@app.put("/api/meta/accounts/{act_id}")
def _update_meta_account(act_id: str, body: MetaAccountBody):
    database.upsert_meta_account(
        act_id, body.act_name, body.access_token, body.pingykj_account
    )
    return {"success": True}

@app.delete("/api/meta/accounts/{act_id}")
def _delete_meta_account(act_id: str):
    database.delete_meta_account(act_id)
    return {"success": True}

class TokenRefreshBody(BaseModel):
    access_token: str

@app.post("/api/meta/accounts/{act_id}/refresh-token")
def _refresh_meta_token(act_id: str, body: TokenRefreshBody):
    database.update_meta_token(act_id, body.access_token)
    return {"success": True}

# ---- Meta 数据同步控制 API ----

@app.post("/api/meta/sync")
def _trigger_meta_sync():
    result = scraper.sync_all_meta_insights()
    return result

@app.get("/api/meta/sync-status")
def _meta_sync_status():
    accounts = database.get_meta_accounts()
    active = [a for a in accounts if a.get("status") == "active"]
    return {
        "total_accounts": len(accounts),
        "active_accounts": len(active),
        "accounts": [
            {
                "act_id": a["act_id"],
                "act_name": a["act_name"],
                "last_sync": database.get_meta_sync_state(a["act_id"]),
            }
            for a in active
        ]
    }

@app.get("/api/meta/sync-interval")
def _get_meta_sync_interval():
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return {"interval": config.get("meta", {}).get("sync_interval_seconds", 300)}

@app.post("/api/meta/sync-interval")
async def _set_meta_sync_interval(request: Request):
    body = await request.json()
    seconds = int(body.get("interval", 300))
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("meta", {})["sync_interval_seconds"] = seconds
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "interval": seconds}


# ---- 投放模板管理 API ----

@app.get("/api/delivery/templates")
def _get_delivery_templates():
    return database.get_delivery_templates()

class CreateTemplateBody(BaseModel):
    name: str
    targeting: dict = {}
    placements: dict = {}
    budget_type: str = "daily_budget"
    budget_value: int = 0
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    billing_event: str = "IMPRESSIONS"
    conversion_event: str = ""
    ad_account_id: str = ""

@app.post("/api/delivery/templates")
def _create_delivery_template(body: CreateTemplateBody):
    tid = database.create_delivery_template(body.model_dump())
    return {"success": True, "id": tid}

@app.put("/api/delivery/templates/{template_id}")
async def _update_delivery_template(template_id: int, request: Request):
    body = await request.json()
    database.update_delivery_template(template_id, body)
    return {"success": True}

@app.delete("/api/delivery/templates/{template_id}")
def _delete_delivery_template(template_id: int):
    database.delete_delivery_template(template_id)
    return {"success": True}

@app.get("/api/delivery/templates/fb-adsets/{account_id}")
def _get_fb_adsets(account_id: str):
    token = None
    account = database.get_meta_account(account_id)
    if account:
        token = account.get("access_token")
    if not token:
        raise HTTPException(400, "未找到该账户的 access token")
    adsets, err = meta_api.get_adsets(account_id, token)
    if err:
        raise HTTPException(400, err)
    return {"data": adsets or []}

class ImportTemplateBody(BaseModel):
    account_id: str
    adset_id: str
    name: str = ""

@app.post("/api/delivery/templates/import")
def _import_template_from_fb(body: ImportTemplateBody):
    token = None
    account = database.get_meta_account(body.account_id)
    if account:
        token = account.get("access_token")
    if not token:
        raise HTTPException(400, "未找到该账户的 access token")

    adsets, err = meta_api.get_adsets(body.account_id, token)
    if err:
        raise HTTPException(400, err)

    target_adset = None
    for a in (adsets or []):
        if a.get("id") == body.adset_id:
            target_adset = a
            break
    if not target_adset:
        raise HTTPException(404, "未找到指定 AdSet")

    tname = body.name or f"导入:{target_adset.get('name', '')}"
    tid = database.create_delivery_template({
        "name": tname,
        "source": "imported_from_fb",
        "source_adset_id": body.adset_id,
        "targeting": target_adset.get("targeting", {}),
        "placements": {},
        "budget_type": "daily_budget" if target_adset.get("daily_budget") else "lifetime_budget",
        "budget_value": target_adset.get("daily_budget") or target_adset.get("lifetime_budget") or 0,
        "bid_strategy": target_adset.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
        "optimization_goal": target_adset.get("optimization_goal", "OFFSITE_CONVERSIONS"),
        "billing_event": target_adset.get("billing_event", "IMPRESSIONS"),
        "conversion_event": (
            target_adset.get("promoted_object", {}).get("custom_event_type", "")
            if target_adset.get("promoted_object") else ""
        ),
        "ad_account_id": body.account_id,
    })
    return {"success": True, "id": tid}

# ---- 投放队列管理 API ----

class AddToQueueBody(BaseModel):
    items: list

@app.post("/api/delivery/queue")
def _add_to_delivery_queue(body: AddToQueueBody):
    count = database.add_to_delivery_queue(body.items)
    return {"success": True, "count": count}

@app.get("/api/delivery/queue")
def _get_delivery_queue(page: int = 1, page_size: int = 20, status: str = None):
    return database.get_delivery_queue(page, page_size, status)

@app.post("/api/delivery/queue/{queue_id}/approve")
async def _approve_queue_item(queue_id: int, request: Request):
    body = await request.json()
    template_id = body.get("template_id", 0)
    reviewer = body.get("reviewer", "")
    database.update_queue_status(queue_id, "approved", reviewer=reviewer)
    if template_id:
        database.batch_approve_queue([queue_id], template_id, reviewer)
    return {"success": True}

@app.post("/api/delivery/queue/{queue_id}/reject")
async def _reject_queue_item(queue_id: int, request: Request):
    body = await request.json()
    reviewer = body.get("reviewer", "")
    database.update_queue_status(queue_id, "rejected", reviewer=reviewer)
    return {"success": True}

class BatchApproveBody(BaseModel):
    ids: list
    template_id: int
    reviewer: str = ""

@app.post("/api/delivery/queue/batch-approve")
def _batch_approve_queue(body: BatchApproveBody):
    database.batch_approve_queue(body.ids, body.template_id, body.reviewer)
    return {"success": True}

class SubmitDeliveryBody(BaseModel):
    queue_ids: list
    template_id: int

@app.post("/api/delivery/submit")
def _submit_delivery(body: SubmitDeliveryBody):
    batch_id, err = delivery.submit_delivery_batch(body.queue_ids, body.template_id)
    if err:
        raise HTTPException(400, err)
    return {"success": True, "batch_id": batch_id}

@app.get("/api/delivery/progress/{batch_id}")
def _delivery_progress(batch_id: str):
    return delivery.get_delivery_progress(batch_id)

@app.get("/api/delivery/stream/{batch_id}")
async def _delivery_stream(batch_id: str):
    import asyncio
    import threading

    local_queue = asyncio.Queue()

    def _poll():
        last_idx = 0
        while True:
            events = delivery._delivery_queues.get(batch_id, [])
            for e in events[last_idx:]:
                local_queue.put_nowait(e)
                last_idx += 1
            if delivery._delivery_events.get(batch_id, threading.Event()).is_set():
                for e in events[last_idx:]:
                    local_queue.put_nowait(e)
                break
            time.sleep(0.5)

    threading.Thread(target=_poll, daemon=True).start()

    async def _event_generator():
        while True:
            try:
                event = await asyncio.wait_for(local_queue.get(), timeout=1.0)
                yield {"event": event["type"], "data": json.dumps(event)}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                if delivery._delivery_events.get(batch_id, threading.Event()).is_set():
                    break

    return EventSourceResponse(_event_generator())

@app.get("/api/delivery/records")
def _get_delivery_records(page: int = 1, page_size: int = 20, status: str = None):
    return database.get_delivery_records(page, page_size, status)


if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("【素材工厂服务已启动】")
    print("本地访问: http://127.0.0.1:8000/static/index.html")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)