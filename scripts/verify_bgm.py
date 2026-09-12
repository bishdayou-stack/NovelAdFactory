# -*- coding: utf-8 -*-
"""验证：背景音乐（本地合成保底 + 只挑 CC0 的合法下载）。

覆盖：
  1. 授权过滤：非 CC0（by-nc-nd 等禁止商用/改编的）一律不能进候选 —— 这条判错就是法律风险
  2. 曲子过滤：排除音效/实地录音（雨声、海浪、风铃）与纯音乐/伴奏（用户要英文歌）
  3. 排序：爱情/情感 + 有人声 的排在前面
  4. 文件名清洗：标题里的 ? : 等字符不能带进文件名（Windows 会直接报错中断下载）
  5. 合成音质底线：不爆音、无 NaN、峰值归一、首尾淡入淡出、低频为主
  6. 库管理：没有曲子才合成、幂等、force 会重做；真歌优先于合成乐
"""
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import bgm

    # 1) 授权过滤 —— 最要紧的一条
    results = [
        {"title": "My Love Song", "url": "http://a/1.mp3", "license": "by-nc-nd"},
        {"title": "Sad love song", "url": "http://a/2.mp3", "license": "by-nc"},
        {"title": "Calm ambient forrest sounds", "url": "http://a/3.mp3", "license": "cc0"},
        {"title": "Piano Instrumental", "url": "http://a/4.mp3", "license": "cc0"},
        {"title": "True Love Song", "url": "http://a/5.mp3", "license": "cc0"},
        {"title": "Another Romantic Ballad", "url": "http://a/6.mp3", "license": "cc0"},
        {"title": "重复的", "url": "http://a/5.mp3", "license": "cc0"},          # 同 URL 去重
        {"title": "no url", "url": "", "license": "cc0"},
    ]
    picked = bgm.split_downloadable(results, want=10)
    titles = [p["title"] for p in picked]
    assert titles == ["True Love Song", "Another Romantic Ballad"], titles
    assert all(p["license"] in bgm._OK_LICENSES for p in picked), picked

    # 2) 曲子过滤
    for title, want in [("My Love Song", True), ("Romantic ballad feat. Anna", True),
                        ("Soft Piano Music", True), ("Piano Instrumental", False),
                        ("Karaoke backing track", False), ("Calm ambient forrest sounds", False),
                        ("Windchime soft", False), ("Rain on window", False),
                        ("a girl describing love tiredly", True), ("", False)]:
        got = bgm.is_music_like(title)
        assert got == want, f"{title!r} 期望 {want} 得到 {got}"

    # 3) 排序：情歌 > 泛泛的器乐
    assert bgm.song_score("My Love Song vocal") > bgm.song_score("Soft Piano Music")
    assert bgm.song_score("Funky Dance Remix") < bgm.song_score("Soft Piano Music")

    # 4) 文件名清洗
    for raw in ["What's going on?? Music.mp3", 'a/b:c*d.mp3', "Song.flac"]:
        clean = bgm._clean_name(raw)
        assert not any(ch in clean for ch in '<>:"/\\|?*'), clean
        assert not clean.lower().endswith((".mp3", ".flac", ".wav")), clean
    assert bgm._clean_name("") == "untitled"

    # 5) 合成音质底线（不真听也能守住的下限）
    x = bgm.synthesize_track(seconds=8.0, seed=7)
    assert x.dtype == np.float32 and len(x) == int(8.0 * bgm.SR)
    assert np.isfinite(x).all(), "出现 NaN/Inf"
    assert abs(float(np.max(np.abs(x))) - 0.9) < 0.02, "峰值没归一"
    assert float(np.abs(x[:50]).max()) < 0.05, "开头没淡入"
    assert float(np.abs(x[-50:]).max()) < 0.05, "结尾没淡出"
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / bgm.SR)
    centroid = float((spec * freqs).sum() / spec.sum())
    assert centroid < 2000, f"频谱重心 {centroid:.0f}Hz 太高，不像垫乐"
    # 两次不同 seed 应给出不同曲子
    y = bgm.synthesize_track(seconds=8.0, seed=99)
    assert not np.allclose(x, y), "不同 seed 应产出不同曲子"

    # 6) 库管理
    tmp = Path(tempfile.mkdtemp(prefix="bgm_"))
    music = tmp / "音乐"
    n = bgm.ensure_library(music, count=2)
    assert n == 2 and len(bgm.list_tracks(music)) == 2, n
    assert bgm.ensure_library(music, count=2) == 0, "已有曲子时不该重复合成"
    assert bgm.real_tracks(music) == [], "合成曲不该被当成真歌"
    # 放一首「真歌」进去，真歌应优先
    (music / "my_song.mp3").write_bytes(b"x" * 2048)
    assert [t.name for t in bgm.real_tracks(music)] == ["my_song.mp3"]

    import main
    main.MUSIC_PATH = music
    import random
    random.seed(1)
    assert main._pick_random_music().name == "my_song.mp3", "有真歌时必须优先真歌"
    # 删掉真歌 → 回落到合成乐
    (music / "my_song.mp3").unlink()
    assert main._pick_random_music().name.startswith(bgm.SYNTH_PREFIX)
    # force 重建：旧的合成曲被换掉
    old = sorted(p.name for p in bgm.list_tracks(music))
    bgm.ensure_library(music, count=2, force=True)
    assert len(bgm.list_tracks(music)) == 2
    assert sorted(p.name for p in bgm.list_tracks(music)) == old, "重建后文件名应一致（内容换新）"

    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 只收合法 CC0、排除音效与纯音乐、文件名安全、合成音质达标、真歌优先于合成乐")


if __name__ == "__main__":
    main()
