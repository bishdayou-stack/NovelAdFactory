import sqlite3
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH: Path = None

def _get_db_path() -> Path:
    global DB_PATH
    if DB_PATH is None:
        base = Path(__file__).parent.resolve()
        db_dir = base / "data"
        db_dir.mkdir(exist_ok=True)
        DB_PATH = db_dir / "dashboard.db"
    return DB_PATH

@contextmanager
def get_conn():
    """获取数据库连接，自动提交/回滚/关闭，保证异常安全"""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ====== Schema ======

def init_db() -> None:
    with get_conn() as conn:
        # 迁移：为 novel_books 补加新列
        _new_columns = {
            "create_time": "TEXT",
            "book_ad_spend": "REAL DEFAULT 0",
            "promotion_link_count": "INTEGER DEFAULT 0",
            "source": "TEXT",
            "region": "TEXT",
            "tags": "TEXT",
            "recommend": "INTEGER DEFAULT 0",
            "exclusive_status": "TEXT",
            "create_by": "TEXT",
            "word_count": "INTEGER DEFAULT 0",
            "collect_num": "INTEGER DEFAULT 0",
            "locale_code": "TEXT",
        }
        existing = {r["name"] for r in conn.execute("PRAGMA table_info('novel_books')").fetchall()}
        for col_name, col_def in _new_columns.items():
            if col_name not in existing:
                try:
                    conn.execute(f"ALTER TABLE novel_books ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass

        # 迁移：为 ad_daily_stats 新增 Meta 指标列 + source 列
        _ad_stats_new_columns = {
            "source": "TEXT DEFAULT 'pingykj'",
            "meta_account_id": "TEXT",
            "ctr": "REAL",
            "cpm": "REAL",
            "cpc": "REAL",
            "inline_link_clicks": "INTEGER",
            "inline_link_click_ctr": "REAL",
            "add_to_cart": "INTEGER",
            "add_to_cart_cost": "REAL",
            "purchases": "INTEGER",
            "cost_per_purchase": "REAL",
            "purchase_value": "REAL",
        }
        existing_ad = {r["name"] for r in conn.execute("PRAGMA table_info('ad_daily_stats')").fetchall()}
        for col_name, col_def in _ad_stats_new_columns.items():
            if col_name not in existing_ad:
                try:
                    conn.execute(f"ALTER TABLE ad_daily_stats ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass

        # 迁移：重建唯一约束为 (date, ad_account, source)
        try:
            existing_indexes = [r["name"] for r in conn.execute("PRAGMA index_list('ad_daily_stats')").fetchall()]
            if "sqlite_autoindex_ad_daily_stats_1" in existing_indexes:
                pragma_info = conn.execute("PRAGMA index_info('sqlite_autoindex_ad_daily_stats_1')").fetchall()
                if len(pragma_info) == 2:
                    conn.execute("ALTER TABLE ad_daily_stats RENAME TO ad_daily_stats_old")
                    conn.execute("""
                        CREATE TABLE ad_daily_stats (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date DATE NOT NULL,
                            ad_account TEXT NOT NULL,
                            total_spend REAL DEFAULT 0,
                            total_revenue REAL DEFAULT 0,
                            ad_count INTEGER DEFAULT 0,
                            impressions INTEGER DEFAULT 0,
                            clicks INTEGER DEFAULT 0,
                            extra_data TEXT,
                            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            source TEXT DEFAULT 'pingykj',
                            meta_account_id TEXT,
                            ctr REAL, cpm REAL, cpc REAL,
                            inline_link_clicks INTEGER,
                            inline_link_click_ctr REAL,
                            add_to_cart INTEGER,
                            add_to_cart_cost REAL,
                            purchases INTEGER,
                            cost_per_purchase REAL,
                            purchase_value REAL,
                            UNIQUE(date, ad_account, source)
                        )
                    """)
                    conn.execute("""
                        INSERT INTO ad_daily_stats (
                            id, date, ad_account, total_spend, total_revenue, ad_count,
                            impressions, clicks, extra_data, synced_at
                        ) SELECT id, date, ad_account, total_spend, total_revenue, ad_count,
                            impressions, clicks, extra_data, synced_at
                        FROM ad_daily_stats_old
                    """)
                    conn.execute("DROP TABLE ad_daily_stats_old")
        except Exception:
            pass

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS login_session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cookies TEXT,
                username TEXT,
                expires_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ad_daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                ad_account TEXT NOT NULL,
                total_spend REAL DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                ad_count INTEGER DEFAULT 0,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                extra_data TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'pingykj',
                meta_account_id TEXT,
                ctr REAL,
                cpm REAL,
                cpc REAL,
                inline_link_clicks INTEGER,
                inline_link_click_ctr REAL,
                add_to_cart INTEGER,
                add_to_cart_cost REAL,
                purchases INTEGER,
                cost_per_purchase REAL,
                purchase_value REAL,
                UNIQUE(date, ad_account, source)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                order_date DATE,
                amount REAL,
                status TEXT,
                customer_info TEXT,
                ad_account TEXT,
                extra_data TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT,
                status TEXT,
                records_count INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS raw_ad_stats (
                record_id TEXT NOT NULL,
                stat_date TEXT NOT NULL,
                ad_account_id TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(record_id)
            );

            CREATE TABLE IF NOT EXISTS raw_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                raw_json TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                sync_type TEXT PRIMARY KEY,
                last_sync_date TEXT,
                last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS account_aliases (
                account_id TEXT PRIMARY KEY,
                alias TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS novel_books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT UNIQUE NOT NULL,
                novel_name TEXT,
                author TEXT,
                cover_url TEXT,
                status TEXT,
                category TEXT,
                intro TEXT,
                total_chapters INTEGER DEFAULT 0,
                create_time TEXT,
                book_ad_spend REAL DEFAULT 0,
                promotion_link_count INTEGER DEFAULT 0,
                source TEXT,
                region TEXT,
                tags TEXT,
                recommend INTEGER DEFAULT 0,
                exclusive_status TEXT,
                create_by TEXT,
                word_count INTEGER DEFAULT 0,
                collect_num INTEGER DEFAULT 0,
                locale_code TEXT,
                raw_json TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS novel_chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                chapter_no INTEGER,
                chapter_name TEXT,
                content TEXT,
                word_count INTEGER DEFAULT 0,
                raw_json TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(novel_id, chapter_no)
            );

            CREATE TABLE IF NOT EXISTS meta_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                act_id TEXT UNIQUE NOT NULL,
                act_name TEXT,
                access_token TEXT,
                token_expires_at TIMESTAMP,
                pingykj_account TEXT,
                status TEXT DEFAULT 'active',
                rate_limit_remaining INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS delivery_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                source_adset_id TEXT,
                targeting_json TEXT,
                placements_json TEXT,
                budget_type TEXT DEFAULT 'daily_budget',
                budget_value INTEGER DEFAULT 0,
                bid_strategy TEXT DEFAULT 'LOWEST_COST_WITHOUT_CAP',
                optimization_goal TEXT DEFAULT 'OFFSITE_CONVERSIONS',
                billing_event TEXT DEFAULT 'IMPRESSIONS',
                conversion_event TEXT,
                ad_account_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS delivery_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                image_type TEXT,
                image_path TEXT,
                image_prompt TEXT,
                overlay_text TEXT,
                status TEXT DEFAULT 'pending',
                reviewer TEXT,
                template_id INTEGER,
                delivery_params_json TEXT,
                fb_campaign_id TEXT,
                fb_adset_id TEXT,
                fb_ad_id TEXT,
                fb_creative_id TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

# ====== Session CRUD ======

def get_session_cookies() -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cookies FROM login_session WHERE id = 1 AND expires_at > datetime('now')"
        ).fetchone()
        return row["cookies"] if row and row["cookies"] else None

def save_session_cookies(cookies_json: str, username: str = "", expires_at: str = None) -> None:
    if expires_at is None:
        expires_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO login_session (id, cookies, username, expires_at, updated_at)
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET cookies=excluded.cookies, username=excluded.username, expires_at=excluded.expires_at, updated_at=CURRENT_TIMESTAMP
        """, (cookies_json, username, expires_at))

# ====== Ad Stats CRUD ======

def upsert_ad_stats(rows: List[Dict[str, Any]]) -> int:
    """批量 UPSERT 广告日报数据，返回实际写入行数"""
    if not rows:
        return 0
    with get_conn() as conn:
        count = 0
        for r in rows:
            conn.execute("""
                INSERT INTO ad_daily_stats (date, ad_account, total_spend, total_revenue, ad_count, impressions, clicks, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ad_account, source) DO UPDATE SET
                    total_spend=excluded.total_spend,
                    total_revenue=excluded.total_revenue,
                    ad_count=excluded.ad_count,
                    impressions=excluded.impressions,
                    clicks=excluded.clicks,
                    extra_data=excluded.extra_data,
                    synced_at=CURRENT_TIMESTAMP
            """, (
                r.get("date"), r.get("ad_account"), r.get("total_spend", 0), r.get("total_revenue", 0),
                r.get("ad_count", 0), r.get("impressions", 0), r.get("clicks", 0),
                json.dumps(r.get("extra_data", {}), ensure_ascii=False) if r.get("extra_data") else None
            ))
            count += 1
        return count

# ====== Orders CRUD ======

def upsert_orders(rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    with get_conn() as conn:
        count = 0
        for r in rows:
            order_id = r.get("order_id")
            if not order_id:
                continue
            # customer_info 已是 JSON 字符串（来自 _parse_order_rows），无需再 json.dumps
            ci = r.get("customer_info")
            if ci and not isinstance(ci, str):
                ci = json.dumps(ci, ensure_ascii=False)
            # extra_data 是 dict，需要 json.dumps
            ed = r.get("extra_data")
            if ed and not isinstance(ed, str):
                ed = json.dumps(ed, ensure_ascii=False)

            conn.execute("""
                INSERT INTO orders (order_id, order_date, amount, status, customer_info, ad_account, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    order_date=excluded.order_date,
                    amount=excluded.amount,
                    status=excluded.status,
                    customer_info=excluded.customer_info,
                    ad_account=excluded.ad_account,
                    extra_data=excluded.extra_data,
                    synced_at=CURRENT_TIMESTAMP
            """, (
                order_id, r.get("order_date"), r.get("amount", 0), r.get("status"),
                ci, r.get("ad_account"), ed
            ))
            count += 1
        return count

# ====== Sync Log ======

# ====== Raw Data CRUD ======

def save_raw_ad_stats(records: List[Dict[str, Any]]) -> int:
    """保存广告 API 原始记录（全字段），按 API 记录 id 去重"""
    if not records:
        return 0
    with get_conn() as conn:
        count = 0
        for r in records:
            record_id = str(r.get("id") or "")
            stat_date = str(r.get("statDate") or "")
            ad_account_id = str(r.get("adAccountId") or "")
            if not record_id:
                continue
            raw_json_str = json.dumps(r, ensure_ascii=False)
            conn.execute("""
                INSERT INTO raw_ad_stats (record_id, stat_date, ad_account_id, raw_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    stat_date=excluded.stat_date,
                    ad_account_id=excluded.ad_account_id,
                    raw_json=excluded.raw_json,
                    synced_at=CURRENT_TIMESTAMP
            """, (record_id, stat_date, ad_account_id, raw_json_str))
            count += 1
        return count

def get_raw_ad_stats(start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    """读取原始广告数据，返回完整 JSON 字典列表"""
    with get_conn() as conn:
        where = ["1=1"]
        params = []
        if start_date:
            where.append("stat_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("stat_date <= ?")
            params.append(end_date)
        rows = conn.execute(
            f"SELECT raw_json FROM raw_ad_stats WHERE {' AND '.join(where)} ORDER BY stat_date DESC",
            params
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

def save_raw_orders(records: List[Dict[str, Any]]) -> int:
    """保存订单 API 原始记录（全字段），按 order_id 去重"""
    if not records:
        return 0
    with get_conn() as conn:
        count = 0
        for r in records:
            order_id = str(r.get("orderNo") or r.get("order_id") or "")
            if not order_id:
                continue
            raw_json_str = json.dumps(r, ensure_ascii=False)
            conn.execute("""
                INSERT INTO raw_orders (order_id, raw_json)
                VALUES (?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    raw_json=excluded.raw_json, synced_at=CURRENT_TIMESTAMP
            """, (order_id, raw_json_str))
            count += 1
        return count

def get_raw_orders(start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    """读取原始订单数据"""
    with get_conn() as conn:
        where = ["1=1"]
        params = []
        if start_date:
            where.append("json_extract(raw_json, '$.createTime') >= ?")
            params.append(start_date)
        if end_date:
            where.append("json_extract(raw_json, '$.createTime') <= ?")
            params.append(end_date)
        rows = conn.execute(
            f"SELECT raw_json FROM raw_orders WHERE {' AND '.join(where)} ORDER BY synced_at DESC",
            params
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]


# ====== Sync State ======

def get_last_sync_date(sync_type: str) -> Optional[str]:
    """获取上次同步日期，用于增量更新"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = ?", (sync_type,)
        ).fetchone()
        return row["last_sync_date"] if row else None

def set_last_sync_date(sync_type: str, date_str: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (sync_type, last_sync_date, last_sync_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sync_type) DO UPDATE SET
                last_sync_date=excluded.last_sync_date, last_sync_at=CURRENT_TIMESTAMP
        """, (sync_type, date_str))


# ====== Sync Interval ======

def get_sync_interval() -> int:
    """获取自动同步间隔（秒），默认 180（3分钟）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = 'interval'"
        ).fetchone()
        if row and row["last_sync_date"]:
            try:
                return int(row["last_sync_date"])
            except ValueError:
                pass
        return 180

def set_sync_interval(seconds: int) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (sync_type, last_sync_date, last_sync_at)
            VALUES ('interval', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sync_type) DO UPDATE SET
                last_sync_date=excluded.last_sync_date, last_sync_at=CURRENT_TIMESTAMP
        """, (str(seconds),))


# ====== Account Aliases ======

def get_account_aliases() -> Dict[str, str]:
    """返回 {account_id: alias} 映射"""
    with get_conn() as conn:
        rows = conn.execute("SELECT account_id, alias FROM account_aliases").fetchall()
        return {r["account_id"]: r["alias"] for r in rows}

def set_account_alias(account_id: str, alias: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO account_aliases (account_id, alias, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                alias=excluded.alias, updated_at=CURRENT_TIMESTAMP
        """, (account_id, alias))

def delete_account_alias(account_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM account_aliases WHERE account_id = ?", (account_id,))

def get_account_display_list() -> List[Dict[str, str]]:
    """返回账户列表，含别名"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ad_account_id
            FROM raw_ad_stats
            ORDER BY ad_account_id
        """).fetchall()

        aliases = get_account_aliases()
        result = []
        for r in rows:
            acct_id = r["ad_account_id"]
            alias = aliases.get(acct_id, "")
            display = alias if alias else acct_id
            result.append({
                "account_id": acct_id,
                "alias": alias,
                "display": display,
            })
        return result


def log_sync(sync_type: str, status: str, records_count: int = 0, error_message: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO sync_logs (sync_type, status, records_count, error_message, finished_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sync_type, status, records_count, error_message or None))
        log_id = cur.lastrowid
        return log_id


# ====== Novel Books CRUD ======

def upsert_novel_books(rows: List[Dict[str, Any]]) -> int:
    """批量 UPSERT 书籍信息，返回写入行数"""
    if not rows:
        return 0
    with get_conn() as conn:
        count = 0
        for r in rows:
            conn.execute("""
                INSERT INTO novel_books (novel_id, novel_name, author, cover_url, status, category, intro,
                    total_chapters, create_time, book_ad_spend, promotion_link_count, source, region, tags,
                    recommend, exclusive_status, create_by, word_count, collect_num, locale_code, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(novel_id) DO UPDATE SET
                    novel_name=excluded.novel_name,
                    author=excluded.author,
                    cover_url=excluded.cover_url,
                    status=excluded.status,
                    category=excluded.category,
                    intro=excluded.intro,
                    total_chapters=excluded.total_chapters,
                    create_time=excluded.create_time,
                    book_ad_spend=excluded.book_ad_spend,
                    promotion_link_count=excluded.promotion_link_count,
                    source=excluded.source,
                    region=excluded.region,
                    tags=excluded.tags,
                    recommend=excluded.recommend,
                    exclusive_status=excluded.exclusive_status,
                    create_by=excluded.create_by,
                    word_count=excluded.word_count,
                    collect_num=excluded.collect_num,
                    locale_code=excluded.locale_code,
                    raw_json=excluded.raw_json,
                    synced_at=CURRENT_TIMESTAMP
            """, (
                r.get("novel_id"), r.get("novel_name"), r.get("author"),
                r.get("cover_url"), r.get("status"), r.get("category"),
                r.get("intro"), r.get("total_chapters", 0),
                r.get("create_time"), r.get("book_ad_spend", 0),
                r.get("promotion_link_count", 0), r.get("source"),
                r.get("region"), r.get("tags"),
                r.get("recommend"), r.get("exclusive_status"),
                r.get("create_by"), r.get("word_count", 0),
                r.get("collect_num", 0), r.get("locale_code"),
                r.get("raw_json")
            ))
            count += 1
        return count


def get_novel_books(page: int = 1, page_size: int = 20, keyword: str = None,
                    status_filter: str = None) -> dict:
    """分页查询书籍列表"""
    with get_conn() as conn:
        where = []
        params = []
        if keyword:
            where.append("(novel_name LIKE ? OR author LIKE ? OR novel_id LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM novel_books{where_clause}", params
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM novel_books{where_clause} ORDER BY create_time DESC LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def get_novel_book(novel_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM novel_books WHERE novel_id = ?", (novel_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_novel_ids() -> List[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT novel_id FROM novel_books").fetchall()
        return [r["novel_id"] for r in rows]


# ====== Novel Chapters CRUD ======

def upsert_novel_chapters(rows: List[Dict[str, Any]]) -> int:
    """批量 UPSERT 章节，返回写入行数"""
    if not rows:
        return 0
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO novel_chapters (novel_id, chapter_no, chapter_name, content, word_count, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(novel_id, chapter_no) DO UPDATE SET
                chapter_name=excluded.chapter_name,
                content=excluded.content,
                word_count=excluded.word_count,
                raw_json=excluded.raw_json,
                synced_at=CURRENT_TIMESTAMP
        """, [
            (r.get("novel_id"), r.get("chapter_no"), r.get("chapter_name"),
             r.get("content"), r.get("word_count", 0), r.get("raw_json"))
            for r in rows
        ])
        return len(rows)


def get_novel_chapters(novel_id: str, page: int = 1, page_size: int = 50) -> dict:
    """分页查询某书的章节列表"""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM novel_chapters WHERE novel_id = ?", (novel_id,)
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            """SELECT id, novel_id, chapter_no, chapter_name, word_count, synced_at
               FROM novel_chapters WHERE novel_id = ?
               ORDER BY chapter_no ASC LIMIT ? OFFSET ?""",
            (novel_id, page_size, offset)
        ).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def get_novel_chapter(chapter_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM novel_chapters WHERE id = ?", (chapter_id,)
        ).fetchone()
        return dict(row) if row else None


def get_novel_chapter_count(novel_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM novel_chapters WHERE novel_id = ?", (novel_id,)
        ).fetchone()
        return row["cnt"] if row else 0
