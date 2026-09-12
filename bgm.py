# -*- coding: utf-8 -*-
"""背景音乐：本地合成保底 + 下载 CC0 真录音。

为什么要合成：原来合成视频的背景音要用户自己往 `音乐/` 放 MP4，不放就没有背景音。
合成这条路什么都不用搜集、离线能跑、不存在版权问题，作为「兜底」常驻。
你往 `音乐/` 放了真歌（自己下的或用 download_cc0 下的）就优先用真歌，合成乐自动让位。

音色说明：合成的是舒缓的和弦垫 + 稀疏轻钢琴点缀，音量压得很低当垫底用；
它不追求好听，追求「一直有、不出事、不侵权」。
"""
import json
import random
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

SR = 32000                 # 采样率：垫乐够用，文件小
BGM_TRACK_COUNT = 12       # 合成保底曲目数（约 11MB）
SYNTH_PREFIX = "bgm_"      # 合成曲的文件名前缀（识别用的，别手动改）
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4")

# 各调的和弦进行（半音偏移，相对于根音）。都是听着不闹的走向。
_PROGRESSIONS = [
    [0, 9, 5, 7],      # I - vi - IV - V
    [0, 7, 9, 5],      # I - V - vi - IV
    [9, 5, 0, 7],      # vi - IV - I - V
    [0, 5, 9, 7],      # I - IV - vi - V
]
_ROOTS = [57, 60, 62, 64, 65, 67]        # A3 C4 D4 E4 F4 G4
_CHORD_TONES = [[0, 4, 7, 11], [0, 3, 7, 10], [0, 4, 7, 14], [0, 3, 7, 14]]  # maj7/min7/加九


