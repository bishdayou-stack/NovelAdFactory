# -*- coding: utf-8 -*-
"""验证：素材按「用户点选的顺序」按块分配给各系列。

用户场景：选了 9 个素材（其中 3 个视频），想每个系列分到一个视频。
实现里素材是**按块**填的：前 n2×n3 个给系列1，再给系列2……

配套的前端两件事（同一个例子，两边必须一致）：
  - 前端提交时**不再排序**（Set 本身就按插入顺序迭代 = 点选顺序）。
    原来有一句 `.sort((a,b)=>a-b)` 把点击顺序抹掉、改按素材池下标排，
    那样用户根本没法靠"先点视频"控制视频落在哪个系列。
  - 第 4 步预览按同一个公式列出「每个系列分到哪几个素材」（见 verify_wizard_preview.js）。

覆盖：
  1. **走真路径**：submit_batch_publish 全流程，断言第 i 个系列实际用的就是
     assets[i*n2*n3 : (i+1)*n2*n3]（按点选顺序）
  2. 表驱动地把几种 (n1,n2,n3) 的映射钉死
  3. 静态：提交处不许再出现对 _dwSelected 的排序
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

    # 同一个例子，前端预览测试里也用这一组（两边必须给出一样的分配）
    # 点选顺序：先点视频 V1，再两张图，再视频 V2…… 结果应当是每系列一个视频
    CLICK_ORDER = [6, 0, 1, 7, 2, 3, 8, 4, 5]
    VIDEO_IDX = {6, 7, 8}

    def sim(n1, n2, n3, order):
        """按后台公式算出每个系列拿到哪些下标（点选顺序里的位置）。"""
        per = n2 * n3
        return [order[i * per:(i + 1) * per] for i in range(n1)]

    # ---- 2) 表驱动把映射钉死 ----
    cases = [
        # (n1, n2, n3, 点选顺序, 期望的分配)
        (3, 1, 3, CLICK_ORDER, [[6, 0, 1], [7, 2, 3], [8, 4, 5]]),
        (2, 1, 2, [0, 1, 2, 3], [[0, 1], [2, 3]]),
        (3, 1, 1, [5, 4, 3], [[5], [4], [3]]),
        (1, 2, 2, [0, 1, 2, 3], [[0, 1, 2, 3]]),
    ]
    for n1, n2, n3, order, want in cases:
        got = sim(n1, n2, n3, order)
        assert got == want, f"({n1},{n2},{n3}) 分配不符：{got} != {want}"
    # 每系列一个视频（用户要的效果）
    groups = sim(3, 1, 3, CLICK_ORDER)
    assert [len([i for i in g if i in VIDEO_IDX]) for g in groups] == [1, 1, 1], \
        f"这个点选顺序应该每系列 1 个视频：{groups}"

    # ---- 1) 真路径：素材真的按这个映射落到广告上 ----
    events = []
    real = {
        "push": delivery._push_event, "token": delivery._get_token,
        "cc": meta_api.create_campaign, "ca": meta_api.create_adset,
        "cad": meta_api.create_ad, "img": meta_api.upload_ad_image,
        "db": {k: getattr(database, k) for k in (
            "create_delivery_campaign", "create_delivery_adset", "add_to_delivery_queue",
            "update_delivery_campaign_fb_id", "update_delivery_adset_fb_id")},
    }
    seq = iter(range(7000, 9999))
    ad_seen = []          # (adset_id, 素材下标)
    tmpdir = Path(tempfile.mkdtemp(prefix="assets_"))
    try:
        delivery._push_event = lambda b, t, d=None: events.append((t, d or {}))
        delivery._get_token = lambda a, uid=None: "tok"
        meta_api.create_campaign = lambda *a, **k: ("fb_c", None)
        meta_api.create_adset = lambda act, tok, name, cid, **k: (f"adset_{name}", None)
        meta_api.upload_ad_image = lambda act, tok, path: (Path(path).stem, None)   # hash 里带上素材名
        def fake_create_ad(act, tok, name, adset_id, **k):
            ad_seen.append((adset_id, k.get("image_hash")))
            return "fb_ad", None          # 必须是二元组：调用处是 `fb_ad_id, err = ...`
        meta_api.create_ad = fake_create_ad
        database.create_delivery_campaign = lambda *a, **k: next(seq)
        database.create_delivery_adset = lambda *a, **k: next(seq)
        database.add_to_delivery_queue = lambda *a, **k: None
        database.update_delivery_campaign_fb_id = lambda *a, **k: None
        database.update_delivery_adset_fb_id = lambda *a, **k: None

        # 素材按**点选顺序**排好传进来（前端就是按这个顺序发的）
        names = [f"asset{i}" for i in range(9)]
        ordered = [names[i] for i in CLICK_ORDER]
        assets = []
        for nm in ordered:
            p = tmpdir / f"{nm}.png"
            p.write_bytes(b"x")
            assets.append({"image_type": "text_single", "overlay_text": "", "_resolved_path": str(p)})

        n1, n2, n3 = 3, 1, 3
        params = {
            "ad_account_id": "act_1", "n_campaigns": n1, "n_adsets": n2, "n_ads": n3,
            "assets": assets, "headlines": ["H"], "ad_name": "Ad",
            "budget_strategy": "adset", "adset_daily_budget": 1000,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "optimization_goal": "OFFSITE_CONVERSIONS",
            "pixel_id": "1", "page_id": "p", "link_url": "https://x/", "targeting_json": "{}",
            "status": "PAUSED",
        }
        bid, err = delivery.submit_batch_publish(params, user_id=1)
        assert not err, err
        delivery._delivery_events[bid].wait(timeout=20)
        assert len(ad_seen) == 9, f"应该建 9 条广告，实际 {len(ad_seen)}"

        # 后台建的广告组名字里带系列号（Adset-<i+1>-<j+1>），据此把广告归回系列
        by_camp = {}
        name2idx = {nm: pos for pos, nm in enumerate(ordered)}   # 素材名 → 在点选顺序里的位置
        for adset_id, hash_name in ad_seen:
            camp = int(adset_id.split("Adset-")[1].split("-")[0]) - 1
            by_camp.setdefault(camp, []).append(name2idx[hash_name])
        for i in range(n1):
            got = by_camp.get(i, [])
            want = sim(n1, n2, n3, list(range(9)))[i]
            assert got == want, (
                f"系列{i+1} 实际拿到素材位置 {got}，按公式应该是 {want} —— "
                f"预览显示的分配和实际投出去的对不上")
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
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- 3) 静态：提交处不许再排序 ----
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "Array.from(_dwSelected)" in html, "找不到提交处的 _dwSelected 取值"
    import re
    bad = re.findall(r"Array\.from\(_dwSelected\)\s*\.\s*sort", html)
    assert not bad, (
        "提交时又对 _dwSelected 排序了 —— JS 的 Set 本来就按插入顺序迭代（= 点选顺序），"
        "一排序就变回按素材池下标排，用户「先点视频」的意图就丢了，"
        "第 4 步预览显示的分配也会和实际投出去的不一致")
    assert "dwRenderPreview" in html and "dwThumbHtml" in html, "预览没接上"

    print("OK: 素材按点选顺序按块分配（真路径验证第 i 个系列拿到 assets[i*n2*n3:(i+1)*n2*n3]；"
          "表驱动钉死 4 种 (系列,组,广告) 组合；提交处不再排序；预览已接上）")


if __name__ == "__main__":
    main()
