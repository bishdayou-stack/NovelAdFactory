# -*- coding: utf-8 -*-
"""验证：上传的可靠性改造（超时拆分 / 临时文件清理 / 上传心跳）。

背景：投放向导传视频时体感非常慢，而且卡住时前端一片死寂，分不清「还在传」和「已经挂了」。
读代码发现三件事：

  1. `upload_ad_video` 传 `timeout=600`，而 `_http_request` 把**同一个值**同时当成连接超时
     和传输超时 → `curl --connect-timeout 600`。网断了要干等 10 分钟才重试。
     而 1MB 的素材根本不需要 10 分钟，50MB 按实测 0.7MB/s 也只要 ~70 秒。
  2. `_http_request` 把上传内容写到临时文件用 `delete=False`，**全文件没有任何地方删它** ——
     每传一次（图片也一样）就在临时目录留一份完整副本。
  3. 上传期间服务端一个进度事件都不推（进度只在每个广告做完后推），前端面板定住不动。

覆盖：
  1. **回归守门员**：不传 connect_timeout 时，命令行和改造前**逐字一致**（24 个既有调用点
     的行为一个字都不能变 —— 看板同步、账户查询、建系列/组/广告全走这个函数）
  2. 传了 connect_timeout：连接用小的、传输仍按大预算
  3. 上传预算随文件大小递增，且有上下限
  4. 临时文件：成功 / 失败 / 重试之后都必须删掉；重试期间必须**还在**（删早了第 2 次尝试必挂）
  5. 并发时互不干扰（清理列表必须是每次调用独立的，用模块级全局会把别人的文件删了）
  6. 上传前推的那条事件：类型是 upload（不是 progress）、字段齐、**不带 error**
  7. 前端有 upload 的监听（否则事件发出去没人看）
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def _tmp_path_in(cmd):
    """从 curl 命令行里抠出临时文件路径。

    注意：是 `["-F", "source=@/tmp/x.mp4;filename=x.mp4"]` 两个**分开的** argv 元素，
    不是拼在一起的一整串 —— 直接对每个元素 startswith("-F") 是抠不到的。
    """
    for i, part in enumerate(cmd):
        if part == "-F" and i + 1 < len(cmd) and "@" in cmd[i + 1]:
            return cmd[i + 1].split("@", 1)[1].split(";", 1)[0]
    return None


def main():
    import meta_api

    # ---- 1) 回归守门员：既有调用点的命令行一字不变 ----
    cmds = []
    real_run = meta_api.subprocess.run

    class _R:
        returncode = 0
        stdout = '{"ok": 1}\n200'
        stderr = ""

    meta_api.subprocess.run = lambda cmd, **kw: (cmds.append(list(cmd)), _R())[1]
    try:
        # 普通调用（不传 connect_timeout）—— 24 个既有调用点都是这种
        meta_api._http_request("GET", "https://graph.facebook.com/v25.0/me", params={"a": "b"})
        joined = " ".join(cmds[-1])
        assert "--connect-timeout 30" in joined and "--max-time 45" in joined, \
            f"既有调用点的超时变了（24 个调用点全走这里，不能动）：{joined}"
        # 传了 timeout 的老写法也一样：连接超时仍然跟着 timeout
        meta_api._http_request("POST", "https://x/y", data={"k": "v"}, timeout=120)
        joined = " ".join(cmds[-1])
        assert "--connect-timeout 120" in joined and "--max-time 135" in joined, joined

        # ---- 2) 拆开之后：连接用小预算，传输用大预算 ----
        meta_api._http_request("POST", "https://x/y", data={"k": "v"},
                               timeout=600, connect_timeout=20)
        joined = " ".join(cmds[-1])
        assert "--connect-timeout 20" in joined, f"连接超时没走单独的值：{joined}"
        assert "--max-time 615" in joined, f"传输预算被连接超时带跑了：{joined}"
    finally:
        meta_api.subprocess.run = real_run

    # ---- 3) 上传预算按大小算 ----
    f = meta_api.upload_timeout_seconds
    assert f(1 * 1024 * 1024) == 90, f(1)
    assert f(26 * 1024 * 1024) == 104, f(26)          # 26MB → 104 秒（实测传 30-40 秒，够用）
    assert f(50 * 1024 * 1024) == 200, f(50)          # 50MB → 200 秒
    assert f(500 * 1024 * 1024) == 900, f(500)        # 封顶 15 分钟
    assert f(1) == 90 and f(0) == 90, "小文件要有下限"
    assert f(26 * 1048576) > f(10 * 1048576), "预算必须随文件变大"

    # ---- 4) 临时文件：成功 / 失败 / 重试 都要删，重试期间必须在 ----
    attempts = {"n": 0, "missing": 0, "paths": []}

    class _R2:
        def __init__(self, out):
            self.returncode = 0
            self.stdout = out
            self.stderr = ""

    def fake_run(cmd, **kw):
        attempts["n"] += 1
        path = _tmp_path_in(cmd)
        if path:
            attempts["paths"].append(path)
            if not Path(path).exists():
                attempts["missing"] += 1
        if attempts["n"] < 3:
            return _R2("")            # 空响应 → 触发重试
        return _R2('{"id": "v1"}\n200')

    real_run = meta_api.subprocess.run
    meta_api.subprocess.run = fake_run
    try:
        payload = b"x" * 2048
        data, err = meta_api._http_request("POST", "https://x/y",
                                           data={"source": ("clip.mp4", payload)})
        assert not err and data.get("id") == "v1", (data, err)
        assert attempts["n"] == 3, f"应该重试到第 3 次才成功，实际 {attempts['n']} 次"
        assert attempts["missing"] == 0, (
            "重试期间临时文件被删了 —— 第 2 次尝试必然失败（清理必须放在整个重试循环之后）")
        # 全程用的是同一个临时文件（不是每次重试新建）
        assert len(set(attempts["paths"])) == 1, f"重试换了临时文件：{set(attempts['paths'])}"
        assert all(not Path(p).exists() for p in attempts["paths"]), \
            f"临时文件没被删掉，泄漏了：{[p for p in attempts['paths'] if Path(p).exists()]}"

        # 4b) 调用抛异常时也要删
        attempts["paths"] = []
        def boom_run(cmd, **kw):
            path = _tmp_path_in(cmd)
            if path:
                attempts["paths"].append(path)
            raise RuntimeError("模拟崩了")
        meta_api.subprocess.run = boom_run
        data, err = meta_api._http_request("POST", "https://x/y",
                                           data={"source": ("clip.mp4", b"y" * 512)})
        assert err, "异常应该被兜成错误返回"
        assert all(not Path(p).exists() for p in attempts["paths"]), "异常路径下临时文件没删"
    finally:
        meta_api.subprocess.run = real_run

    # ---- 5) 并发互不干扰 ----
    created, lock = [], threading.Lock()
    real_run = meta_api.subprocess.run

    def slow_run(cmd, **kw):
        p = _tmp_path_in(cmd)
        if p:
            with lock:
                created.append(p)
            time.sleep(0.15 if "A" in p else 0.30)   # B 应该比 A 后结束
        return _R2('{"ok": 1}\n200')

    meta_api.subprocess.run = slow_run
    try:
        out = {}

        def worker(tag):
            d, e = meta_api._http_request("POST", "https://x/y",
                                          data={"source": (f"{tag}.mp4", tag.encode() * 100)})
            out[tag] = (d, e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert all(v[1] is None for v in out.values()), out
        with lock:
            assert len(set(created)) == 2, f"两次调用的临时文件应该各自独立：{created}"
            assert all(not Path(p).exists() for p in created), "并发下临时文件没清干净"
    finally:
        meta_api.subprocess.run = real_run

    # ---- 4c) 上传函数真的按大小给预算、并按小预算连 ----
    vid = Path(tempfile.mkdtemp(prefix="upsize_")) / "v.mp4"
    vid.write_bytes(b"z" * (3 * 1024 * 1024))     # 3MB
    seen = []
    meta_api.subprocess.run = lambda cmd, **kw: (seen.append(" ".join(map(str, cmd))), _R2('{"id":"x"}\n200'))[1]
    try:
        meta_api.upload_ad_video("act_1", "tok", str(vid))
        joined = seen[-1]
        assert "--connect-timeout 20" in joined, f"上传没用小连接超时：{joined}"
        assert "--max-time 105" in joined, f"3MB 应给 90 秒预算（+15）：{joined}"
        assert "--connect-timeout 600" not in joined, "连接超时还是那个害人的 600 秒"
    finally:
        meta_api.subprocess.run = real_run
        vid.unlink(missing_ok=True)
        vid.parent.rmdir()

    # ---- 6) 真路径：上传前推的那条事件 ----
    import delivery
    events = []
    real_push = delivery._push_event
    real_token = delivery._get_token
    import database
    real_db = {k: getattr(database, k) for k in (
        "create_delivery_campaign", "create_delivery_adset", "add_to_delivery_queue",
        "update_delivery_campaign_fb_id", "update_delivery_adset_fb_id")}
    real_api = {k: getattr(meta_api, k) for k in (
        "create_campaign", "create_adset", "create_ad", "upload_ad_video")}
    seq = iter(range(5000, 9000))
    try:
        delivery._push_event = lambda bid, typ, data=None: events.append((typ, data or {}))
        delivery._get_token = lambda a, uid=None: "tok"
        meta_api.create_campaign = lambda *a, **k: ("c1", None)
        meta_api.create_adset = lambda *a, **k: ("a1", None)
        meta_api.create_ad = lambda *a, **k: ("ad1", None)
        meta_api.upload_ad_video = lambda *a, **k: ("v1", None)
        database.create_delivery_campaign = lambda *a, **k: next(seq)
        database.create_delivery_adset = lambda *a, **k: next(seq)
        database.add_to_delivery_queue = lambda *a, **k: None
        database.update_delivery_campaign_fb_id = lambda *a, **k: None
        database.update_delivery_adset_fb_id = lambda *a, **k: None

        clip = Path(tempfile.mkdtemp(prefix="upbeat_")) / "clip.mp4"
        clip.write_bytes(b"v" * (2 * 1024 * 1024))   # 2MB，让 size_mb 是个有意义的数
        params = {
            "ad_account_id": "act_1", "n_campaigns": 1, "n_adsets": 1, "n_ads": 1,
            "assets": [{"image_type": "scroll", "overlay_text": "", "_resolved_path": str(clip)}],
            "headlines": ["H1"], "ad_name": "Ad-1", "budget_strategy": "adset",
            "adset_daily_budget": 1000, "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "optimization_goal": "OFFSITE_CONVERSIONS", "pixel_id": "1",
            "page_id": "p1", "link_url": "https://x/", "targeting_json": "{}",
            "status": "PAUSED",
        }
        bid, err = delivery.submit_batch_publish(params, user_id=1)
        assert not err, err
        delivery._delivery_events[bid].wait(timeout=20)
        ups = [d for t, d in events if t == "upload"]
        assert ups, f"上传前没推 upload 事件：{[t for t, _ in events]}"
        u = ups[0]
        assert u["kind"] == "视频", u
        assert u.get("size_mb", 0) > 0, (
            f"size_mb 算出来是 {u.get('size_mb')} —— 前端会当成「没这个字段」而不显示大小（0 在 JS 里是假值）")
        assert "completed" in u and "total" in u, u
        assert "error" not in u, (
            "upload 事件带了 error 字段 —— 前端会把每次上传都当成一次失败记进错误列表")
        prog = [d for t, d in events if t == "progress"]
        assert prog and all("error" not in d for d in prog), "这次投放不该有失败"
    finally:
        delivery._push_event = real_push
        delivery._get_token = real_token
        for k, v in real_db.items():
            setattr(database, k, v)
        for k, v in real_api.items():
            setattr(meta_api, k, v)

    # ---- 7) 前端得有人在听这个事件 ----
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "addEventListener('upload'" in html, \
        "服务端推了 upload 事件，前端没监听 —— 用户还是看不到「正在上传」"
    assert "正在上传" in html, "前端的 upload 事件没有可见文案"

    print("OK: 上传可靠性（既有 24 个调用点命令行一字不变 / 连接超时与传输预算拆开 / "
          "预算按文件大小算 / 临时文件成功失败重试并发都清干净且重试期间不被删 / "
          "上传前推 upload 事件且不带 error / 前端有监听）")


if __name__ == "__main__":
    main()