def _midi_to_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _adsr(n: int, attack: float, release: float) -> np.ndarray:
    """长起音长释放的包络 —— 舒缓感的来源，别用硬起音。"""
    env = np.ones(n, dtype=np.float32)
    a = min(int(attack * SR), n // 2)
    r = min(int(release * SR), n // 2)
    if a > 0:
        env[:a] = np.linspace(0, 1, a, dtype=np.float32) ** 1.6
    if r > 0:
        env[-r:] = np.linspace(1, 0, r, dtype=np.float32) ** 1.6
    return env


def _pad(notes: List[float], dur: float) -> np.ndarray:
    """和弦垫：每个音叠三个略微失谐的正弦，出来是柔和的合唱感"""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    out = np.zeros(n, dtype=np.float32)
    for note in notes:
        f = _midi_to_hz(note)
        for det in (0.997, 1.0, 1.003):
            out += np.sin(2 * np.pi * f * det * t, dtype=np.float32) * 0.05
    return out * _adsr(n, 1.5, 2.0)


def _pluck(note: float, dur: float) -> np.ndarray:
    """轻钢琴式的点缀：正弦加一点二次谐波，指数衰减"""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = _midi_to_hz(note)
    env = np.exp(-t * 2.2).astype(np.float32)
    return (np.sin(2 * np.pi * f * t, dtype=np.float32) +
            0.25 * np.sin(2 * np.pi * 2 * f * t, dtype=np.float32)) * env * 0.09


def _reverb(x: np.ndarray) -> np.ndarray:
    """多抽头延迟当混响：比卷积便宜得多，垫乐够用"""
    out = x.copy()
    for delay_ms, gain in ((61, 0.32), (97, 0.24), (151, 0.17), (233, 0.11)):
        d = int(delay_ms * SR / 1000)
        if d < len(x):
            out[d:] += x[:-d] * gain
    return out


def _lowpass(x: np.ndarray) -> np.ndarray:
    """滑动平均当低通：削掉高频毛刺，听着更"暖"。

    （真一阶 IIR 要逐样本递归，纯 Python 太慢；垫乐本来就是低频为主，均值滤波够用。）
    """
    k = max(1, int(SR / 8000))
    if k <= 1:
        return x
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def synthesize_track(seconds: float = 75.0, seed: int = 0) -> np.ndarray:
    """合成一首舒缓的垫乐。返回 float32 单声道，峰值归一化到 0.9。"""
    rng = random.Random(seed)
    root = rng.choice(_ROOTS)
    prog = rng.choice(_PROGRESSIONS)
    tones = rng.choice(_CHORD_TONES)
    bar = rng.choice([6.0, 7.5, 8.0])            # 每个和弦的时长
    total = int(seconds * SR)
    out = np.zeros(total + SR * 4, dtype=np.float32)   # 留出尾巴给混响

    pos = 0.0
    while pos < seconds:
        degree = prog[int(pos / bar) % len(prog)]
        notes = [_midi_to_hz(root + degree + iv) for iv in tones]
        notes_midi = [root + degree + iv for iv in tones]
        dur = min(bar + 2.0, seconds - pos + 2.0)      # 相邻和弦重叠一点，衔接不断
        if dur <= 0:
            break
        start = int(pos * SR)
        seg = _pad(notes_midi, dur)
        out[start:start + len(seg)] += seg[:max(0, len(out) - start)]
        # 低八度根音铺底，增加厚度
        bass = _pad([root + degree - 12], dur) * 0.8
        out[start:start + len(bass)] += bass[:max(0, len(out) - start)]
        # 稀疏点缀：每小节随机 1-2 个音
        for _ in range(rng.randint(1, 2)):
            off = pos + rng.uniform(0.5, bar - 1.0)
            note = rng.choice(notes_midi) + rng.choice([0, 12])
            pl = _pluck(note, 2.5)
            s = int(off * SR)
            if 0 <= s < len(out):
                out[s:s + len(pl)] += pl[:max(0, len(out) - s)]
        pos += bar

    out = _lowpass(_reverb(out))
    out = out[:total]

    # 首尾各淡入淡出 2 秒（配上 -stream_loop 循环也不会有爆音）
    fade = int(2.0 * SR)
    out[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)

    peak = float(np.max(np.abs(out))) or 1.0
    return (out * (0.9 / peak)).astype(np.float32)


def _write_m4a(samples: np.ndarray, dest: Path) -> bool:
    """float32 单声道 → 16bit WAV → ffmpeg 转 m4a（75 秒约 0.9MB，别存 WAV）"""
    import imageio_ffmpeg
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2")
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(wav_path),
                            "-c:a", "aac", "-b:a", "96k", str(dest)],
                           capture_output=True, timeout=180)
        return r.returncode == 0 and dest.exists() and dest.stat().st_size > 1024
    finally:
        wav_path.unlink(missing_ok=True)


def list_tracks(music_dir: Path) -> List[Path]:
    """音乐目录里的可用曲子（含合成与真录音）"""
    if not music_dir.exists():
        return []
    return sorted(f for f in music_dir.iterdir()
                  if f.is_file() and f.suffix.lower() in AUDIO_EXTS and f.stat().st_size > 1024)


def real_tracks(music_dir: Path) -> List[Path]:
    """真录音（非合成）—— 有真歌时优先用真歌"""
    return [t for t in list_tracks(music_dir) if not t.name.startswith(SYNTH_PREFIX)]


def ensure_library(music_dir: Path, count: int = 12, force: bool = False) -> int:
    """没有曲子就现场合成一批。幂等，返回新生成的曲数。

    只认合成曲：music_dir 里只要有任何音频（哪怕只有真人放的 1 首）都不打扰。
    """
    music_dir.mkdir(parents=True, exist_ok=True)
    existing = list_tracks(music_dir)
    if existing and not force:
        return 0
    if force:
        for old in music_dir.glob(f"{SYNTH_PREFIX}*"):
            old.unlink(missing_ok=True)
    n = 0
    for i in range(count):
        dest = music_dir / f"{SYNTH_PREFIX}{i + 1:02d}.m4a"
        try:
            if dest.exists() and not force:
                continue
            if _write_m4a(synthesize_track(seed=1000 + i), dest):
                n += 1
        except Exception as e:
            print(f"[BGM] 合成 {dest.name} 失败: {e}")
    if n:
        print(f"[BGM] 已合成 {n} 首背景乐到 {music_dir}")
    return n


