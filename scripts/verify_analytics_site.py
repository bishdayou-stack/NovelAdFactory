# -*- coding: utf-8 -*-
"""验证看板查询按 site 过滤，site=None 时合计（两站相加）。"""
import shutil, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))
import database, analytics


def main():
    tmp = Path(tempfile.mkdtemp(prefix="site_an_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()
    database.upsert_ad_stats([{"date": "2026-09-01", "ad_account": "acc1", "total_spend": 10.0, "total_revenue": 20.0}], user_id=1, site="a")
    database.upsert_ad_stats([{"date": "2026-09-01", "ad_account": "acc1", "total_spend": 5.0, "total_revenue": 6.0}], user_id=1, site="b")
    # get_summary 的 total_revenue 取自 orders 表，需同步造订单才能验证 orders 侧 where
    database.upsert_orders([{"order_id": "o_a1", "order_date": "2026-09-01", "amount": 20.0, "status": "成功",
                             "customer_info": '{"novelId":"n1","novelName":"BookA"}'}], user_id=1, site="a")
    database.upsert_orders([{"order_id": "o_b1", "order_date": "2026-09-01", "amount": 6.0, "status": "成功",
                             "customer_info": '{"novelId":"n1","novelName":"BookA"}'}], user_id=1, site="b")

    a = analytics.get_summary(user_id=1, site="a")
    b = analytics.get_summary(user_id=1, site="b")
    all_ = analytics.get_summary(user_id=1, site=None)
    assert a["total_spend"] == 10.0, a
    assert b["total_spend"] == 5.0, b
    assert all_["total_spend"] == 15.0, f"合计应为 15，实际 {all_['total_spend']}"
    assert all_["total_revenue"] == 26.0, all_

    # 其余 pingykj 查询同走 _add_site_filter，各点名一次防漏改/错前缀
    assert analytics.get_daily_stats(user_id=1, site="a")["total"] == 1
    assert analytics.get_daily_stats(user_id=1, site=None)["total"] == 2
    assert analytics.get_account_ranking(user_id=1, site="a")["data"][0]["spend"] == 10.0
    assert analytics.get_account_ranking(user_id=1, site=None)["data"][0]["spend"] == 15.0
    assert analytics.get_orders(user_id=1, site="b")["data"][0]["amount"] == 6.0
    assert analytics.get_novel_stats(user_id=1, site="a")["data"][0]["order_count"] == 1
    assert analytics.get_novel_stats(user_id=1, site=None)["data"][0]["order_count"] == 2
    # get_user_ranking 用正向 source='pingykj' + a./o. 别名前缀
    r = analytics.get_user_ranking(site="a")
    assert r[0]["total_spend"] == 10.0 and r[0]["total_revenue"] == 20.0, r
    r = analytics.get_user_ranking(site=None)
    assert r[0]["total_spend"] == 15.0 and r[0]["total_revenue"] == 26.0, r
    # get_accounts 透传到 database（账户来自 raw_ad_stats）
    database.save_raw_ad_stats([{"id": "ra", "statDate": "2026-09-01", "adAccountId": "acc1"}], user_id=1, site="a")
    database.save_raw_ad_stats([{"id": "rb", "statDate": "2026-09-01", "adAccountId": "acc2"}], user_id=1, site="b")
    assert [x["account_id"] for x in analytics.get_accounts(user_id=1, site="a")] == ["acc1"]
    assert len(analytics.get_accounts(user_id=1, site=None)) == 2

    # Ruling 9：合计时起点快照也必须跨站求和，否则 recent_spend 虚高。
    # save_novel_spend_snapshots 固定写今天，故取 start_date=明天 使 snap_before=今天。
    import datetime as _dt
    tmr = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
    database.upsert_novel_books([{"novel_id": "n9", "novel_name": "Book9", "book_ad_spend": 100.0}], site="a")
    database.upsert_novel_books([{"novel_id": "n9", "novel_name": "Book9", "book_ad_spend": 50.0}], site="b")
    database.save_novel_spend_snapshots([{"novel_id": "n9", "book_ad_spend": 40.0}], site="a")
    database.save_novel_spend_snapshots([{"novel_id": "n9", "book_ad_spend": 10.0}], site="b")
    ci9 = '{"novelId":"n9","novelName":"Book9"}'
    database.upsert_orders([{"order_id": "o9a", "order_date": tmr, "amount": 5.0, "status": "成功", "customer_info": ci9}], user_id=1, site="a")
    database.upsert_orders([{"order_id": "o9b", "order_date": tmr, "amount": 5.0, "status": "成功", "customer_info": ci9}], user_id=1, site="b")

    ns = {r["novel_id"]: r for r in analytics.get_novel_stats(start_date=tmr, site=None)["data"]}
    assert ns["n9"]["book_ad_spend"] == 150.0, ns["n9"]       # 两站当前累计相加
    assert ns["n9"]["recent_spend"] == 100.0, ns["n9"]        # 150 - (40+10) 起点两站相加
    ns_a = {r["novel_id"]: r for r in analytics.get_novel_stats(start_date=tmr, site="a")["data"]}
    assert ns_a["n9"]["recent_spend"] == 60.0, ns_a["n9"]     # 100 - 40，只含 A 站

    # 边界：某站缺快照按 0 计入，不得因缺行整体返回 None；两站皆无才 None
    database.save_novel_spend_snapshots([{"novel_id": "n_only_a", "book_ad_spend": 7.0}], site="a")
    assert database.get_novel_spend_snapshot("n_only_a", tmr, site=None) == 7.0
    assert database.get_novel_spend_snapshot("n_only_a", tmr, site="b") is None
    assert database.get_novel_spend_snapshot("n_nowhere", tmr, site=None) is None

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 看板按 site 过滤，合计=两站相加")

if __name__ == "__main__":
    main()
