# -*- coding: utf-8 -*-
"""验证：批量投放的名字规则（系列 / 广告组 = 前缀 + 序号）。

用户的要求：
    系列名前缀填 `9.16-2057723374815436802`，建 3 个系列 →
    9.16-2057723374815436802-1 / -2 / -3

这个格式本来就有，但**前缀留空时会变成 `-1` / `-2` / `-3`**（开头一个光秃秃的横杠）：
    params.get('campaign_name_prefix', 'Campaign')
params 来自 BatchPublishBody.model_dump()，那个键**总是存在**（默认空串），
.get 的第二个参数（默认值）永远拿不到 —— 典型的 .get 误用。广告组名同理（`-1-1`）。

覆盖：
  1. 有前缀：系列/广告组名字带序号，本地库记录和发给 Meta 的**必须一致**
     （不一致的话，你在系统里看到的和 Meta 后台看到的对不上）
  2. 前缀留空 → 退回默认名（Campaign / Adset），不能出现裸的 "-1"
  3. 前缀带首尾空格 → trim 掉
  4. 序号规律：n1 个系列、n2 个广告组 → 系列 -1..-n1，广告组 -i-j
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import delivery
    import database
    import meta_api

    # ---- 规则本身 ----
    f = delivery._seq_name
    P = "9.16-2057723374815436802"
    assert f(P, "Campaign", 1) == P + "-1", f(P, "Campaign", 1)
    assert [f(P, "Campaign", i) for i in (1, 2, 3)] == [P + "-1", P + "-2", P + "-3"]
    assert f(P, "Adset", 2, 3) == P + "-2-3", f(P, "Adset", 2, 3)
    # 留空 → 默认名（这就是线上那个 bug：以前会得到 "-1"）
    assert f("", "Campaign", 1) == "Campaign-1", f("", "Campaign", 1)
    assert f(None, "Campaign", 1) == "Campaign-1", f(None, "Campaign", 1)
    assert f("   ", "Adset", 1, 1) == "Adset-1-1", f("   ", "Adset", 1, 1)
    # 首尾空格 trim（不然名字里会多一个空格，搜都搜不到）
    assert f("  my-camp  ", "Campaign", 1) == "my-camp-1", f("  my-camp  ", "Campaign", 1)
    # 中文前缀也要正常
    assert f("9月16日", "Campaign", 2) == "9月16日-2", f("9月16日", "Campaign", 2)

    # ---- 走真路径：本地记录名 == 发给 Meta 的名字 ----
    local_camps, local_adsets, fb_camps, fb_adsets = [], [], [], []
    real = {
        "cc": meta_api.create_campaign, "ca": meta_api.create_adset, "cad": meta_api.create_ad,
        "img": meta_api.upload_ad_image, "push": delivery._push_event,
        "tok": delivery._get_token,
        "db": {k: getattr(database, k) for k in (
            "create_delivery_campaign", "create_delivery_adset", "add_to_delivery_queue",
            "update_delivery_campaign_fb_id", "update_delivery_adset_fb_id")},
    }
    seq = iter(range(1000, 9999))
    tmp = Path(tempfile.mkdtemp(prefix="names_"))

    def run(prefix, adset_prefix, n1, n2, n3):
        local_camps.clear(); local_adsets.clear(); fb_camps.clear(); fb_adsets.clear()
        delivery._push_event = lambda *a, **k: None
        delivery._get_token = lambda a, uid=None: "tok"
        meta_api.create_campaign = lambda act, tok, name, **k: (fb_camps.append(name), ("fc", None))[1]
        meta_api.create_adset = lambda act, tok, name, cid, **k: (fb_adsets.append(name), ("fa", None))[1]
        meta_api.create_ad = lambda *a, **k: ("fad", None)
        meta_api.upload_ad_image = lambda *a, **k: ("h", None)
        database.create_delivery_campaign = lambda name, **k: (local_camps.append(name), next(seq))[1]
        database.create_delivery_adset = lambda cid, name, **k: (local_adsets.append(name), next(seq))[1]
        database.add_to_delivery_queue = lambda *a, **k: None
        database.update_delivery_campaign_fb_id = lambda *a, **k: None
        database.update_delivery_adset_fb_id = lambda *a, **k: None
        total = n1 * n2 * n3
        assets = []
        for i in range(total):
            p = tmp / f"x{i}.png"
            p.write_bytes(b"x")
            assets.append({"image_type": "text_single", "overlay_text": "", "_resolved_path": str(p)})
        bid, err = delivery.submit_batch_publish({
            "ad_account_id": "act_1", "n_campaigns": n1, "n_adsets": n2, "n_ads": n3,
            "assets": assets, "headlines": ["H"], "ad_name": "Ad", "budget_strategy": "adset",
            "adset_daily_budget": 1000, "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "optimization_goal": "OFFSITE_CONVERSIONS", "pixel_id": "1", "page_id": "p",
            "link_url": "https://x/", "targeting_json": "{}", "status": "PAUSED",
            "campaign_name_prefix": prefix, "adset_name_prefix": adset_prefix,
        }, user_id=1)
        assert not err, err
        delivery._delivery_events[bid].wait(timeout=20)
        return (list(local_camps), list(local_adsets)), (list(fb_camps), list(fb_adsets))

    try:
        # 1) 用户要的效果：前缀 + 3 个系列 → 前缀-1/-2/-3
        (lc, la), (fc, fa) = run(P, P + "-G", 3, 1, 1)
        assert fc == [P + "-1", P + "-2", P + "-3"], f"Meta 那边的系列名不对：{fc}"
        assert fa == [P + "-G-1-1", P + "-G-2-1", P + "-G-3-1"], f"广告组名不对：{fa}"
        assert (lc, la) == (fc, fa), f"本地记录名和发给 Meta 的不一致：本地 {lc} {la} / Meta {fc} {fa}"
        assert not any(str(x).startswith("-") for x in fc + fa), f"出现了裸的 '-1'：{fc + fa}"

        # 2) 前缀留空（用户忘了填）→ 退回默认名，不是 "-1"
        (lc, la), (fc, fa) = run("", "", 3, 1, 1)
        assert fc == ["Campaign-1", "Campaign-2", "Campaign-3"], f"前缀留空应退回默认名，实际：{fc}"
        assert fa == ["Adset-1-1", "Adset-2-1", "Adset-3-1"], f"广告组默认名不对：{fa}"
        assert (lc, la) == (fc, fa), (lc, la, fc, fa)

        # 3) 系列 + 广告组都带序号
        (lc, la), (fc, fa) = run(P, "G", 2, 2, 1)
        assert fc == [P + "-1", P + "-2"], f"系列名不对：{fc}"
        assert fa == ["G-1-1", "G-1-2", "G-2-1", "G-2-2"], f"广告组名不对：{fa}"
        assert (lc, la) == (fc, fa), f"本地/Meta 不一致：本地 {lc} {la} / Meta {fc} {fa}"

        # 4) 前缀带首尾空格 → trim 后拼（不然名字里会多一个空格，搜都搜不到）
        (lc, la), (fc, fa) = run("  " + P + "  ", " G ", 1, 1, 1)
        assert fc == [P + "-1"], f"系列前缀没 trim：{fc}"
        assert fa == ["G-1-1"], f"广告组前缀没 trim：{fa}"
    finally:
        meta_api.create_campaign = real["cc"]
        meta_api.create_adset = real["ca"]
        meta_api.create_ad = real["cad"]
        meta_api.upload_ad_image = real["img"]
        delivery._push_event = real["push"]
        delivery._get_token = real["tok"]
        for k, v in real["db"].items():
            setattr(database, k, v)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK: 批量投放命名（前缀+序号 → '前缀-1/-2/-3'；前缀留空退回默认名而不是裸 '-1'；"
          "首尾空格 trim；系列和广告组都带序号；本地记录名与发给 Meta 的名字完全一致）")


if __name__ == "__main__":
    main()