# ====== 下载 CC0 真录音 ======

_OPENVERSE = "https://api.openverse.org/v1/audio/"
# 要英文的「歌」而不是纯音乐，主题偏爱情/情感
_MOOD_QUERIES = ["love song", "romantic song english", "emotional ballad", "sad love song",
                 "heartbreak song", "love ballad acoustic"]
# 只收 CC0：by-nc-nd 之类禁止商用/禁止改编，放广告里是踩线
_OK_LICENSES = {"cc0", "pdm"}

# Openverse 的 CC0 池子里大部分是 freesound 的音效/实地录音（森林、海浪、风铃），
# 不是音乐。当广告背景音得是曲子，所以按标题筛一遍：必须像音乐，且不像音效。
_MUSIC_HINTS = ("piano", "guitar", "music", "melody", "chord", "lofi", "lo-fi",
                "chill", "jazz", "strings", "song", "theme", "beat", "acoustic", "orchestral",
                "ambient music", "soundtrack",
                # 现在搜的是英文情歌，这些词本身就是「是首歌」的信号
                "ballad", "vocal", "love", "romantic", "singer", "duet", "lyric")
_NOISE_HINTS = ("sound", "noise", "rain", "wind", "ocean", "wave", "breeze", "field",
                "recording", "forest", "forrest", "thunder", "bird", "storm", "foley", "sfx",
                "effect", "sample pack", "atmosphere", "texture", "drone", "loop kit")


def is_music_like(title: str) -> bool:
    """标题看着像一首曲子（而不是雨声/海浪/风铃这类音效，也不是纯音乐/伴奏）。

    这条判错就会把音效或纯伴奏当背景乐塞进广告里，所以抽出来单独测。
    """
    t = (title or "").lower()
    if not t:
        return False
    if any(k in t for k in _NOISE_HINTS):
        return False
    if any(k in t for k in _INSTRUMENTAL_HINTS):   # 用户明确要英文歌，不要纯音乐
        return False
    return any(k in t for k in _MUSIC_HINTS)


_CALM_HINTS = ("calm", "soft", "relax", "gentle", "slow", "peace", "quiet", "tender", "warm")
# 爱情/情感主题（要的就是这个调性）
_LOVE_HINTS = ("love", "heart", "romance", "romantic", "baby", "forever", "miss you", "kiss",
               "darling", "sweetheart", "together", "valentine")
_EMOTION_HINTS = ("emotional", "sad", "tear", "cry", "broken", "lonely", "memory", "feel",
                  "longing", "goodbye", "melancholy", "ballad")
# 有人声的迹象
_VOCAL_HINTS = ("song", "vocal", "sing", "lyrics", "voice", "feat", "choir", "acoustic version")
# 明确是纯音乐/伴奏的，直接排除（用户要英文歌，不要纯音乐）
_INSTRUMENTAL_HINTS = ("instrumental", "karaoke", "backing track", "no vocal", "piano solo",
                       "type beat", "pure music", "bgm only",
                       # CC0 池子里大量是「人声片段/采风录音」，不是完整的歌
                       "vocals only", "vocal only", "acapella", "a cappella", "stem",
                       "snippet", "jingle", "folk song of", "field recording", "demo")
_ENERGETIC_HINTS = ("funky", "dance", "drum", "upbeat", "energetic", "rock", "metal", "techno",
                    "house", "party", "remix", "epic battle", "hip hop", "rap")


def song_score(title: str) -> int:
    """越贴近「英文情歌、有人声、舒缓」分越高 —— 用来在候选里排序。

    veto 纯音乐（_INSTRUMENTAL_HINTS）在 is_music_like 里做，这里只排优先级。
    """
    t = (title or "").lower()
    return (2 * sum(1 for k in _VOCAL_HINTS if k in t)
            + 2 * sum(1 for k in _LOVE_HINTS if k in t)
            + sum(1 for k in _EMOTION_HINTS if k in t)
            + sum(1 for k in _CALM_HINTS if k in t)
            - 2 * sum(1 for k in _ENERGETIC_HINTS if k in t))


