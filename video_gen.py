# -*- coding: utf-8 -*-
"""独立视频生成模块：小说原文 → LLM 镜头分解脚本 → 视频模型逐镜头生成 → ffmpeg 拼接。

流程（P0-P3）：
1. generate_video_script(): Chat API → JSON 镜头脚本（4-8 镜头，15-25 秒）
2. VideoGeneratorAdapter:   统一适配层，geeknow 等 OpenAI 兼容代理（Sora 风格 /videos 接口）
3. assemble_videos():       ffmpeg 拼接完整视频
"""
import base64
import json
import math
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import imageio_ffmpeg

# LLM 系统提示词（用户提供，逐条保留）
_SCRIPT_PROMPT_PATH = Path(__file__).parent / "prompts" / "video_script.txt"
_PROMO_PROMPT_PATH = Path(__file__).parent / "prompts" / "video_promo.txt"

# 每模型家族参数映射（geeknow 文档 https://docs.geeknow.top/api-reference/videos/model-matrix）：
# 全部模型统一入口 POST /v1/videos，仅字段名按家族区分；未知家族发通用字段（seconds + aspect_ratio）
_AR_SIZE = {"16:9": "1280x720", "9:16": "720x1280"}

# 电影级视觉语言：追加到每条出片提示词末尾，保证成片有电影感（anamorphic 镜头/浅景深/体积光/胶片调色与颗粒）
_CINEMATIC_SUFFIX = (", cinematic movie-quality look, anamorphic lens, shallow depth of field, "
                     "volumetric lighting, filmic color grade, delicate film grain, "
                     "high production value, dramatic premium art direction")


def _p_sora(payload, dur, ar):
    """sora-2 / veo_3_1：seconds + size(分辨率)"""
    return {**payload, "seconds": str(dur), "size": _AR_SIZE.get(ar, "1280x720")}


def _p_grok(payload, dur, ar):
    """grok-imagine-video*：seconds + aspect_ratio + resolution"""
    return {**payload, "seconds": str(dur), "aspect_ratio": ar, "resolution": "720P"}


def _p_ratio(payload, dur, ar):
    """minimax-h3* / manxue-2.x：duration + ratio"""
    return {**payload, "duration": dur, "ratio": ar, "resolution": "720P"}


def _p_wan(payload, dur, ar):
    """wan3.0-video*：seconds + size(清晰度) + aspect_ratio"""
    return {**payload, "seconds": str(dur), "size": "720P", "aspect_ratio": ar}


def _p_generic(payload, dur, ar):
    """国产 AIGC（Kling/Vidu/Hailuo 等）及未知家族：seconds + aspect_ratio"""
    return {**payload, "seconds": str(dur), "aspect_ratio": ar}


_MODEL_PARAM_MAP = {
    "sora": _p_sora,
    "veo": _p_sora,
    "grok": _p_grok,
    "minimax": _p_ratio,
    "manxue": _p_ratio,
    "wan3": _p_wan,
}


def _curl_json(url: str, payload: dict, headers: dict, timeout_sec: int = 300):
    """subprocess 调 curl 发 POST（与 main.py/_curl_json_post 同款：长 JSON 写临时文件，规避
    Windows SSL/代理问题与 [WinError 206] 命令行超长）"""
    body = json.dumps(payload, ensure_ascii=False)
    tmp_file = None
    cmd = ["curl", "-s", "-m", str(timeout_sec), "-w", "\n%{http_code}",
           "-X", "POST", "-H", "Content-Type: application/json"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if len(body) > 500:
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w', encoding='utf-8')
        tmp_file.write(body)
        tmp_file.close()
        cmd += ["-d", "@" + tmp_file.name]
    else:
        cmd += ["-d", body]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 10)
    finally:
        if tmp_file:
            Path(tmp_file.name).unlink(missing_ok=True)
    out = r.stdout.decode("utf-8", errors="replace")
    if "\n" not in out:
        return None, 0, out.strip() or "curl 无响应"
    raw, code = out.rsplit("\n", 1)
    try:
        return json.loads(raw), int(code.strip()), ""
    except (json.JSONDecodeError, ValueError):
        return None, int(code.strip()) if code.strip().isdigit() else 0, raw[:500]


