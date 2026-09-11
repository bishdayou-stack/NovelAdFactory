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

    test_failure_injection()
    test_half_migrated()
    test_fresh_db()


def test_fresh_db():
    """全新库首启：_migrate_user_isolation 建出的 users 表也必须补齐 pingykj_offline_at 等列。

    否则首启时 database.list_users()（= 管理页 /api/users 的数据源）直接
    sqlite3.OperationalError: no such column: pingykj_offline_at，重启一次才自愈。
    """
    tmpdir = tempfile.mkdtemp(prefix="site_mig_fresh_")
    db_file = Path(tmpdir) / "dashboard.db"
    assert not db_file.exists(), "前置：这里必须是全新库"

    import database
    database.DB_PATH = db_file
    database.init_db()                       # 全新库走 _migrate_user_isolation 建 users 表

    users = database.list_users()            # 修复前抛 no such column: pingykj_offline_at
    assert users, "全新库至少应存在默认 admin"

    conn = sqlite3.connect(str(db_file))
    cols = {r[1] for r in conn.execute("PRAGMA table_info('users')")}
    conn.close()
    for col in ("pingykj_offline_at", "last_login_at", "last_login_ip"):
        assert col in cols, f"全新库 users 表缺列 {col}"

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK: 全新库首启 users 表列齐备，list_users() 不抛异常")


def test_half_migrated():
    """半迁移状态：site 列已在、唯一键仍不含 site，必须继续重建（只看列的哨兵会跳过并永久修不好）。"""
    tmpdir = tempfile.mkdtemp(prefix="site_mig_half_")
    db_file = Path(tmpdir) / "dashboard.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(OLD_SCHEMA)
    conn.executescript(SEED)
    conn.execute("ALTER TABLE novel_books ADD COLUMN site TEXT NOT NULL DEFAULT 'a'")
    conn.commit()
    conn.close()

    import database
    database.DB_PATH = db_file
    database.init_db()

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    keys = [[x[2] for x in conn.execute(f"PRAGMA index_info('{r['name']}')")]
            for r in conn.execute("PRAGMA index_list('novel_books')") if r["unique"]]
    assert any("site" in k for k in keys), f"半迁移表未被重建，唯一键仍是 {keys}"
    conn.execute("INSERT INTO novel_books (novel_id, novel_name, site) VALUES ('n1','B站同名书','b')")
    n = conn.execute("SELECT COUNT(*) c FROM novel_books WHERE novel_id='n1'").fetchone()["c"]
    assert n == 2, f"半迁移表重建后跨站点同 novel_id 仍不能共存，got {n}"
    conn.commit()
    conn.close()

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK: 半迁移表（有 site 列、唯一键无 site）会被继续重建")


def test_failure_injection():
    """注入一次建表失败：init_db 抛错后必须整体回滚（原表还在、行数不变、无 __pre_site 残留）。

    前提：python sqlite3 的隐式 BEGIN 只覆盖 DML，DDL 默认 autocommit，
    所以迁移必须自己显式 BEGIN，否则中途失败会留下已重建的空表 + 孤儿 __pre_site。
    """
    tmpdir = tempfile.mkdtemp(prefix="site_mig_fail_")
    db_file = Path(tmpdir) / "dashboard.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(OLD_SCHEMA)
    conn.executescript(SEED)
    conn.commit()
    conn.close()

    import database
    database.DB_PATH = db_file
    good_ddl = database._SITE_REBUILD_DDL
    bad_ddl = dict(good_ddl)
    bad_ddl["orders"] = "CREATE TABLE orders (这不是合法的 DDL"   # 第 2 张表炸，第 1 张也要跟着回滚
    database._SITE_REBUILD_DDL = bad_ddl
    try:
        database.init_db()
        raise AssertionError("注入建表失败后 init_db 竟然没抛错")
    except sqlite3.OperationalError:
        pass
    finally:
        database._SITE_REBUILD_DDL = good_ddl

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    left = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '%__pre_site'")]
    assert not left, f"回滚不干净，残留临时表 {left}"
    for table, expect in (("ad_daily_stats", 1), ("orders", 1)):
        n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        assert n == expect, f"{table} 行数 {n} != {expect}（迁移中途失败丢数据）"
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')")}
        assert "site" not in cols, f"{table} 未回滚（site 列已落库，重启后不会再迁移）"
    conn.close()

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK: 迁移中途失败会整体回滚（行数不变、无空表、无 __pre_site 残留）")


if __name__ == "__main__":
    main()
