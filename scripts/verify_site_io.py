# -*- coding: utf-8 -*-
"""验证读写函数按 site 隔离、site=None 时合计。"""
import shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))
import database

def main():
    tmp = Path(tempfile.mkdtemp(prefix="site_io_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()

    database.upsert_ad_stats([{"date": "2026-09-01", "ad_account": "acc1", "total_spend": 10.0}], user_id=1, site="a")
    database.upsert_ad_stats([{"date": "2026-09-01", "ad_account": "acc1", "total_spend": 5.0}], user_id=1, site="b")
    database.upsert_novel_books([{"novel_id": "n1", "novel_name": "书1", "book_ad_spend": 100.0}], site="a")
    database.upsert_novel_books([{"novel_id": "n1", "novel_name": "书1", "book_ad_spend": 40.0}], site="b")

    # 单站点
    books_a = database.get_novel_books(site="a")["data"]
    assert len(books_a) == 1 and books_a[0]["book_ad_spend"] == 100.0, books_a
    books_b = database.get_novel_books(site="b")["data"]
    assert len(books_b) == 1 and books_b[0]["book_ad_spend"] == 40.0, books_b

    # 合计 = 不过滤：同 novel_id 去重后消耗相加
    books_all = database.get_novel_books(site=None)["data"]
    assert len(books_all) == 1, f"合计应去重为 1 条，实际 {len(books_all)}"
    assert books_all[0]["book_ad_spend"] == 140.0, f"合计消耗应相加为 140，实际 {books_all[0]['book_ad_spend']}"

    # 订单也必须按站点隔离：A 站 2 单、B 站 3 单，不可跨站串单、不可被 JOIN 放大
    database.upsert_orders([
        {"order_id": "A1", "customer_info": {"novelId": "n1"}, "status": "成功"},
        {"order_id": "A2", "customer_info": {"novelId": "n1"}, "status": "成功"},
    ], user_id=1, site="a")
    database.upsert_orders([
        {"order_id": "B1", "customer_info": {"novelId": "n1"}, "status": "成功"},
        {"order_id": "B2", "customer_info": {"novelId": "n1"}, "status": "成功"},
        {"order_id": "B3", "customer_info": {"novelId": "n1"}, "status": "成功"},
    ], user_id=1, site="b")

    ba = database.get_novel_books(site="a")["data"][0]
    assert ba["order_count"] == 2, f"A 站订单数应只含 A 站 2 单，实际 {ba['order_count']}"
    assert ba["conversion_cost"] == 50.0, f"A 站转化成本应为 100/2=50.0，实际 {ba['conversion_cost']}"
    bb = database.get_novel_books(site="b")["data"][0]
    assert bb["order_count"] == 3, f"B 站订单数应只含 B 站 3 单，实际 {bb['order_count']}"
    assert bb["conversion_cost"] == round(40 / 3, 2), f"B 站转化成本应为 40/3，实际 {bb['conversion_cost']}"
    ball = database.get_novel_books(site=None)["data"][0]
    assert ball["order_count"] == 5, f"合计订单数应为 2+3=5（不得被 JOIN 放大），实际 {ball['order_count']}"
    assert ball["conversion_cost"] == 28.0, f"合计转化成本应为 140/5=28.0，实际 {ball['conversion_cost']}"

    # sync_state 按站点独立
    database.set_last_sync_date("ads", "2026-09-01", user_id=1, site="a")
    database.set_last_sync_date("ads", "2026-09-02", user_id=1, site="b")
    assert database.get_last_sync_date("ads", user_id=1, site="a") == "2026-09-01"
    assert database.get_last_sync_date("ads", user_id=1, site="b") == "2026-09-02"

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 读写按 site 隔离，合计=去重+相加")

if __name__ == "__main__":
    main()
