# -*- coding: utf-8 -*-
"""验证：滚屏视频/拼接不再把整个视频的帧攒在内存里。

背景：原实现 `frames.append(...)` 攒满再用 moviepy 编码，1080x1920 一帧 6.2MB、
600+ 帧就是 3~4GB，4G 内存的服务器会被内核 OOM 连整个服务一起杀掉（网页 502）。
现在改成把帧流式写进 ffmpeg 管道，峰值内存只有一帧。

检查：
  1. 流式编码 300 帧 720x1280 时，进程峰值内存几乎不涨（攒列表的话要 +830MB）
  2. 编码出来的 mp4 帧数正确（流式没丢帧）
  3. assemble_videos 用 concat 解复用器拼接，帧数等于两段之和
  4. main.py 的滚屏路径里不再有 frames.append（防回退的源码哨兵）

内存断言只在能拿到 ru_maxrss 的系统上跑（Linux；Windows 上自动跳过）。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

W, H, FPS = 720, 1280, 30
N_FRAMES = 300          # 攒列表的话 300 * 720*1280*3 ≈ 830MB


def peak_rss_mb():
    """进程峰值内存（MB）。Linux 用 resource，Windows 用 psapi；都拿不到返回 None。"""
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / 1024 if rss > 100000 else rss   # Linux 给 KB，macOS 给字节
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        get_mem = k32.K32GetProcessMemoryInfo
        # argtypes/restype 必须显式声明：默认按 c_int 传参会把伪句柄 -1 截断，
        # 调用返回 0 却不抛异常，读出来是 0 —— 断言会"假通过"
        get_mem.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
        get_mem.restype = wt.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not get_mem(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.PeakWorkingSetSize / 1024 / 1024
    except Exception:
        return None


def frame_count(mp4: Path) -> int:
    """用 ffmpeg 解码一遍数帧"""
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(mp4), "-f", "null", "-"],
                       capture_output=True, timeout=300)
    err = r.stderr.decode(errors="replace")
    last = None
    for line in err.splitlines():
        if "frame=" in line:
            tail = line.split("frame=")[-1].strip().split()[0]
            if tail.isdigit():
                last = int(tail)
    if last is None:
        raise AssertionError(f"数不出帧数:\n{err[-800:]}")
    return last


def solid(color) -> np.ndarray:
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    arr[:, :] = color
    return arr


def main():
    import main

    tmp = Path(tempfile.mkdtemp(prefix="video_mem_"))
    out = tmp / "stream.mp4"

    before = peak_rss_mb()

    def frames():
        for i in range(N_FRAMES):
            yield solid((i % 256, 40, 200))

    main._encode_frames_to_mp4(frames(), out, (W, H), FPS)
    assert out.exists() and out.stat().st_size > 0, "流式编码没有产出文件"
    assert frame_count(out) == N_FRAMES, f"帧数不对: {frame_count(out)} != {N_FRAMES}"

    after = peak_rss_mb()
    if before is not None and after is not None:
        grew = after - before
        # 攒列表的话这里至少涨 800MB；流式应当只有几十 MB 的编码器开销
        assert grew < 300, f"峰值内存涨了 {grew:.0f}MB —— 帧又被攒进内存了"

    # 拼接：两段各 1 秒，拼完应为两段之和
    import video_gen
    a, b = tmp / "a.mp4", tmp / "b.mp4"
    main._encode_frames_to_mp4((solid((255, 0, 0)) for _ in range(30)), a, (W, H), FPS)
    main._encode_frames_to_mp4((solid((0, 255, 0)) for _ in range(30)), b, (W, H), FPS)
    joined = tmp / "joined.mp4"
    video_gen.assemble_videos([a, b], joined)
    got = frame_count(joined)
    assert got == 60, f"拼接后应为 60 帧，实际 {got}"
    assert not joined.with_suffix(".concat.txt").exists(), "临时 concat 清单没清掉"

    # 源码哨兵：滚屏两条路径不许再攒帧（弹屏视频那处是按「段」攒图，只有几十张，留着）
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    n_append = src.count("frames.append")
    assert n_append == 1, f"main.py 里 frames.append 出现 {n_append} 次，应只剩弹屏视频那一处"
    assert src.count("_encode_frames_to_mp4(frame_stream") == 2, "两条滚屏路径都应走流式编码"

    print(f"OK: 流式编码 {N_FRAMES} 帧峰值内存增长 "
          f"{'n/a' if before is None else f'{after - before:.0f}MB'}；拼接与帧数均正确")


if __name__ == "__main__":
    main()