def _curl_get(url: str, headers: dict, timeout_sec: int = 60):
    cmd = ["curl", "-s", "-m", str(timeout_sec), "-w", "\n%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 10)
    out = r.stdout.decode("utf-8", errors="replace")
    if "\n" not in out:
        return None, 0, out.strip() or "curl 无响应"
    raw, code = out.rsplit("\n", 1)
    try:
        return json.loads(raw), int(code.strip()), ""
    except (json.JSONDecodeError, ValueError):
        return None, int(code.strip()) if code.strip().isdigit() else 0, raw[:500]


def _curl_download(url: str, dest: Path, headers: dict, timeout_sec: int = 600) -> bool:
    cmd = ["curl", "-s", "-m", str(timeout_sec), "-L", "-o", str(dest), "-w", "%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 10)
    code = r.stdout.decode(errors="replace").strip()
    return code.startswith("2") and dest.exists() and dest.stat().st_size > 1024


# ====== 步骤1：LLM → 镜头分解脚本 ======

def _parse_shot_json(raw: str) -> List[dict]:
    """解析 LLM 返回的 JSON 数组（容忍 markdown 包裹与前后说明文字）"""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw.startswith("["):
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            raw = m.group(0)
    shots = json.loads(raw)
    if not isinstance(shots, list) or not shots:
        raise ValueError("镜头脚本不是非空 JSON 数组")
    out = []
    for i, s in enumerate(shots):
        if not isinstance(s, dict) or not str(s.get("visual_prompt", "")).strip():
            continue
        s.setdefault("shot_id", i + 1)
        s.setdefault("shot_type", "medium shot")
        s.setdefault("camera_movement", "static")
        s.setdefault("duration_seconds", 3)
        s.setdefault("audio_cue", "")
        s.setdefault("transition", "cut")
        out.append(s)
    if not out:
        raise ValueError("镜头脚本无有效 visual_prompt")
    return out


def generate_video_script(api_url: str, api_key: str, chat_model_name: str,
                          novel_content: str, shot_count: int = 6) -> List[dict]:
    """调用 Chat API 把小说原文拆解成镜头脚本 JSON 数组"""
    system = _SCRIPT_PROMPT_PATH.read_text(encoding="utf-8").replace("{shot_count}", str(shot_count))
    system = system.replace("{{shot_count}}", str(shot_count))
    payload = {
        "model": chat_model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"INPUT NOVEL CONTENT:\n{novel_content.strip()}"},
        ],
        "temperature": 0.85,
        "max_tokens": 8192,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = api_url.rstrip("/") + "/chat/completions"
    j, code, curl_err = _curl_json(url, payload, headers, 300)
    if curl_err or code >= 400:
        raise RuntimeError(f"Chat API HTTP {code}: {curl_err or json.dumps(j, ensure_ascii=False)[:300]}")
    raw = j["choices"][0]["message"]["content"].strip()
    print(f"[VIDEO SCRIPT] 原始响应({len(raw)}字符)")
    return _parse_shot_json(raw)


def generate_promo_script(api_url: str, api_key: str, chat_model_name: str,
                          novel_content: str, max_candidates: int = 5) -> List[dict]:
    """单段 FB 推广：LLM 从小说原文产出多个（默认 5 个）候选推广剧本，每个是 10-15 秒连续镜头。

    返回候选 list（每个元素 schema 与镜头脚本一致），由前端/调用方选一个再出片。
    每个候选时长强制落在 10-15。"""
    system = _PROMO_PROMPT_PATH.read_text(encoding="utf-8")
    payload = {
        "model": chat_model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"INPUT NOVEL CONTENT:\n{novel_content.strip()}"},
        ],
        "temperature": 0.9,
        "max_tokens": 12000,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = api_url.rstrip("/") + "/chat/completions"
    j, code, curl_err = _curl_json(url, payload, headers, 300)
    if curl_err or code >= 400:
        raise RuntimeError(f"Chat API HTTP {code}: {curl_err or json.dumps(j, ensure_ascii=False)[:300]}")
    raw = j["choices"][0]["message"]["content"].strip()
    print(f"[VIDEO PROMO] 原始响应({len(raw)}字符)")
    shots = _parse_shot_json(raw)
    if not shots:
        raise ValueError("未提取到有效推广剧本")
    out = []
    for s in shots[:max_candidates]:
        if not isinstance(s, dict) or not str(s.get("visual_prompt", "")).strip():
            continue
        s.setdefault("concept_title", "推广片段" + str(len(out) + 1))
        s.setdefault("hook", "")
        try:
            dur = int(s.get("duration_seconds") or 12)
        except (TypeError, ValueError):
            dur = 12
        s["duration_seconds"] = max(10, min(15, dur))  # grok 单次上限 15s，LLM 在 10-15 内自选
        out.append(s)
    if not out:
        raise ValueError("候选剧本均无有效 visual_prompt")
    return out


# ====== 步骤2：统一视频模型适配层 ======

