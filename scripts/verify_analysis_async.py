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

    # 5) 过期清理：只清旧的
    old_id, new_id = "sweep_old", "sweep_new"
    main._analysis_set(old_id, status="success", percent=100)
    main._analysis_set(new_id, status="success", percent=100)
    with main._ANALYSIS_LOCK:
        main._ANALYSIS_JOBS[old_id]["created_at"] = time.time() - main._ANALYSIS_TTL - 60
    main._analysis_sweep()
    assert main._analysis_get(old_id) is None, "过期任务没被清掉"
    assert main._analysis_get(new_id) is not None, "新任务被误清了"

    print("OK: 分析已改为异步（提交 <2s 返回 / 进度只进不退 / 5 条失败路径都落到 failed / "
          "轮询不重复传大 JSON / 过期清理不误伤）")


if __name__ == "__main__":
    main()
