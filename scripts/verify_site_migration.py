# -*- coding: utf-8 -*-
"""验证 site 迁移：老库数据不丢、site 默认 'a'、跨站点同 key 可共存。"""
import copy
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# 迁移前的旧 schema（节选关键表，与迁移前 database.py 一致）
# 说明：ad_daily_stats 额外带上线上库真实存在、但不在新 DDL 里的历史列 link_id，
# 用来验证重建时历史列（及其数据）不会被丢掉。
OLD_SCHEMA = """
CREATE TABLE ad_daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date DATE NOT NULL, ad_account TEXT NOT NULL,
    total_spend REAL DEFAULT 0, total_revenue REAL DEFAULT 0, ad_count INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, extra_data TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT DEFAULT 'pingykj',
    meta_account_id TEXT, ctr REAL, cpm REAL, cpc REAL, inline_link_clicks INTEGER,
    inline_link_click_ctr REAL, add_to_cart INTEGER, add_to_cart_cost REAL,
    purchases INTEGER, cost_per_purchase REAL, purchase_value REAL, user_id INTEGER DEFAULT 1,
    link_id TEXT DEFAULT '', UNIQUE(date, ad_account, source, user_id));
CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL,
    order_date DATE, amount REAL, status TEXT, customer_info TEXT, ad_account TEXT,
    extra_data TEXT, user_id INTEGER DEFAULT 1, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE raw_ad_stats (record_id TEXT NOT NULL, stat_date TEXT NOT NULL,
    ad_account_id TEXT NOT NULL, raw_json TEXT NOT NULL, user_id INTEGER DEFAULT 1,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(record_id));
CREATE TABLE raw_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL,
    raw_json TEXT NOT NULL, user_id INTEGER DEFAULT 1, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sync_state (sync_type TEXT NOT NULL, user_id INTEGER NOT NULL DEFAULT 1,
    last_sync_date TEXT, last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sync_type, user_id));
CREATE TABLE account_aliases (account_id TEXT NOT NULL, user_id INTEGER NOT NULL DEFAULT 1,
    alias TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, user_id));
CREATE TABLE novel_books (id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT UNIQUE NOT NULL,
    novel_name TEXT, author TEXT, cover_url TEXT, status TEXT, category TEXT, intro TEXT,
    total_chapters INTEGER DEFAULT 0, create_time TEXT, book_ad_spend REAL DEFAULT 0,
    promotion_link_count INTEGER DEFAULT 0, source TEXT, region TEXT, tags TEXT,
    recommend INTEGER DEFAULT 0, exclusive_status TEXT, create_by TEXT,
    word_count INTEGER DEFAULT 0, collect_num INTEGER DEFAULT 0, locale_code TEXT,
    raw_json TEXT, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE novel_chapters (id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL,
    chapter_no INTEGER, chapter_name TEXT, content TEXT, word_count INTEGER DEFAULT 0,
    raw_json TEXT, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(novel_id, chapter_no));
CREATE TABLE novel_spend_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL,
    snap_date DATE NOT NULL, book_ad_spend REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(novel_id, snap_date));
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, salt TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user');
CREATE TABLE sync_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, sync_type TEXT, status TEXT,
    records_count INTEGER DEFAULT 0, error_message TEXT, user_id INTEGER DEFAULT 1,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, finished_at TIMESTAMP);
"""

SEED = """
INSERT INTO ad_daily_stats (date, ad_account, total_spend, user_id, link_id) VALUES ('2026-09-01','acc1',10.5,1,'L-9');
INSERT INTO orders (order_id, amount, user_id) VALUES ('o1', 9.9, 1);
INSERT INTO raw_ad_stats (record_id, stat_date, ad_account_id, raw_json, user_id) VALUES ('r1','2026-09-01','acc1','{}',1);
INSERT INTO raw_orders (order_id, raw_json, user_id) VALUES ('o1','{}',1);
INSERT INTO sync_state (sync_type, user_id, last_sync_date) VALUES ('ads',1,'2026-09-01');
INSERT INTO account_aliases (account_id, user_id, alias) VALUES ('acc1',1,'别名1');
INSERT INTO novel_books (novel_id, novel_name, book_ad_spend) VALUES ('n1','书1',100.0);
INSERT INTO novel_chapters (novel_id, chapter_no, chapter_name) VALUES ('n1',1,'第一章');
INSERT INTO novel_spend_snapshots (novel_id, snap_date, book_ad_spend) VALUES ('n1','2026-09-01',100.0);
INSERT INTO sync_logs (sync_type, status, user_id) VALUES ('ads','success',1);
"""

EXPECT_ROWS = {
    "ad_daily_stats": 1, "orders": 1, "raw_ad_stats": 1, "raw_orders": 1,
    "sync_state": 1, "account_aliases": 1, "novel_books": 1, "novel_chapters": 1,
    "novel_spend_snapshots": 1, "sync_logs": 1,
}


def main():
    tmpdir = tempfile.mkdtemp(prefix="site_mig_")
    db_file = Path(tmpdir) / "dashboard.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(OLD_SCHEMA)
    conn.executescript(SEED)
    conn.commit()
    conn.close()

    import database
    database.DB_PATH = db_file          # 指向临时库
    database.init_db()

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for table, expect in EXPECT_ROWS.items():
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')")}
        assert "site" in cols, f"{table} 缺 site 列"
        rows = conn.execute(f"SELECT COUNT(*) c, MIN(site) s FROM {table}").fetchone()
        assert rows["c"] == expect, f"{table} 行数 {rows['c']} != {expect}（迁移丢数据）"
        assert rows["s"] == "a", f"{table} 老数据 site={rows['s']} != 'a'"

    # 历史遗留列（不在新 DDL 里）必须连数据一起保留
    ad_cols = {r["name"] for r in conn.execute("PRAGMA table_info('ad_daily_stats')")}
    assert "link_id" in ad_cols, "重建 ad_daily_stats 丢了历史列 link_id"
    assert conn.execute("SELECT link_id FROM ad_daily_stats").fetchone()["link_id"] == "L-9", \
        "重建 ad_daily_stats 丢了 link_id 数据"

    # 跨站点同 key 必须能共存（唯一键已含 site）
    conn.execute("INSERT INTO novel_books (novel_id, novel_name, site) VALUES ('n1','B站同名书','b')")
    conn.execute("INSERT INTO orders (order_id, amount, user_id, site) VALUES ('o1', 1.0, 1, 'b')")
    conn.execute("INSERT INTO sync_state (sync_type, user_id, site) VALUES ('ads',1,'b')")
    n = conn.execute("SELECT COUNT(*) c FROM novel_books WHERE novel_id='n1'").fetchone()["c"]
    assert n == 2, f"跨站点同 novel_id 未共存，got {n}"
    conn.commit()

    # 幂等：再跑一次迁移不应报错、不丢数据
    conn.close()
    database.init_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    n = conn.execute("SELECT COUNT(*) c FROM novel_books").fetchone()["c"]
    assert n == 2, f"重复迁移后行数异常 {n}"
    conn.close()

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK: site 迁移正确（数据不丢、默认 a、跨站共存、幂等）")


if __name__ == "__main__":
    main()