def calm_score(title: str) -> int:
    """旧名字，等价于 song_score（保留兼容）"""
    return song_score(title)


def _clean_name(title: str) -> str:
    """把标题当文件名：去掉已有的音频后缀（免得 xxx.mp3.mp3），并清掉各系统的非法字符。

    标题里常带 ? ' : 这类字符 —— Linux 存得下，Windows 会直接报 WinError 123，
    本地开发机上整个下载就断了。
    """
    name = title or "untitled"
    for ext in AUDIO_EXTS:
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = "".join(c for c in name if ord(c) >= 32).strip(" .")
    return name[:48] or "untitled"


def _curl_json(url: str, proxy: str = "") -> Optional[Dict]:
    cmd = ["curl", "-s", "--max-time", "40", "-A", "NovelAdFactory/1.0"]
    if proxy:
        cmd += ["-x", proxy]
    try:
        r = subprocess.run(cmd + [url], capture_output=True, timeout=60)
        return json.loads(r.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None


def split_downloadable(results: List[Dict], want: int) -> List[Dict]:
    """从 Openverse 结果里挑出「能直接下载、且授权允许商用」的曲子。

    抽成独立函数是为了能单测 —— 授权判断错了会把非商用的曲子放进广告里。
    """
    picked, seen = [], set()
    for it in results:
        if len(picked) >= want:
            break
        lic = (it.get("license") or "").lower()
        url = it.get("url") or ""
        if lic not in _OK_LICENSES or not url.startswith("http"):
            continue
        if not is_music_like(it.get("title") or ""):
            continue
        if url in seen:
            continue
        seen.add(url)
        picked.append({"title": it.get("title") or "untitled", "url": url,
                       "license": lic, "creator": it.get("creator") or ""})
    # 越像「英文情歌、有人声、舒缓」的排越前（同分保持原顺序）
    picked.sort(key=lambda x: song_score(x["title"]), reverse=True)
    return picked


def download_cc0(music_dir: Path, want: int = 12, proxy: str = "") -> Tuple[int, List[Dict], List[str]]:
    """从 Openverse 下 CC0 曲子到音乐目录。返回 (成功数, 已下载信息, 错误信息)。"""
    music_dir.mkdir(parents=True, exist_ok=True)
    got, downloaded, errors = 0, [], []
    have = {t.stem for t in list_tracks(music_dir)}
    for q in _MOOD_QUERIES:
        if got >= want:
            break
        data = _curl_json(f"{_OPENVERSE}?license=cc0&q={q.replace(' ', '%20')}&page_size=20", proxy)
        if not data:
            errors.append(f"{q}: 接口无响应（服务器可能连不上 openverse.org）")
            continue
        picked = split_downloadable(data.get("results") or [], want - got)
        if not picked:
            errors.append(f"{q}: 没有可用的 CC0 结果")
            continue
        for it in picked:
            dest = music_dir / f"{_clean_name(it['title'])}.mp3"
            if dest.stem in have:
                continue
            cmd = ["curl", "-s", "-L", "--max-time", "120", "-o", str(dest), it["url"]]
            if proxy:
                cmd[3:3] = ["-x", proxy]
            try:
                subprocess.run(cmd, capture_output=True, timeout=150)
            except Exception as e:
                errors.append(f"{it['title']}: {e}")
                continue
            ok = False
            try:
                ok = dest.exists() and dest.stat().st_size > 10240
            except OSError:
                ok = False
            if ok:
                have.add(dest.stem)
                got += 1
                it["file"] = dest.name
                downloaded.append(it)
            else:
                try:
                    dest.unlink(missing_ok=True)      # 别因为删不掉半个文件把整轮下载带崩
                except OSError:
                    pass
                errors.append(f"{it['title']}: 下载失败")
    return got, downloaded, errors
