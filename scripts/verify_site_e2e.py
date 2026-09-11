# -*- coding: utf-8 -*-
"""Task 9 端到端验证：合成数据 + 临时库，一次跑穿 database / analytics / 路由三层。

证明三件事：
1) 隔离：site='a' 只见 A 站、site='b' 只见 B 站，B 站看不到只在 A 站存在的书与订单
2) 合计：site=None 时同一本小说只有一行，消耗 = 两站相加、订单数 = 两站相加
3) 比率：合计的 roi/cpa 用合计后的分子分母重算，**不是**两站比率的平均或相加

另有 TestClient 部分，证明 /api/dashboard/summary 与 /api/novels/list 的返回确实随 site 变化。
用 dependency_overrides 注入登录态——不猜、不改任何用户口令。

跑法：python scripts/verify_site_e2e.py
只用 tempfile 临时库；不碰真实 data/dashboard.db，不联网。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

import database  # noqa: E402

# DB_PATH 必须在 import main 之前重定向：main 模块级会 init_db() 并启动调度器
_TMP = Path(tempfile.mkdtemp(prefix="site_e2e_"))
database.DB_PATH = _TMP / "dashboard.db"
database.init_db()

import analytics  # noqa: E402
import main  # noqa: E402

A_SPEND, B_SPEND, N2_SPEND = 100.0, 40.0, 70.0
A_ORDERS, B_ORDERS = [30.0, 20.0], [10.0, 10.0, 10.0]
DATE = "2026-09-01"


def _ci(nid):
    return {"novelId": nid, "novelName": nid}


def seed():
    """n1 两站都有；n2 只在 A 站；消耗与订单按站分写。"""
    database.upsert_novel_books(
        [{"novel_id": "n1", "novel_name": "书1", "book_ad_spend": A_SPEND},
         {"novel_id": "n2", "novel_name": "书2", "book_ad_spend": N2_SPEND}], site="a")
    database.upsert_novel_books(
        [{"novel_id": "n1", "novel_name": "书1", "book_ad_spend": B_SPEND}], site="b")

    database.upsert_ad_stats([{"date": DATE, "ad_account": "acc1", "total_spend": A_SPEND}],
                             user_id=1, site="a")
    database.upsert_ad_stats([{"date": DATE, "ad_account": "acc1", "total_spend": B_SPEND}],
                             user_id=1, site="b")

    database.upsert_orders(
        [{"order_id": f"A{i}", "order_date": DATE, "amount": amt, "status": "成功",
          "customer_info": _ci("n1")} for i, amt in enumerate(A_ORDERS, 1)],
        user_id=1, site="a")
    database.upsert_orders(
        [{"order_id": f"B{i}", "order_date": DATE, "amount": amt, "status": "成功",
          "customer_info": _ci("n1")} for i, amt in enumerate(B_ORDERS, 1)],
        user_id=1, site="b")


def check_novel_books():
    a = {b["novel_id"]: b for b in database.get_novel_books(page_size=99, site="a")["data"]}
    b = {x["novel_id"]: x for x in database.get_novel_books(page_size=99, site="b")["data"]}
    st = {x["novel_id"]: x for x in database.get_novel_books(page_size=99, site=None)["data"]}

    # A 站：n1 + 只属于 A 的 n2
    assert set(a) == {"n1", "n2"}, f"A 站书目 {set(a)}"
    assert a["n1"]["book_ad_spend"] == A_SPEND, a["n1"]
    assert a["n1"]["order_count"] == len(A_ORDERS), a["n1"]

    # 隔离：B 站看不到 n2，也看不到 A 站的订单
    assert set(b) == {"n1"}, f"B 站不得出现只在 A 站存在的 n2：{set(b)}"
    assert b["n1"]["book_ad_spend"] == B_SPEND, b["n1"]
    assert b["n1"]["order_count"] == len(B_ORDERS), f"B 站订单数应只含 B 站 3 单：{b['n1']}"

    # 合计：n1 只有一行，消耗与订单数 = 两站相加
    assert sorted(st) == ["n1", "n2"], sorted(st)
    assert "n1" in st and len([k for k in st if k == "n1"]) == 1
    assert st["n1"]["book_ad_spend"] == A_SPEND + B_SPEND, \
        f"合计消耗应为 {A_SPEND + B_SPEND}，实际 {st['n1']['book_ad_spend']}"
    assert st["n1"]["order_count"] == len(A_ORDERS) + len(B_ORDERS), \
        f"合计订单数应为 5（不得被 JOIN 放大），实际 {st['n1']['order_count']}"
    assert st["n1"]["conversion_cost"] == round((A_SPEND + B_SPEND) / 5, 2), st["n1"]
    assert st["n2"]["book_ad_spend"] == N2_SPEND and st["n2"]["order_count"] == 0, st["n2"]
    print("OK: novel_books 按 site 隔离，合计 = 去重 + 消耗/订单相加")


def check_summary():
    sa = analytics.get_summary(user_id=1, site="a")
    sb = analytics.get_summary(user_id=1, site="b")
    st = analytics.get_summary(user_id=1, site=None)

    assert sa["total_spend"] == A_SPEND and sb["total_spend"] == B_SPEND, (sa, sb)
    assert sa["total_revenue"] == sum(A_ORDERS), sa
    assert sb["total_revenue"] == sum(B_ORDERS), sb
    assert sa["order_count"] == len(A_ORDERS) and sb["order_count"] == len(B_ORDERS), (sa, sb)

    # 合计 = 两站之和
    assert st["total_spend"] == A_SPEND + B_SPEND, f"合计消耗应为 140，实际 {st['total_spend']}"
    assert st["total_revenue"] == sum(A_ORDERS) + sum(B_ORDERS), st
    assert st["order_count"] == len(A_ORDERS) + len(B_ORDERS), st

    # 比率必须由合计后的分子/分母重算 —— 这是最容易写错的地方
    assert st["roi"] == round(st["total_revenue"] / st["total_spend"], 2) == 0.57, st["roi"]
    assert st["cpa"] == round(st["total_spend"] / st["order_count"], 2) == 28.0, st["cpa"]
    assert st["subscribe_roi"] == round(st["subscribe_amount"] / st["total_spend"], 2), st
    assert sa["roi"] == 0.5 and sb["roi"] == 0.75, (sa["roi"], sb["roi"])

    # 反向锁死：合计比率不得等于两站比率的相加或平均
    bad_roi = {round(sa["roi"] + sb["roi"], 2), round((sa["roi"] + sb["roi"]) / 2, 2)}
    bad_cpa = {round(sa["cpa"] + sb["cpa"], 2), round((sa["cpa"] + sb["cpa"]) / 2, 2)}
    assert st["roi"] not in bad_roi, f"合计 roi={st['roi']} 落在两站比率的相加/平均里 {bad_roi}"
    assert st["cpa"] not in bad_cpa, f"合计 cpa={st['cpa']} 落在两站比率的相加/平均里 {bad_cpa}"
    print(f"OK: get_summary 合计=两站之和，roi={st['roi']}（非平均/相加）、cpa={st['cpa']}")


def check_routes():
    from fastapi.testclient import TestClient

    # 注入登录态，不依赖也不改动任何真实口令
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 1, "role": "admin"}
    c = TestClient(main.app)

    def summary(site=None):
        r = c.get("/api/dashboard/summary", params=({"site": site} if site else {}))
        assert r.status_code == 200, f"summary?site={site} -> {r.status_code} {r.text[:200]}"
        return r.json()

    def books(site=None):
        p = {"page_size": 99}
        if site:
            p["site"] = site
        r = c.get("/api/novels/list", params=p)
        assert r.status_code == 200, f"novels/list?site={site} -> {r.status_code} {r.text[:200]}"
        return {x["novel_id"]: x for x in r.json()["data"]}

    # 返回确实随 site 变化
    assert summary("a")["total_spend"] == A_SPEND, summary("a")
    assert summary("b")["total_spend"] == B_SPEND, summary("b")
    assert summary("a")["order_count"] == 2 and summary("b")["order_count"] == 3
    # 不带 site = 合计 = 两站之和
    assert summary()["total_spend"] == A_SPEND + B_SPEND, summary()
    assert summary()["order_count"] == 5, summary()
    assert summary()["roi"] == round(80.0 / 140.0, 2), summary()

    assert set(books("a")) == {"n1", "n2"}, books("a")
    assert set(books("b")) == {"n1"}, f"B 站路由不得返回 A 站专有的 n2：{books('b')}"
    assert books()["n1"]["book_ad_spend"] == A_SPEND + B_SPEND, books()["n1"]
    assert books()["n1"]["order_count"] == 5, books()["n1"]
    assert books("b")["n1"]["book_ad_spend"] == B_SPEND, books("b")

    main.app.dependency_overrides.clear()
    print("OK: 路由 /api/dashboard/summary 与 /api/novels/list 随 site 变化，合计=两站之和")


def main_():
    seed()
    check_novel_books()
    check_summary()
    check_routes()


if __name__ == "__main__":
    try:
        main_()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("OK: 多书城端到端验证通过（隔离 / 合计=相加 / 比率重算 / 路由透传）")