class VideoGeneratorAdapter:
    """geeknow 等 OpenAI 兼容代理的视频生成适配器（Sora 风格：创建任务 → 轮询 → 下载）。

    模型名决定参数映射（sora/kling/runway/pika），未知名按 sora 处理。
    """

    POLL_INTERVAL = 5          # 轮询间隔秒
    POLL_TIMEOUT = 900         # 单镜头最长等待 15 分钟

    def __init__(self, api_url: str, api_key: str, model_name: str,
                 aspect_ratio: str = "16:9"):
        self.api_url = (api_url or "").rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.aspect_ratio = aspect_ratio
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def format_prompt(self, shot_data: dict) -> dict:
        """标准 shot_data → 对应模型家族的创建任务 payload（统一 POST /v1/videos）"""
        name = self.model_name.lower()
        fn = next((_MODEL_PARAM_MAP[k] for k in _MODEL_PARAM_MAP if name.startswith(k)), _p_generic)
        # 单次时长上限按模型族：grok-imagine-video* 支持到 15s（FB 单段推广 10-15s）；其余维持 5s 上限
        cap = 15 if name.startswith("grok") else 5
        dur = int(max(2, min(cap, int(shot_data.get("duration_seconds", 3) or 3))))
        visual = str(shot_data.get("visual_prompt", "")).strip()
        shot_type = str(shot_data.get("shot_type", "")).strip()
        camera = str(shot_data.get("camera_movement", "")).strip()
        prompt = ", ".join(x for x in [shot_type, camera] if x and x.lower() != "static") + \
            (": " if (shot_type or camera) else "") + visual
        audio = str(shot_data.get("audio_cue", "") or "").strip()
        if audio:
            # 模型支持同步声音（grok-imagine-video 等）：把音效/对白/音乐氛围并入提示词
            prompt = (prompt + ", " if prompt else "") + audio
        payload = {"model": self.model_name, "prompt": (prompt + _CINEMATIC_SUFFIX).strip(", ")}
        return fn(payload, dur, self.aspect_ratio)

    def _create_task(self, payload: dict) -> str:
        url = self.api_url + "/videos"
        j, code, err = _curl_json(url, payload, self.headers, 120)
        if err or code >= 400:
            raise RuntimeError(f"创建视频任务 HTTP {code}: {err or json.dumps(j, ensure_ascii=False)[:300]}")
        task_id = (j or {}).get("id") or (j or {}).get("task_id") or ""
        if not task_id:
            # 某些代理直接同步返回视频地址
            vurl = self._extract_video_url(j)
            if vurl:
                return "direct::" + vurl
            raise RuntimeError(f"未返回任务 id: {json.dumps(j, ensure_ascii=False)[:300]}")
        return str(task_id)

    @staticmethod
    def _extract_video_url(j) -> str:
        if not isinstance(j, dict):
            return ""
        data = j.get("data") if isinstance(j.get("data"), dict) else j
        for k in ("video_url", "url", "output_url", "result_url"):
            if isinstance(data, dict) and data.get(k):
                return data[k]
        if isinstance(data, dict) and isinstance(data.get("output"), list) and data["output"]:
            first = data["output"][0]
            if isinstance(first, dict) and first.get("url"):
                return first["url"]
        return ""

    def _poll_task(self, task_id: str) -> str:
        """轮询直到完成，返回视频 URL（或 'direct::URL' 原样通过）"""
        if task_id.startswith("direct::"):
            return task_id
        url = self.api_url + "/videos/" + task_id
        deadline = time.time() + self.POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            j, code, err = _curl_get(url, self.headers, 60)
            if err or code >= 400:
                print(f"[VIDEO] 轮询 HTTP {code}: {err or ''}")
                continue
            status = str((j or {}).get("status", "")).lower()
            vurl = self._extract_video_url(j)
            if status in ("completed", "succeeded", "success", "done") or (vurl and status not in ("failed", "error")):
                if vurl:
                    return vurl
            if status in ("failed", "error", "cancelled"):
                reason = (j or {}).get("error") or json.dumps(j, ensure_ascii=False)[:300]
                raise RuntimeError(f"视频生成失败: {reason}")
        raise RuntimeError("视频生成超时（15 分钟）")

    def generate(self, shot_data: dict, dest_dir: Path, shot_id: int) -> dict:
        """生成单个镜头视频，下载到 dest_dir，返回 {"file": 文件名, "duration": 秒}"""
        payload = self.format_prompt(shot_data)
        print(f"[VIDEO] shot {shot_id} 创建任务: model={self.model_name}")
        task_id = self._create_task(payload)
        vurl = self._poll_task(task_id)
        fname = f"shot-{shot_id}.mp4"
        dest = dest_dir / fname
        auth = {"Authorization": f"Bearer {self.api_key}"}
        ok = bool(vurl) and _curl_download(vurl, dest, auth, 600)
        if not ok:
            # 兜底：代理结果代理下载端点（docs: GET /v1/videos/{task_id}/content）
            if task_id.startswith("direct::"):
                raise RuntimeError(f"shot {shot_id} 视频下载失败")
            ok = _curl_download(self.api_url + "/videos/" + task_id + "/content", dest, auth, 600)
        if not ok:
            raise RuntimeError(f"shot {shot_id} 视频下载失败")
        dur = int(shot_data.get("duration_seconds", 3) or 3)
        return {"file": fname, "duration": dur}


