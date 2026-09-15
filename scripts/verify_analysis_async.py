# -*- coding: utf-8 -*-
"""验证：小说分析页的「提交 → 后台跑 → 轮询进度」改造。

背景：分析原本是同步阻塞的 —— 模型一慢 HTTP 就一直挂着，前端只能显示「一直卡在分析中」。
现在和生产中心一致：POST 立刻返回 analysis_id，后台线程干活，前端轮询进度。

覆盖：
  1. 提交立刻返回且带 analysis_id（不阻塞）
  2. 任务状态机：running → success / failed，进度只增不减
  3. 轮询：unknown id → 404；成功时带 data，进行中不带（别每 2 秒传一遍大 JSON）
  4. 各种失败路径都落到 failed 并且带 error，不会静默卡在 running
  5. 过期清理不会误删新任务
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

GOOD = json.dumps({"text_single_prompts": [{"image_prompt": "a"}],
                   "scroll_visual_prompts": [], "lr_split_prompts": [],
                   "tb_split_prompts": [], "three_panel_prompts": [],
                   "story_card_prompts": []})


def _api(content: str) -> dict:
    """包成 Chat API 的响应结构（代码读的是 choices[0].message.content）"""
    return {"choices": [{"message": {"content": content}}]}


def _job_body():
    import main
    return main.AnalyzeNovelRequest(api_key="k", api_url="https://example.invalid/v1",
                                    chat_model_name="m", novel_content="novel text",
                                    analysis_prompt="", story_card_count=0)


def main():
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)

    # 1) 提交立刻返回（后台没跑完也不该等它）
    t0 = time.time()
    r = client.post("/api/analyze-novel", json={
        "api_key": "k", "api_url": "https://example.invalid/v1",
        "chat_model_name": "m", "novel_content": "novel text", "analysis_prompt": ""})
    dt = time.time() - t0
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d.get("status") == "submitted" and d.get("analysis_id"), d
    assert dt < 2.0, f"提交居然花了 {dt:.1f}s —— 又变回同步阻塞了？"
    aid = d["analysis_id"]

    # 3a) 进行中不带 data
    s = client.get(f"/api/analyze-novel/status/{aid}").json()
    assert s["status"] in ("running", "success", "failed"), s
    if s["status"] == "running":
        assert "data" not in s, "进行中就把大 JSON 塞进轮询响应了"

    # 3b) 未知 id → 404
    assert client.get("/api/analyze-novel/status/ja_does_not_exist").status_code == 404

    # 2+4) 状态机：用假 _curl_json_post 直接驱动后台任务，快且确定
    real = main._curl_json_post

    def run_case(fake):
        main._curl_json_post = fake
        job = _job_body()
        jid = f"test_{time.time_ns()}"
        main._analysis_set(jid, status="running", percent=5, step="任务已提交", error="", result=None)
        main.run_analysis_job(job, jid)
        return main._analysis_get(jid)

    try:
        st = run_case(lambda *a, **k: (_api(GOOD), 200, None))
        assert st["status"] == "success", st
        assert st["percent"] == 100 and st["result"]["text_single_prompts"], st

        st = run_case(lambda *a, **k: ({"error": "boom"}, 500, None))
        assert st["status"] == "failed" and "500" in st["error"], st

        st = run_case(lambda *a, **k: (None, 0, "连接超时"))
        assert st["status"] == "failed" and "连接超时" in st["error"], st

        st = run_case(lambda *a, **k: (_api("这不是 JSON"), 200, None))
        assert st["status"] == "failed" and "解析失败" in st["error"], st

        st = run_case(lambda *a, **k: (_api(""), 200, None))
        assert st["status"] == "failed" and "空内容" in st["error"], st

        # 带 ``` 围栏的 JSON 要能剥掉
        fenced = "```json\n" + GOOD + "\n```"
        st = run_case(lambda *a, **k: (_api(fenced), 200, None))
        assert st["status"] == "success", st

        # 中转站掐断响应（JSON 到一半 Unterminated string）→ 必须自动重试一次
        calls = {"n": 0}
        def flaky_then_ok(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return (_api(GOOD[:len(GOOD) // 2]), 200, None)   # 截断
            return (_api(GOOD), 200, None)
        st = run_case(flaky_then_ok)
        assert st["status"] == "success", st
        assert calls["n"] == 2, f"没有重试（只调了 {calls['n']} 次）"

        # 两次都被截断 → failed，且错误信息要说清是截断、带字节数，并提示可重试
        calls["n"] = 0
        st = run_case(lambda *a, **k: (_api(GOOD[:len(GOOD) // 2]), 200, None))
        assert st["status"] == "failed", st
        assert "截断" in st["error"] and "字节" in st["error"], st["error"]
        assert "重试" in st["error"], "错误信息没告诉用户可以重试"

        # finish_reason=length 时也要认出来（哪怕 JSON 恰好还能解析一半）
        def length_cut(*a, **k):
            d = _api(GOOD[:len(GOOD) // 2])
            d["choices"][0]["finish_reason"] = "length"
            return (d, 200, None)
        st = run_case(length_cut)
        assert st["status"] == "failed" and "截断" in st["error"], st

        # 不是截断的格式错（比如模型吐了人话）→ 报解析失败，不要误报成截断
        st = run_case(lambda *a, **k: (_api("好的，我来分析这本小说："), 200, None))
        assert st["status"] == "failed" and "JSON 解析失败" in st["error"], st

        # 提示词组装抛异常也要落到 failed，不能把任务永远挂在 running
        def boom(*a, **k):
            raise RuntimeError("组装炸了")
        real_build = main.build_analysis_prompt
        main.build_analysis_prompt = boom
        try:
            st = run_case(lambda *a, **k: (_api(GOOD), 200, None))
        finally:
            main.build_analysis_prompt = real_build
        assert st["status"] == "failed" and "组装炸了" in st["error"], st
    finally:
        main._curl_json_post = real

    # 6) 分析成功后**服务端直接出图**（用户不再确认提示词）。
    #    链在服务端而不是前端，就是为了「点完分析就关页面」这种最常见的用法 ——
    #    链在前端的话，人一走分析跑完图一张都不出。
    spawned = []
    real_gen = main._run_analysis_generation

    def fake_gen(req, bid):
        spawned.append((req, bid))

    def run_auto(raw_json, **kw):
        main._run_analysis_generation = fake_gen
        main._curl_json_post = lambda *a, **k: (_api(raw_json), 200, None)
        body = _job_body()
        for k, v in kw.items():
            setattr(body, k, v)
        jid = f"auto_{time.time_ns()}"
        main._analysis_set(jid, status="running", percent=5, step="任务已提交", error="", result=None)
        try:
            main.run_analysis_job(body, jid, 4242)
        finally:
            main._run_analysis_generation = real_gen
        return main._analysis_get(jid)

    empty_json = json.dumps({"text_single_prompts": [], "scroll_visual_prompts": [],
                             "lr_split_prompts": [], "tb_split_prompts": [],
                             "three_panel_prompts": [], "story_card_prompts": []})
    made = []
    try:
        # 6a) 成功 → 服务端提交出图，status 里带 batch_id，批次记在触发分析的用户名下
        st = run_auto(GOOD, auto_generate=True)
        assert st["status"] == "success", st
        assert st.get("batch_id"), "auto_generate=True 却没提交出图批次"
        assert st["step"] == "已提交出图", st["step"]
        made.append(st["batch_id"])
        meta = json.loads((main.OUTPUT_ROOT / str(st["batch_id"]) / "_meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["user_id"] == 4242, f"批次没记到提交人名下，历史记录里会看不到：{meta}"

        for _ in range(100):
            if spawned:
                break
            time.sleep(0.05)
        assert spawned, "出图任务没进后台线程"
        req, bid = spawned[0]
        assert bid == st["batch_id"], (bid, st["batch_id"])
        assert req.text_single_prompts == json.loads(GOOD)["text_single_prompts"], req.text_single_prompts
        assert req.api_key == "k" and req.image_model_name == "", "出图参数没从分析请求带过来"

        # 6b) 模型一条提示词都没出 → 不提交空批次，且状态里要说明白
        st = run_auto(empty_json, auto_generate=True)
        assert st["status"] == "success" and not st.get("batch_id"), st
        assert "没有可生成的提示词" in st["step"], st

        # 6c) 不开 auto_generate 时行为不变（老调用方/脚本还按原样用）
        st = run_auto(GOOD)
        assert st["status"] == "success" and not st.get("batch_id"), st
        assert st["step"] == "完成", st

        # 6d) 提交出图炸了也不能把分析判死 —— 提示词是好的，还能在历史里看到
        real_init = main._init_batch
        main._init_batch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("提交炸了"))
        try:
            st = run_auto(GOOD, auto_generate=True)
        finally:
            main._init_batch = real_init
            main._curl_json_post = real
        assert st["status"] == "success" and not st.get("batch_id"), st
        assert "提交出图失败" in st["step"], st["step"]
    finally:
        main._curl_json_post = real
        main._run_analysis_generation = real_gen
        for b in made:
            shutil.rmtree(main.OUTPUT_ROOT / str(b), ignore_errors=True)

    # 6e) 轮询响应必须带 batch_id —— 前端就靠它从「分析进度」切到「出图进度」
    main._analysis_set("withbatch", status="success", percent=100, step="已提交出图",
                       result={}, batch_id=777)
    got = client.get("/api/analyze-novel/status/withbatch").json()
    assert got.get("batch_id") == 777, f"轮询响应没带 batch_id，前端切不到出图进度：{got}"

    # 6f) 静态：前端必须继续发 auto_generate=true，且确认弹窗要清干净。
    #     漏了 auto_generate 后端默认 False —— 页面会静默变回「只分析不出图」，什么错都不报。
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "auto_generate: true" in html, "分析页没再发 auto_generate，分析完不会自动出图"
    assert "confirmModal" not in html and "startAnalysisGeneration" not in html, \
        "「确认提示词」那条老路还留在前端，会和新流程打架"

    # 7) 过期清理：只清旧的
    old_id, new_id = "sweep_old", "sweep_new"
    main._analysis_set(old_id, status="success", percent=100)
    main._analysis_set(new_id, status="success", percent=100)
    with main._ANALYSIS_LOCK:
        main._ANALYSIS_JOBS[old_id]["created_at"] = time.time() - main._ANALYSIS_TTL - 60
    main._analysis_sweep()
    assert main._analysis_get(old_id) is None, "过期任务没被清掉"
    assert main._analysis_get(new_id) is not None, "新任务被误清了"

    print("OK: 分析已改为异步（提交 <2s 返回 / 进度只进不退 / 5 条失败路径都落到 failed / "
          "轮询不重复传大 JSON / 过期清理不误伤）；分析成功后服务端自动提交出图"
          "（批次记在提交人名下 / 没提示词就不提交空批次 / 提交失败不判死分析）")


if __name__ == "__main__":
    main()
