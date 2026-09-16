# -*- coding: utf-8 -*-
"""验证：广告组的竞价参数按策略来（最低成本不许带竞价金额）。

线上故障原文（用户贴回来的）：
    建广告组失败: API 错误 [100/1815858]: 你无法使用 LOWEST_COST_WITHOUT_CAP 竞价策略
    来设置广告组的竞价上限。请移除 bid_amount，或者改用包含 bid_amount 的
    LOWEST_COST_WITH_BID_CAP 竞价策略。

根因是 delivery.py 里一句自作聪明的话：
    elif campaign_daily and not ad_bid_amount and bid_strategy == "LOWEST_COST_WITHOUT_CAP":
        ad_bid_amount = 500  # CBO 模式 Meta v25 要求竞价金额（5 美元）
注释写的「Meta 要求」是误判 —— 最低成本策略**禁止**竞价金额。于是「CBO + 最低成本」
这个组合必然建不出广告组（系列能建，广告组一失败广告也不会去建），而 COST_CAP /
ROAS 走不到那个分支，所以那两种好好的 —— 和用户描述的症状完全对上。

覆盖：
  1. 三种策略下 create_adset 实际发出去的 body（拦 _http_request 看真参数）
  2. **走真路径**：submit_batch_publish 全流程跑一遍，抓 create_adset 收到的 bid_amount
     （这条才抓得住 delivery.py 里那句 500）
  3. 前端：预算只发当前策略那一个（ABO 模式下残留的系列预算会把系列变成 CBO）
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import meta_api
    import delivery
    import database

    # ---- 1) create_adset 的 body：按策略决定发不发 bid_amount ----
    sent = []
    real_http = meta_api._http_request

    def fake_http(method, url, data=None, **kw):
        sent.append(data or {})
        return {"id": "adset_1"}, None

    meta_api._http_request = fake_http
    try:
        cases = [
            # (策略, 传进来的 bid_amount, 期望 body 里有没有 bid_amount)
            ("LOWEST_COST_WITHOUT_CAP", 500, False),          # 就是线上那个组合
            ("LOWEST_COST_WITHOUT_CAP", None, False),
            ("LOWEST_COST_WITH_MIN_ROAS", 500, False),        # ROAS 走 bid_constraints
            ("COST_CAP", 500, True),
            ("LOWEST_COST_WITH_BID_CAP", 300, True),
            ("COST_CAP", None, False),                        # 没填就别瞎补，让 Meta 报必填
        ]
        for strat, amt, want in cases:
            sent.clear()
            meta_api.create_adset("act_1", "tok", "n", "cid", targeting={},
                                  bid_strategy=strat, bid_amount=amt)
            got = "bid_amount" in sent[0]
            assert got == want, (
                f"{strat} + bid_amount={amt}：期望{'带' if want else '不带'} bid_amount，实际"
                f"{'带' if got else '不带'} —— 最低成本带上就是 100/1815858")
            if want:
                assert sent[0]["bid_amount"] == str(amt), sent[0]
        # bid_constraints 是另一条路，别被上面的拦截误伤
        sent.clear()
        meta_api.create_adset("act_1", "tok", "n", "cid", targeting={},
                              bid_strategy="LOWEST_COST_WITH_MIN_ROAS",
                              bid_constraints={"roas_average_floor": 20000})
        assert sent[0].get("bid_constraints") == '{"roas_average_floor": 20000}', sent[0]
        assert "bid_amount" not in sent[0], sent[0]
    finally:
        meta_api._http_request = real_http

    # ---- 2) 真路径：submit_batch_publish（CBO + 最低成本）----
    got_adsets = []
    events = []

    real = {
        "get_token": delivery._get_token,
        "push_event": delivery._push_event,
        "cc": meta_api.create_campaign, "ca": meta_api.create_adset,
        "cad": meta_api.create_ad, "img": meta_api.upload_ad_image,
        "c_camp": database.create_delivery_campaign,
        "c_adset": database.create_delivery_adset,
        "q": database.add_to_delivery_queue,
        "u_camp": database.update_delivery_campaign_fb_id,
        "u_adset": database.update_delivery_adset_fb_id,
    }
    seq = iter(range(1000, 9000))
    try:
        delivery._get_token = lambda act, uid=None: "tok"
        delivery._push_event = lambda bid, typ, data=None: events.append((typ, data or {}))
        meta_api.create_campaign = lambda *a, **k: ("fb_c_1", None)
        meta_api.create_ad = lambda *a, **k: ("fb_ad_1", None)
        meta_api.upload_ad_image = lambda *a, **k: ("hash_1", None)

        def fake_create_adset(act, tok, name, cid, **kw):
            got_adsets.append({"name": name, **kw})
            return "fb_a_1", None
        meta_api.create_adset = fake_create_adset

        database.create_delivery_campaign = lambda *a, **k: next(seq)
        database.create_delivery_adset = lambda *a, **k: next(seq)
        database.add_to_delivery_queue = lambda *a, **k: None
        database.update_delivery_campaign_fb_id = lambda *a, **k: None
        database.update_delivery_adset_fb_id = lambda *a, **k: None

        params = {
            "ad_account_id": "act_1", "n_campaigns": 1, "n_adsets": 1, "n_ads": 1,
            "assets": [{"image_type": "text_single", "overlay_text": "",
                        "_resolved_path": __file__}],       # 随便给个存在的文件路径
            "headlines": ["Headline A"], "ad_name": "Ad-1",
            "budget_strategy": "campaign",
            "campaign_daily_budget": 2000,                    # CBO：系列日预算 20 美元
            "adset_daily_budget": 0,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "bid_amount": 0,
            "optimization_goal": "OFFSITE_CONVERSIONS", "pixel_id": "123",
            "page_id": "page_1", "link_url": "https://x.example/",
            "targeting_json": '{"geo_locations": {"countries": ["US"]}}',
            "status": "PAUSED",
        }
        bid, err = delivery.submit_batch_publish(params, user_id=1)
        assert not err, err
        ev = delivery._delivery_events[bid]
        ev.wait(timeout=20)
        assert len(got_adsets) == 1, f"广告组创建次数不对：{len(got_adsets)}"

        a = got_adsets[0]
        assert a.get("bid_amount") in (None, 0), (
            f"CBO + 最低成本仍然把 bid_amount={a.get('bid_amount')} 发给了广告组 —— "
            f"这就是线上 100/1815858 那条错（delivery.py 里那句『补 500』又回来了？）")
        assert a.get("bid_strategy") == "LOWEST_COST_WITHOUT_CAP", a.get("bid_strategy")

        errs = [d.get("error") for t, d in events if d.get("error")]
        assert not errs, f"投放过程中报错：{errs}"
        done = [d for t, d in events if t == "complete"]
        assert done and done[-1].get("completed") == 1, f"没投放成功：{events[-3:]}"

        # 2b) 反向确认：给了竞价上限的策略，bid_amount 得原样传到广告组
        got_adsets.clear()
        params2 = dict(params, bid_strategy="LOWEST_COST_WITH_BID_CAP", bid_amount=350)
        bid2, err2 = delivery.submit_batch_publish(params2, user_id=1)
        assert not err2, err2
        delivery._delivery_events[bid2].wait(timeout=20)
        assert got_adsets[0].get("bid_amount") == 350, got_adsets[0].get("bid_amount")
    finally:
        delivery._get_token = real["get_token"]
        delivery._push_event = real["push_event"]
        meta_api.create_campaign = real["cc"]
        meta_api.create_adset = real["ca"]
        meta_api.create_ad = real["cad"]
        meta_api.upload_ad_image = real["img"]
        database.create_delivery_campaign = real["c_camp"]
        database.create_delivery_adset = real["c_adset"]
        database.add_to_delivery_queue = real["q"]
        database.update_delivery_campaign_fb_id = real["u_camp"]
        database.update_delivery_adset_fb_id = real["u_adset"]

    # ---- 3) 前端：预算只发当前策略那一个 ----
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "_isCbo ? (Math.round(parseFloat(document.getElementById('dwCampaignDailyBudget')" in html, \
        "系列预算没跟预算策略挂钩：ABO 模式下残留的值会让后端按 CBO 建系列"
    assert "_isCbo ? 0 : (Math.round(parseFloat(document.getElementById('dwAdsetDailyBudget')" in html, \
        "广告组预算没跟预算策略挂钩"
    assert "_isCbo ? 1 : 0" in html, "is_adset_budget_sharing_enabled 没跟预算策略挂钩"

    print("OK: 竞价参数按策略发（最低成本/ROAS 不带 bid_amount —— 修掉 100/1815858；"
          "成本上限/竞价上限照常带；真路径跑通并确认广告组不带竞价上限；"
          "前端预算只发当前策略那一个）")


if __name__ == "__main__":
    main()
