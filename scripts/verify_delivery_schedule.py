# -*- coding: utf-8 -*-
"""验证：投放向导的「排期」（广告组起止时间）。

「排期」在本项目设计文档里的定义就是 `start_time` / `end_time`：
  .superpowers/brainstorm/*/delivery-engine-v2.html  ——「排期 -> start_time / end_time」
  docs/superpowers/specs/2026-06-10-meta-ads-integration-design.md ——「模板预设受众/预算/排期/出价」
`meta_api.create_adset` 从 6-10 那个 plan 起就有这两个参数，但**从来没被传过** —— 这次是接线。

这里最危险的是**时区**：前端 datetime-local 给的是本机本地时间（不带时区），
后端跑在 UTC 服务器上。所以前端必须转成带 Z 的 UTC 再发，后端也绝不去猜服务器本地时区
（猜时区是这类功能最经典的坑；本项目为此专门有 database.bj_now()）。

覆盖：
  1. 真路径：submit_batch_publish 把排期传给广告组（且只传一次、传的是规整后的 UTC）
  2. create_adset 的 body：有排期才带这两个字段，没排期一个都不带
  3. 路由的时间规整：带 Z / 带偏移 / 裸本地时间 三种入参 → 统一成 UTC；垃圾 → 400
  4. 结束时间早于开始时间 → 拒绝
  5. 「排期在未来 + 状态关闭」→ 必须给警告（否则用户以为排上了，实际永远不会投）
  6. 静态：前端只发 toISOString()（不许把裸本地时间发出去）
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import main
    import delivery
    import meta_api
    import database

    # ---- 3) 时间规整 ----
    p = main._parse_meta_time
    f = main._fmt_meta_time
    # 带 Z：就是 UTC
    assert f(p("2026-09-20T00:00:00.000Z", "开始时间")) == "2026-09-20T00:00:00+0000"
    # 带偏移：换算成 UTC（北京时间 08:00 = 前一天 24:00 UTC）
    assert f(p("2026-09-20T08:00:00+08:00", "开始时间")) == "2026-09-20T00:00:00+0000"
    assert f(p("2026-09-19T20:00:00-04:00", "开始时间")) == "2026-09-20T00:00:00+0000"
    # 裸本地时间（手工 curl 常见）→ **按 UTC 解释**，绝不当成服务器本地时间
    assert f(p("2026-09-20T08:00", "开始时间")) == "2026-09-20T08:00:00+0000"
    # 空 → 不排期
    assert p("", "开始时间") is None and f(None) == ""
    # 垃圾 → 报错
    try:
        p("下周三", "开始时间")
        raise AssertionError("非法时间没被拒绝")
    except main.HTTPException as e:
        assert "开始时间" in e.detail and "ISO8601" in e.detail, e.detail

    # ---- 2) create_adset 的 body ----
    sent = []
    real_http = meta_api._http_request
    meta_api._http_request = lambda m, u, data=None, **k: (sent.append(data or {}), ({"id": "a"}, None))[1]
    try:
        meta_api.create_adset("act", "tok", "n", "c", targeting={},
                              start_time="2026-09-20T00:00:00+0000",
                              end_time="2026-09-30T00:00:00+0000")
        assert sent[0].get("start_time") == "2026-09-20T00:00:00+0000", sent[0]
        assert sent[0].get("end_time") == "2026-09-30T00:00:00+0000", sent[0]
        sent.clear()
        meta_api.create_adset("act", "tok", "n", "c", targeting={})     # 没排期
        assert "start_time" not in sent[0] and "end_time" not in sent[0], sent[0]
    finally:
        meta_api._http_request = real_http

    # ---- 1) 真路径：排期一路传到广告组 ----
    got = []
    events = []
    real = {
        "push": delivery._push_event, "token": delivery._get_token,
        "cc": meta_api.create_campaign, "ca": meta_api.create_adset,
        "cad": meta_api.create_ad, "img": meta_api.upload_ad_image,
        "db": {k: getattr(database, k) for k in (
            "create_delivery_campaign", "create_delivery_adset", "add_to_delivery_queue",
            "update_delivery_campaign_fb_id", "update_delivery_adset_fb_id")},
    }
    seq = iter(range(3000, 6000))
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="sched_"))
    try:
        delivery._push_event = lambda b, t, d=None: events.append((t, d or {}))
        delivery._get_token = lambda a, uid=None: "tok"
        meta_api.create_campaign = lambda *a, **k: ("fc", None)
        meta_api.create_ad = lambda *a, **k: ("fa", None)
        meta_api.upload_ad_image = lambda *a, **k: ("h", None)

        def fake_adset(act, tok, name, cid, **k):
            got.append(k)
            return "fa1", None
        meta_api.create_adset = fake_adset

        database.create_delivery_campaign = lambda *a, **k: next(seq)
        database.create_delivery_adset = lambda *a, **k: next(seq)
        database.add_to_delivery_queue = lambda *a, **k: None
        database.update_delivery_campaign_fb_id = lambda *a, **k: None
        database.update_delivery_adset_fb_id = lambda *a, **k: None

        img = tmp / "a.png"; img.write_bytes(b"x")
        params = {
            "ad_account_id": "act_1", "n_campaigns": 1, "n_adsets": 1, "n_ads": 1,
            "assets": [{"image_type": "text_single", "overlay_text": "", "_resolved_path": str(img)}],
            "headlines": ["H"], "ad_name": "Ad", "budget_strategy": "adset",
            "adset_daily_budget": 1000, "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "optimization_goal": "OFFSITE_CONVERSIONS", "pixel_id": "1",
            "page_id": "p", "link_url": "https://x/", "targeting_json": "{}",
            "status": "ACTIVE",
            "start_time": "2026-09-20T00:00:00+0000", "end_time": "2026-09-30T00:00:00+0000",
        }
        bid, err = delivery.submit_batch_publish(params, user_id=1)
        assert not err, err
        delivery._delivery_events[bid].wait(timeout=20)
        assert got, "广告组没被创建"
        assert got[0].get("start_time") == "2026-09-20T00:00:00+0000", got[0]
        assert got[0].get("end_time") == "2026-09-30T00:00:00+0000", got[0]

        # 不排期时不能塞空串（create_adset 里是 if start_time 判断，但这里直接确认一下）
        got.clear()
        p2 = dict(params, start_time="", end_time="")
        bid2, err2 = delivery.submit_batch_publish(p2, user_id=1)
        assert not err2, err2
        delivery._delivery_events[bid2].wait(timeout=20)
        assert not got[0].get("start_time"), f"没排期却传了 {got[0].get('start_time')!r}"
    finally:
        delivery._push_event = real["push"]
        delivery._get_token = real["token"]
        meta_api.create_campaign = real["cc"]
        meta_api.create_adset = real["ca"]
        meta_api.create_ad = real["cad"]
        meta_api.upload_ad_image = real["img"]
        for k, v in real["db"].items():
            setattr(database, k, v)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 4) + 5) 路由：校验与警告 ----
    from fastapi.testclient import TestClient
    captured = {}
    real_submit = delivery.submit_batch_publish
    client = TestClient(main.app, raise_server_exceptions=False)
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 1, "role": "admin"}
    alt = tmp / "b.png"        # 素材路径要在 OUTPUT_ROOT 下才解析得出来
    try:
        # 素材得真存在（resolve_output_path 按 /static/output/<批>/<文件> 解析）
        import shutil as _sh
        batch_dir = main.OUTPUT_ROOT / "9901"
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "b.png").write_bytes(b"x")

        def base_body(**kw):
            b = {
                "ad_account_id": "act_1", "n_campaigns": 1, "n_adsets": 1, "n_ads": 1,
                "assets": [{"image_url": "/static/output/9901/b.png",
                            "image_type": "text_single", "overlay_text": ""}],
                "headlines": ["H"], "ad_name": "Ad", "budget_strategy": "adset",
                "adset_daily_budget": 1000, "page_id": "p", "link_url": "https://x/",
                "pixel_id": "1", "optimization_goal": "OFFSITE_CONVERSIONS",
                "targeting_json": '{"geo_locations": {"countries": ["US"]}}',
                "status": "ACTIVE",
            }
            b.update(kw)
            return b

        def post(**kw):
            captured.clear()
            delivery.submit_batch_publish = lambda params, uid=None: (captured.update(params), ("b1", None))[1]
            r = client.post("/api/delivery/batch-publish", json=base_body(**kw))
            return r.status_code, r.json()

        try:
            # 正常：带 Z 的开始时间 → 规整成 +0000 后传下去
            code, d = post(start_time="2026-09-20T00:00:00.000Z")
            assert code == 200 and d.get("success"), d
            assert captured.get("start_time") == "2026-09-20T00:00:00+0000", captured.get("start_time")
            assert not captured.get("end_time"), "没填结束时间却传了值"

            # 带偏移的入参也换算成 UTC（北京 08:00 → 00:00Z）
            code, d = post(start_time="2026-09-20T08:00:00+08:00")
            assert captured.get("start_time") == "2026-09-20T00:00:00+0000", captured.get("start_time")

            # 结束时间早于开始时间 → 拒绝
            code, d = post(start_time="2026-09-20T00:00:00.000Z",
                           end_time="2026-09-19T00:00:00.000Z")
            assert not d.get("success") and "结束时间必须晚于开始时间" in d.get("message", ""), d

            # 垃圾时间 → 拒绝（不是 500）
            code, d = post(start_time="下周三")
            assert not d.get("success") and "ISO8601" in d.get("message", ""), d

            # 排期在未来 + 状态关闭 → 必须警告（这是最容易踩的坑）
            code, d = post(start_time="2099-09-20T00:00:00.000Z", status="PAUSED")
            assert d.get("success"), d
            assert d.get("warning") and "不会开始投放" in d["warning"], d

            # 排期在未来 + 状态开启 → 不该有多余警告
            code, d = post(start_time="2099-09-20T00:00:00.000Z", status="ACTIVE")
            assert d.get("success") and not d.get("warning"), d

            # 不排期 + 关闭 → 也不该警告（这是正常的存草稿）
            code, d = post(status="PAUSED")
            assert d.get("success") and not d.get("warning"), d
        finally:
            delivery.submit_batch_publish = real_submit
            _sh.rmtree(batch_dir, ignore_errors=True)
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)

    # ---- 6) 静态：前端接线 ----
    # 注意：「本地时间转 UTC」**不在这里测**。原来这里写的是 `"toISOString()" in html`，
    # 那是无效断言 —— index.html 里另外还有 4 处 toISOString（别的日期逻辑），
    # 把 dwLocalToIso 改成返回裸串它照样绿（实测过）。那件事交给
    # scripts/verify_wizard_schedule.js：抠出真函数、真跑一遍、跨时区验证。
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "start_time: dwLocalToIso(" in html and "end_time: dwLocalToIso(" in html, \
        "发布 payload 里没接排期字段"
    assert "dwScheduleIssues" in html, "排期的问题提示丢了"
    assert "到点也不会开始投放" in html, "「排期 + 关闭」的警告文案丢了"

    print("OK: 投放排期（时间规整成 UTC 且不猜服务器时区 / 一路传到广告组 / 没排期就不带字段 / "
          "结束早于开始被拒 / 非法时间报 400 而不是 500 / 排期在未来且状态关闭必须警告 / "
          "前端 payload 已接排期字段；前端的时区换算见 verify_wizard_schedule.js）")


if __name__ == "__main__":
    main()
