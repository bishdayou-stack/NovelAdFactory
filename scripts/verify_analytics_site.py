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

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 看板按 site 过滤，合计=两站相加")

if __name__ == "__main__":
    main()