# ====== 步骤3：ffmpeg 拼接 ======

def assemble_videos(video_paths: List[Path], out_path: Path) -> None:
    """按顺序拼接多个 mp4（统一重编码，避免编码参数不一致导致 concat 失败）"""
    if not video_paths:
        raise ValueError("无视频可拼接")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    args = [ffmpeg, "-y"]
    for p in video_paths:
        args += ["-i", str(p)]
    n = len(video_paths)
    fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0"
    args += ["-filter_complex", fc, "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", str(out_path)]
    r = subprocess.run(args, capture_output=True, timeout=600)
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg 拼接失败: {r.stderr.decode(errors='replace')[-500:]}")


# ====== 整体流程编排 ======

def run_video_generation(batch_dir: Path, chat_cfg: dict, video_cfg: dict, params: dict,
                         progress_cb: Callable[[dict], None],
                         should_cancel: Optional[Callable[[], bool]] = None) -> None:
    """完整流程：脚本（可选跳过，用传入 script）→ 逐镜头生成 → 拼接。

    chat_cfg: {api_url, api_key, chat_model_name}
    video_cfg: {api_url, api_key, model, aspect_ratio}
    params: {novel_content, script(可选), shot_count, video_model}
    progress_cb(dict): 接收进度快照（调用方负责写 _progress.json / SSE）
    """
    state = {
        "status": "running", "stage": "script", "script": params.get("script") or [],
        "shots": [], "errors": [], "full_video": "", "total_duration": 0,
    }

    def snap(**kw):
        state.update(kw)
        progress_cb(dict(state))

    try:
        script = params.get("script") or []
        if not script:
            snap(stage="script")
            # 单段 FB 推广模式：先产出候选剧本，直接生成时默认取第 1 个出片（正常前端会先选好传 script）
            candidates = generate_promo_script(
                chat_cfg["api_url"], chat_cfg["api_key"], chat_cfg["chat_model_name"],
                params["novel_content"])
            script = candidates[:1]
            state["script"] = script
            snap(stage="generate")
        else:
            snap(stage="generate")

        adapter = VideoGeneratorAdapter(
            video_cfg["api_url"], video_cfg["api_key"],
            params.get("video_model") or video_cfg.get("model", "sora-2"),
            video_cfg.get("aspect_ratio", "16:9"))

        for shot in script:
            sid = int(shot.get("shot_id", len(state["shots"]) + 1))
            state["shots"].append({"shot_id": sid, "state": "pending", "file": "",
                                   "title": shot.get("concept_title") or "",
                                   "error": "", "duration": int(shot.get("duration_seconds", 3) or 3)})
        snap()

        for i, shot in enumerate(script):
            if should_cancel and should_cancel():
                snap(status="cancelled")
                return
            sid = int(shot.get("shot_id", i + 1))
            entry = next((s for s in state["shots"] if s["shot_id"] == sid), state["shots"][i])
            entry["state"] = "generating"
            snap()
            try:
                result = adapter.generate(shot, batch_dir, sid)
                entry["file"] = result["file"]
                entry["state"] = "done"
            except Exception as e:
                entry["state"] = "failed"
                entry["error"] = str(e)[:300]
                state["errors"].append(f"镜头{sid}: {e}")
                print(f"[VIDEO] shot {sid} 失败: {e}")
            snap()

        done_files = [batch_dir / s["file"] for s in state["shots"] if s["file"]]
        if len(done_files) == 1:
            # 单段推广：该片段即最终产物，无需 ffmpeg 拼接
            state["full_video"] = done_files[0].name
        elif done_files and params.get("auto_assemble"):
            snap(stage="assemble")
            full = batch_dir / "full_video.mp4"
            try:
                assemble_videos(done_files, full)
                state["full_video"] = full.name
            except Exception as e:
                state["errors"].append(f"拼接失败: {e}")
                print(f"[VIDEO] 拼接失败: {e}")

        state["total_duration"] = sum(s["duration"] for s in state["shots"] if s["state"] == "done")
        snap(status="done", stage="done")
    except Exception as e:
        snap(status="error", stage="error")
        state["errors"].append(str(e)[:300])
        progress_cb(dict(state))
        raise
