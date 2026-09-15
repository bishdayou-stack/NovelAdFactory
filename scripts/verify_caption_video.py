# -*- coding: utf-8 -*-
"""验证：逐句字幕视频（一张静态底图 + 逐句浮现的字幕）。

覆盖：
  1. 句数区间 × 每句秒数 落在 25-30 秒（用户要的时长）
  2. 规则文件写进 prompt；不带 caption_video 时不该混进来
  3. 回归：用户自定义提示词**不能吞掉**字幕规则（故事卡踩过同一个坑）
  4. 排版：折行的第二句必须留在白框里（实测踩过 —— 第一行放框中心再往下叠，第二句掉到框外）
  5. 分组：满 3 句清空重来
  6. 超长句（> CAPTION_MAX_WORDS）被丢掉，不塞进画面
  7. 真的能出 mp4，且**时长精确**（concat 末帧会多留一段，靠 -t 裁掉）
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

CAPTIONS = [
    "I hid our divorce beneath lab forms.",
    "He signs without lifting his eyes.",
    "Sabrina's hand circles his bare knee.",
    "His pen cuts across our marriage.",
    "He waves me toward the heavy door.",
    "I carry the folder past his mistress.",
]


def captions_21() -> list:
    """21 句（= CAPTION_MIN_LINES），用来测分段：3 段应是 7/7/7。"""
    out = []
    while len(out) < 21:
        out.extend(CAPTIONS)
    return out[:21]


def probe_duration(path: Path) -> float:
    import main
    out = subprocess.run([main.imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)],
                         capture_output=True, text=True).stderr
    for ln in out.splitlines():
        if "Duration" in ln:
            hms = ln.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise AssertionError(f"读不出时长: {out[-300:]}")


def main():
    import main

    # 1) 时长落在 25-30 秒
    for n in (main.CAPTION_MIN_LINES, main.CAPTION_MAX_LINES):
        sec = n * main.CAPTION_SEC_PER_LINE
        assert 25 <= sec <= 30, f"{n} 句 × {main.CAPTION_SEC_PER_LINE}s = {sec}s，不在 25-30 秒"
    assert main.CAPTION_GROUP_SIZE == 3

    # 2) 规则接线
    raw = main._RULES_CAPTION_VIDEO
    assert "{cap_min}" in raw and "{cap_max}" in raw, "规则文件里应有句数占位符"
    on = main._build_rules_text("", 0, 0, 0, 0, 0, False, caption_video=1)
    assert f"{main.CAPTION_MIN_LINES}-{main.CAPTION_MAX_LINES}" in on, on[-300:]
    assert "{cap_min}" not in on
    off = main._build_rules_text("", 1, 0, 0, 0, 0, False)
    assert "逐句字幕" not in off, "没要字幕视频却混进了字幕规则"
    for kw in ("忠实原文", "不许自己编", "同一件事", "绝对不要有文字"):
        assert kw in raw, f"字幕规则缺关键约束：{kw}"

    # 3) 回归：自定义提示词不能吞掉字幕规则
    MY = "暗黑浪漫风格。"
    mixed = main._build_rules_text(MY, 0, 0, 0, 0, 0, False, caption_video=1)
    assert MY in mixed and "逐句字幕" in mixed, "自定义提示词把字幕规则吞掉了"

    # 3b) 底图张数：开关关=1 张，开=CAPTION_BG_COUNT_MULTI 张，且规则里的 {bg_count} 要换成真值
    assert main.caption_bg_count(False) == 1 and main.caption_bg_count(True) == main.CAPTION_BG_COUNT_MULTI
    assert main.CAPTION_BG_COUNT_MULTI == 3, "底图张数改了？确认过观感/成本再改"
    one = main._build_rules_text("", 0, 0, 0, 0, 0, False, caption_video=1)
    many = main._build_rules_text("", 0, 0, 0, 0, 0, False, caption_video=1, caption_multi_bg=True)
    assert "{bg_count}" not in one and "必须是 1" in one, "单底图模式没把 {bg_count} 换成 1"
    assert "{bg_count}" not in many and f"必须是 {main.CAPTION_BG_COUNT_MULTI}" in many, "多底图模式没换成 3"
    assert "均分" in many, "多底图规则没说要按 captions 顺序分段"

    # 3b-2) **回归：pad_items 不能把带 captions 的条目整条丢掉**
    #       实测事故：pad_items 按 image_prompt(单数) 判有效，而字幕视频的 entry 是
    #       image_prompts(复数)+captions —— 每条都被判无效整条替换，captions 全丢，
    #       表现是「底图出来了但视频没有」，warning 写「没有字幕文案，跳过合成（只有底图）」。
    cap_items = [{"image_prompts": ["p1"], "captions": ["a", "b"]},
                 {"image_prompts": ["p2"], "captions": ["c"]}]
    filler = {"image_prompts": ["f"], "captions": []}
    kept = main.pad_items(cap_items, 2, filler, key="captions")
    assert [o.get("captions") for o in kept] == [["a", "b"], ["c"]], f"captions 被 pad_items 丢了：{kept}"
    padded = main.pad_items(cap_items, 3, filler, key="captions")
    assert padded[2] == filler and [o.get("captions") for o in padded[:2]] == [["a", "b"], ["c"]]
    # captions 为空的条目才该被顶掉
    assert main.pad_items([{"captions": []}], 1, filler, key="captions")[0] == filler
    # 不传 key 时（普通图路径）行为不变：字符串 filler 组成 {"image_prompt": ...}
    assert main.pad_items([], 1, "F")[0] == {"image_prompt": "F"}
    assert main.pad_items([{"image_prompt": "x"}], 1, "F")[0] == {"image_prompt": "x"}

    # 3c) 提示词取值：新格式数组 / 旧格式单值 / 缺失
    assert main.caption_bg_prompts_of({"image_prompts": ["a", "b"]}) == ["a", "b"]
    assert main.caption_bg_prompts_of({"image_prompt": "solo"}) == ["solo"]
    assert main.caption_bg_prompts_of({}) == [] and main.caption_bg_prompts_of(None) == []

    # 3d) 均分：不丢、不空、前面的段多一个
    assert main.split_evenly(list(range(22)), 3) == [list(range(8)), list(range(8, 15)), list(range(15, 22))]
    assert sum(len(x) for x in main.split_evenly(list(range(21)), 3)) == 21
    assert all(main.split_evenly(list(range(7)), 3)), "均分出了空段"

    # 4~6) 排版 / 分组 / 超长句
    tmp = Path(tempfile.mkdtemp(prefix="capvideo_"))
    bg_path = tmp / "bg.png"
    Image.new("RGB", (768, 1344), (30, 50, 80)).save(bg_path)
    bg = Image.open(bg_path).convert("RGB")

    font = main._load_font(main.caption_font_path(), max(24, int(768 * 0.062)))
    for i, want_boxes in ((0, 1), (2, 3)):
        png = tmp / f"state{i}.png"
        main.render_caption_state(bg, CAPTIONS[:i + 1], font, main.caption_font_path(), png)
        a = np.asarray(Image.open(png).convert("RGB"))
        white_rows = (a > 235).all(axis=2).any(axis=1)     # 该行有没有白框
        dark_rows = (a < 60).all(axis=2).any(axis=1)       # 该行有没有黑字

        # 白框按行分成连续的几条带
        bands, start = [], None
        for r, w in enumerate(white_rows):
            if w and start is None:
                start = r
            elif not w and start is not None:
                bands.append((start, r - 1)); start = None
        if start is not None:
            bands.append((start, len(white_rows) - 1))
        assert len(bands) == want_boxes, f"第 {i+1} 句应显示 {want_boxes} 个框，实际 {len(bands)}"

        # **每个文字像素都必须落在某个白框的行区间里**。
        # 旧写法把第一行放框中心再往下叠，折行的第二句会掉进框与框之间的缝里 ——
        # 之前只查「最后一个框下方」是查不出来的（缝里的字看不见），那版断言等于没写。
        inside = np.zeros_like(dark_rows)
        for b0, b1 in bands:
            inside[b0:b1 + 1] = True
        stray = int((dark_rows & ~inside).sum())
        assert stray == 0, f"第 {i+1} 句组：有 {stray} 行文字掉在白框外面（折行没在框内居中）"

    too_long = "word " * (main.CAPTION_MAX_WORDS + 3)
    st_video = main.compose_caption_video(bg_path, CAPTIONS + [too_long], tmp / "v.mp4")
    assert st_video["lines"] == len(CAPTIONS), f"超长句没被丢掉：{st_video}"

    # 7) 真的出片，且时长精确
    out = tmp / "v.mp4"
    assert out.exists() and out.stat().st_size > 1000, "没生成 mp4"
    got = probe_duration(out)
    want = len(CAPTIONS) * main.CAPTION_SEC_PER_LINE
    assert abs(got - want) < 0.15, f"时长 {got}s ≠ 期望 {want}s（concat 末帧多留的那段没裁掉）"

    # 7b) 多底图：3 张纯色底图 → 每句画面必须用**它那一段**的颜色（底图真的跟着文案切了）
    colors = [(200, 30, 30), (30, 160, 60), (40, 60, 200)]
    bg_paths = []
    for ci, col in enumerate(colors):
        p = tmp / f"bg{ci}.png"
        Image.new("RGB", (768, 1344), col).save(p)
        bg_paths.append(p)
    multi = tmp / "multi.mp4"
    st_multi = main.compose_caption_video(bg_paths, captions_21(), multi)
    assert st_multi["backgrounds"] == 3, st_multi
    groups = main.split_evenly(list(range(21)), 3)
    for ci, col in enumerate(colors):
        mid = (groups[ci][0] + groups[ci][-1]) / 2
        frame = tmp / f"chk{ci}.png"
        subprocess.run([main.imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss",
                        f"{mid * main.CAPTION_SEC_PER_LINE + 0.1:.2f}", "-i", str(multi),
                        "-frames:v", "1", str(frame)], capture_output=True)
        a = np.asarray(Image.open(frame).convert("RGB"))
        # 取画面最顶部一条（字幕块在上三分之一以下，这里一定是背景）
        top = a[:120].reshape(-1, 3).mean(axis=0)
        assert max(abs(int(top[k]) - col[k]) for k in range(3)) < 30, (
            f"第 {ci+1} 段底图颜色不对：期望 {col}，实测 {tuple(int(x) for x in top)}")
    # 关掉开关 = 一张底图用到底
    st_one = main.compose_caption_video(bg_paths[0], captions_21(), tmp / "one.mp4")
    assert st_one["backgrounds"] == 1, st_one

    # 8) 回归：**相对路径**也要能出片。
    #    ffmpeg 的 concat 清单把相对路径按「清单文件所在目录」解析，
    #    清单里写 output/x/a.png 会被拼成 output/x/output/x/a.png —— 实测线上就是这个错。
    #    上面的用例走 tempfile（绝对路径）碰不到，所以这里专门用相对路径再跑一遍。
    rel_dir = Path("_reltest_capvideo")
    rel_dir.mkdir(exist_ok=True)
    try:
        Image.new("RGB", (768, 1344), (30, 50, 80)).save(rel_dir / "bg.png")
        st_rel = main.compose_caption_video(rel_dir / "bg.png", CAPTIONS[:4], rel_dir / "v.mp4")
        assert (rel_dir / "v.mp4").exists(), "相对路径下没生成 mp4"
        assert abs(probe_duration(rel_dir / "v.mp4") - 4 * main.CAPTION_SEC_PER_LINE) < 0.15
        assert st_rel["lines"] == 4
    finally:
        shutil.rmtree(rel_dir, ignore_errors=True)

    # 9) 回归：视频字段必须**一路活到接口**。
    #    `_save_batch_meta` 和 `/api/history/{id}` 都是白名单式的固定字段列表 ——
    #    加新视频类型时只加一处，就会出现「mp4 在磁盘上、_meta.json 里也有、接口偏偏不返回」，
    #    前端历史那条路就渲染不出来。caption_videos 两处都漏过一次。
    from fastapi.testclient import TestClient
    bid = main._init_batch(999)
    try:
        sample = {
            "batch_id": bid, "status": "success",
            "videos": [f"/static/output/{bid}/{bid}-caption-1.mp4"],
            "caption_videos": [f"/static/output/{bid}/{bid}-caption-1.mp4"],
            "scroll_videos": [f"/static/output/{bid}/{bid}-scroll-video-1.mp4"],
            "ai_scroll_videos": [f"/static/output/{bid}/{bid}-ai-scroll-1.mp4"],
            "ai_popup_videos": [f"/static/output/{bid}/{bid}-ai-popup-1.mp4"],
            "popup_videos": [], "images": [],
        }
        main._save_batch_meta(sample)
        meta = json.loads((main.OUTPUT_ROOT / str(bid) / "_meta.json").read_text(encoding="utf-8"))
        for k in ("videos", "caption_videos", "scroll_videos", "ai_scroll_videos", "ai_popup_videos"):
            assert meta.get(k) == sample[k], f"_save_batch_meta 把 {k} 丢了"

        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 999, "role": "admin"}
        c = TestClient(main.app, raise_server_exceptions=False)
        d = c.get(f"/api/history/{bid}").json()
        for k in ("videos", "caption_videos", "scroll_videos", "ai_scroll_videos", "ai_popup_videos"):
            assert d.get(k) == sample[k], f"/api/history 没返回 {k}（白名单漏了）"
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)
        shutil.rmtree(main.OUTPUT_ROOT / str(bid), ignore_errors=True)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK: 逐句字幕视频正常（{len(CAPTIONS)} 句 → {got:.1f}s / {st_video['groups']} 组 / "
          f"字号 {st_video['font_size']}px；折行不外溢、超长句被丢、时长精确）")


if __name__ == "__main__":
    main()
