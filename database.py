import sqlite3
import json
import os
import hashlib
import secrets
import base64
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

# ====== 加密工具 ======

_FERNET_KEY: Optional[bytes] = None

def _get_fernet():
    """懒加载 Fernet 实例，密钥存储在 data/.fernet_key"""
    global _FERNET_KEY
    if _FERNET_KEY is not None:
        from cryptography.fernet import Fernet
        return Fernet(_FERNET_KEY)
    try:
        from cryptography.fernet import Fernet
        key_path = Path(__file__).parent / "data" / ".fernet_key"
        if key_path.exists():
            _FERNET_KEY = key_path.read_bytes()
        else:
            _FERNET_KEY = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(_FERNET_KEY)
        return Fernet(_FERNET_KEY)
    except ImportError:
        return None  # 回退到 base64 混淆

def encrypt_pingykj_password(plaintext: str) -> str:
    """加密书城密码（Fernet > base64 回退）"""
    if not plaintext:
        return ""
    fernet = _get_fernet()
    if fernet:
        return fernet.encrypt(plaintext.encode()).decode()
    # 回退：base64 混淆（非安全，但比明文好）
    return base64.b64encode(plaintext.encode()).decode()

def decrypt_pingykj_password(ciphertext: str) -> str:
    """解密书城密码"""
    if not ciphertext:
        return ""
    fernet = _get_fernet()
    if fernet:
        from cryptography.fernet import Fernet
        try:
            return fernet.decrypt(ciphertext.encode()).decode()
        except Exception:
            # 尝试 base64 回退解密
            try:
                return base64.b64decode(ciphertext.encode()).decode()
            except Exception:
                return ""
    try:
        return base64.b64decode(ciphertext.encode()).decode()
    except Exception:
        return ""

def hash_password(password: str) -> tuple:
    """SHA256 加盐哈希，返回 (hash_hex, salt_hex)"""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return h, salt

def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """验证密码"""
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return h == stored_hash

def generate_session_token() -> str:
    """生成 64 字符随机会话 token"""
    return secrets.token_hex(32)

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

def _ensure_user_id_columns(conn) -> None:
    """每次启动时幂等检查，为所有隔离表补加 user_id 列，并修复缺失 user_id 的唯一键"""
    isolation_tables = [
        "ad_daily_stats", "orders", "raw_ad_stats", "raw_orders",
        "sync_state", "sync_logs", "account_aliases", "meta_accounts",
        "delivery_templates", "delivery_queue", "hit_materials"
    ]
    for table in isolation_tables:
        try:
            existing_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
            if "user_id" not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass

    # 检查 ad_daily_stats 的唯一键是否包含 user_id
    try:
        col_names = [r["name"] for r in conn.execute("PRAGMA table_info('ad_daily_stats')").fetchall()]
        if "user_id" not in set(col_names):
            pass  # user_id 列不存在，跳过（由上面的 ALTER TABLE 处理）
        else:
            idx_list = conn.execute("PRAGMA index_list('ad_daily_stats')").fetchall()
            need_rebuild = True
            for idx_row in idx_list:
                idx_name = idx_row["name"]
                if "autoindex" in idx_name.lower() or idx_name.startswith("sqlite_autoindex"):
                    idx_info = conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
                    idx_cols = [col_names[p["cid"]] for p in idx_info]
                    if "user_id" in idx_cols:
                        need_rebuild = False
                    break
            if need_rebuild:
                print("[database] 重建 ad_daily_stats 唯一键，加入 user_id...")
                conn.execute("ALTER TABLE ad_daily_stats RENAME TO ad_daily_stats_rebuild")
                conn.execute("""
                    CREATE TABLE ad_daily_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date DATE NOT NULL, ad_account TEXT NOT NULL,
                        total_spend REAL DEFAULT 0, total_revenue REAL DEFAULT 0,
                        ad_count INTEGER DEFAULT 0, impressions INTEGER DEFAULT 0,
                        clicks INTEGER DEFAULT 0, extra_data TEXT,
                        synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        source TEXT DEFAULT 'pingykj', meta_account_id TEXT,
                        ctr REAL, cpm REAL, cpc REAL,
                        inline_link_clicks INTEGER, inline_link_click_ctr REAL,
                        add_to_cart INTEGER, add_to_cart_cost REAL,
                        purchases INTEGER, cost_per_purchase REAL,
                        purchase_value REAL, user_id INTEGER DEFAULT 1,
                        UNIQUE(date, ad_account, source, user_id)
                    )
                """)
                conn.execute("""
                    INSERT INTO ad_daily_stats SELECT * FROM ad_daily_stats_rebuild
                """)
                conn.execute("DROP TABLE ad_daily_stats_rebuild")
                print("[database] ad_daily_stats 唯一键重建完成")
    except Exception:
        pass


def _migrate_user_isolation(conn) -> None:
    """一次性迁移：添加用户隔离支持"""
    # 1. 创建 users 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            display_name TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            pingykj_username TEXT DEFAULT '',
            pingykj_password_encrypted TEXT DEFAULT '',
            session_token TEXT DEFAULT NULL,
            session_expires_at TIMESTAMP,
            last_login_at TIMESTAMP,
            last_login_ip TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 1.1 用户配置表（按用户隔离的 API 配置）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_config (
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT DEFAULT '',
            PRIMARY KEY (user_id, key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # 2. 为隔离表添加 user_id 列
    isolation_tables = [
        "ad_daily_stats", "orders", "raw_ad_stats", "raw_orders",
        "sync_state", "sync_logs", "account_aliases", "meta_accounts",
        "delivery_templates", "delivery_queue", "hit_materials"
    ]
    for table in isolation_tables:
        try:
            existing_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
            if "user_id" not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass

    # 3. 重建 ad_daily_stats：唯一键改为 (date, ad_account, source, user_id)
    existing_ad = {r["name"] for r in conn.execute("PRAGMA table_info('ad_daily_stats')").fetchall()}
    has_user_id = "user_id" in existing_ad
    try:
        existing_indexes = [r["name"] for r in conn.execute("PRAGMA index_list('ad_daily_stats')").fetchall()]
        auto_idx = [i for i in existing_indexes if i.startswith("sqlite_autoindex_ad_daily_stats")]
        if auto_idx:
            pragma_info = conn.execute(f"PRAGMA index_info('{auto_idx[0]}')").fetchall()
            col_names = [r["name"] for r in conn.execute("PRAGMA table_info('ad_daily_stats')").fetchall()]
            idx_cols = [col_names[p["cid"]] for p in pragma_info if p.get("cid", -1) >= 0]
            # 如果旧唯一键不包含 user_id，重建
            if "user_id" not in idx_cols and has_user_id:
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
                        user_id INTEGER DEFAULT 1,
                        UNIQUE(date, ad_account, source, user_id)
                    )
                """)
                conn.execute("""
                    INSERT INTO ad_daily_stats SELECT * FROM ad_daily_stats_old
                """)
                conn.execute("DROP TABLE ad_daily_stats_old")
    except Exception:
        pass

    # 4. 重建 sync_state：主键改为 (sync_type, user_id)
    try:
        sync_idx = [r["name"] for r in conn.execute("PRAGMA index_list('sync_state')").fetchall()]
        if sync_idx and has_user_id:
            conn.execute("ALTER TABLE sync_state RENAME TO sync_state_old")
            conn.execute("""
                CREATE TABLE sync_state (
                    sync_type TEXT NOT NULL,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    last_sync_date TEXT,
                    last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (sync_type, user_id)
                )
            """)
            conn.execute("INSERT INTO sync_state SELECT sync_type, 1, last_sync_date, last_sync_at FROM sync_state_old")
            conn.execute("DROP TABLE sync_state_old")
    except Exception:
        # 有可能表还不存在或已经建好
        try:
            conn.execute("ALTER TABLE sync_state_old RENAME TO sync_state")
        except Exception:
            pass

    # 5. 重建 account_aliases：主键改为 (account_id, user_id)
    try:
        alias_idx = [r["name"] for r in conn.execute("PRAGMA index_list('account_aliases')").fetchall()]
        if alias_idx and has_user_id:
            conn.execute("ALTER TABLE account_aliases RENAME TO account_aliases_old")
            conn.execute("""
                CREATE TABLE account_aliases (
                    account_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    alias TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account_id, user_id)
                )
            """)
            conn.execute("INSERT INTO account_aliases SELECT account_id, 1, alias, updated_at FROM account_aliases_old")
            conn.execute("DROP TABLE account_aliases_old")
    except Exception:
        try:
            conn.execute("ALTER TABLE account_aliases_old RENAME TO account_aliases")
        except Exception:
            pass

    # 6. 重建 meta_accounts：唯一键改为 (act_id, user_id)
    try:
        meta_idx = [r["name"] for r in conn.execute("PRAGMA index_list('meta_accounts')").fetchall()]
        if meta_idx and has_user_id:
            conn.execute("ALTER TABLE meta_accounts RENAME TO meta_accounts_old")
            conn.execute("""
                CREATE TABLE meta_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    act_id TEXT NOT NULL,
                    act_name TEXT,
                    access_token TEXT,
                    token_expires_at TIMESTAMP,
                    pingykj_account TEXT,
                    status TEXT DEFAULT 'active',
                    rate_limit_remaining INTEGER DEFAULT 0,
                    user_id INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(act_id, user_id)
                )
            """)
            conn.execute("""
                INSERT INTO meta_accounts SELECT id, act_id, act_name, access_token,
                token_expires_at, pingykj_account, status, rate_limit_remaining,
                1, created_at, updated_at FROM meta_accounts_old
            """)
            conn.execute("DROP TABLE meta_accounts_old")
    except Exception:
        try:
            conn.execute("ALTER TABLE meta_accounts_old RENAME TO meta_accounts")
        except Exception:
            pass

    # 7. 删除旧 login_session 表（已由 users.session_token 取代）
    conn.execute("DROP TABLE IF EXISTS login_session")

    # 8. 创建默认管理员
    existing_admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if not existing_admin:
        h, s = hash_password("admin123")
        conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, display_name) VALUES (?, ?, ?, 'admin', ?)",
            ("admin", h, s, "Administrator")
        )
        print("[init_db] 默认管理员已创建: admin / admin123，请尽快修改密码")


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

        # 迁移：用户隔离支持
        has_users_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone() is not None
        if not has_users_table:
            _migrate_user_isolation(conn)
        else:
            # 每次启动时幂等补加可能遗漏的 user_id 列
            _ensure_user_id_columns(conn)

        # 迁移：为 users 表补加最后登录时间/IP 列
        if has_users_table:
            cols_users = {r["name"] for r in conn.execute("PRAGMA table_info('users')").fetchall()}
            if "last_login_at" not in cols_users:
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP")
                except Exception:
                    pass
            if "last_login_ip" not in cols_users:
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN last_login_ip TEXT DEFAULT ''")
                except Exception:
                    pass
            if "pingykj_offline_at" not in cols_users:
                try:
                    conn.execute("ALTER TABLE users ADD COLUMN pingykj_offline_at TIMESTAMP")
                except Exception:
                    pass

        # 确保 user_config 表存在（幂等，每次启动都检查）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_config (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT DEFAULT '',
                PRIMARY KEY (user_id, key),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 确保基础表存在（幂等）
        conn.executescript("""
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
                user_id INTEGER DEFAULT 1,
                UNIQUE(date, ad_account, source, user_id)
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
                user_id INTEGER DEFAULT 1,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT,
                status TEXT,
                records_count INTEGER DEFAULT 0,
                error_message TEXT,
                user_id INTEGER DEFAULT 1,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS raw_ad_stats (
                record_id TEXT NOT NULL,
                stat_date TEXT NOT NULL,
                ad_account_id TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                user_id INTEGER DEFAULT 1,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(record_id)
            );

            CREATE TABLE IF NOT EXISTS raw_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                raw_json TEXT NOT NULL,
                user_id INTEGER DEFAULT 1,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                sync_type TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                last_sync_date TEXT,
                last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (sync_type, user_id)
            );

            CREATE TABLE IF NOT EXISTS account_aliases (
                account_id TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                alias TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, user_id)
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

            CREATE TABLE IF NOT EXISTS novel_spend_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                snap_date DATE NOT NULL,
                book_ad_spend REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(novel_id, snap_date)
            );

            CREATE TABLE IF NOT EXISTS meta_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                act_id TEXT NOT NULL,
                act_name TEXT,
                access_token TEXT,
                token_expires_at TIMESTAMP,
                pingykj_account TEXT,
                status TEXT DEFAULT 'active',
                rate_limit_remaining INTEGER DEFAULT 0,
                user_id INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(act_id, user_id)
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
                user_id INTEGER DEFAULT 1,
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
                user_id INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS hit_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                image_url TEXT,
                video_url TEXT,
                prompt TEXT,
                label TEXT,
                novel_id TEXT,
                novel_name TEXT,
                ad_account TEXT,
                campaign_name TEXT,
                spend REAL DEFAULT 0,
                revenue REAL DEFAULT 0,
                roi REAL DEFAULT 0,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                ctr REAL DEFAULT 0,
                score INTEGER DEFAULT 0,
                notes TEXT,
                tags TEXT,
                user_id INTEGER DEFAULT 1,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

# ====== 用户管理 CRUD ======

def create_user(username: str, password: str, role: str = "user",
                display_name: str = "", pingykj_username: str = "",
                pingykj_password: str = "") -> int:
    """创建用户，返回 user_id"""
    h, s = hash_password(password)
    enc_pw = encrypt_pingykj_password(pingykj_password) if pingykj_password else ""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO users (username, password_hash, salt, role, display_name,
                pingykj_username, pingykj_password_encrypted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (username, h, s, role, display_name, pingykj_username, enc_pw))
        return cur.lastrowid

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

def list_users() -> List[Dict[str, Any]]:
    """管理员查看所有用户（不含密码哈希和加密凭据）"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, username, role, display_name, is_active,
                   pingykj_username,
                   CASE WHEN pingykj_password_encrypted != '' THEN '******' ELSE '' END as pingykj_password_masked,
                   last_login_at, last_login_ip, pingykj_offline_at,
                   created_at, updated_at
            FROM users ORDER BY created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

def list_active_users_with_credentials() -> List[Dict[str, Any]]:
    """返回有书城凭据的活跃用户列表（用于定时同步）"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM users
            WHERE is_active = 1 AND pingykj_username != ''
            ORDER BY id
        """).fetchall()
        return [dict(r) for r in rows]

def update_user(user_id: int, **fields) -> bool:
    """更新用户字段（role, display_name, is_active, pingykj_username, pingykj_password）"""
    allowed = {"role", "display_name", "is_active", "pingykj_username", "pingykj_password"}
    updates = {}
    for k, v in fields.items():
        if k in allowed:
            updates[k] = v
    if not updates:
        return False

    # 如果更新密码
    if "pingykj_password" in updates:
        updates["pingykj_password_encrypted"] = encrypt_pingykj_password(updates.pop("pingykj_password"))

    with get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        conn.execute(
            f"UPDATE users SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values + [user_id]
        )
        return True

def update_user_password(user_id: int, new_password: str) -> bool:
    """修改用户密码"""
    h, s = hash_password(new_password)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (h, s, user_id)
        )
        return True

def delete_user(user_id: int) -> bool:
    """删除用户（不允许删除自己，由调用方检查）"""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return True

def get_user_pingykj_credentials(user_id: int) -> Optional[Dict[str, str]]:
    """获取用户的书城登录凭据（解密后）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pingykj_username, pingykj_password_encrypted FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row or not row["pingykj_username"]:
            return None
        return {
            "username": row["pingykj_username"],
            "password": decrypt_pingykj_password(row["pingykj_password_encrypted"]),
        }


# ====== 用户配置（按用户隔离的 API 配置） ======

def get_user_config(user_id: int) -> Dict[str, str]:
    """获取某个用户的所有配置项（key-value 字典）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM user_config WHERE user_id = ?",
            (user_id,)
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_user_config(user_id: int, key: str, value: str) -> None:
    """设置某个用户的一项配置（自动 UPSERT）"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_config (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, value)
        )


def set_user_config_batch(user_id: int, config: Dict[str, str]) -> None:
    """批量设置某个用户的配置（自动 UPSERT）"""
    with get_conn() as conn:
        for key, value in config.items():
            conn.execute(
                "INSERT INTO user_config (user_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                (user_id, key, value)
            )

# ====== 应用登录会话 ======

def set_session_token(user_id: int, token: str, expires_at: str, ip: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET session_token = ?, session_expires_at = ?, last_login_at = CURRENT_TIMESTAMP, last_login_ip = ? WHERE id = ?",
            (token, expires_at, ip, user_id)
        )

def set_pingykj_offline(user_id: int) -> None:
    """记录书城凭据掉线时间"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET pingykj_offline_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id,)
        )


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    """验证 session token，返回用户字典（不含敏感字段）"""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, username, role, display_name, is_active, pingykj_username,
                      session_token, session_expires_at
               FROM users
               WHERE session_token = ? AND session_expires_at > datetime('now') AND is_active = 1""",
            (token,)
        ).fetchone()
        return dict(row) if row else None

def clear_session_token(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET session_token = NULL, session_expires_at = NULL WHERE id = ?",
            (user_id,)
        )

# ====== Ad Stats CRUD ======

def upsert_ad_stats(rows: List[Dict[str, Any]], user_id: int = None) -> int:
    """批量 UPSERT 广告日报数据，返回实际写入行数"""
    if not rows:
        return 0
    uid = user_id or 1
    with get_conn() as conn:
        count = 0
        for r in rows:
            conn.execute("""
                INSERT INTO ad_daily_stats (date, ad_account, total_spend, total_revenue, ad_count, impressions, clicks, extra_data, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ad_account, source, user_id) DO UPDATE SET
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
                json.dumps(r.get("extra_data", {}), ensure_ascii=False) if r.get("extra_data") else None,
                uid
            ))
            count += 1
        return count

# ====== Orders CRUD ======

def upsert_orders(rows: List[Dict[str, Any]], user_id: int = None) -> int:
    if not rows:
        return 0
    uid = user_id or 1
    with get_conn() as conn:
        count = 0
        for r in rows:
            order_id = r.get("order_id")
            if not order_id:
                continue
            ci = r.get("customer_info")
            if ci and not isinstance(ci, str):
                ci = json.dumps(ci, ensure_ascii=False)
            ed = r.get("extra_data")
            if ed and not isinstance(ed, str):
                ed = json.dumps(ed, ensure_ascii=False)

            conn.execute("""
                INSERT INTO orders (order_id, order_date, amount, status, customer_info, ad_account, extra_data, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ci, r.get("ad_account"), ed, uid
            ))
            count += 1
        return count

# ====== Sync Log ======

# ====== Raw Data CRUD ======

def save_raw_ad_stats(records: List[Dict[str, Any]], user_id: int = None) -> int:
    """保存广告 API 原始记录（全字段），按 API 记录 id 去重"""
    if not records:
        return 0
    uid = user_id or 1
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
                INSERT INTO raw_ad_stats (record_id, stat_date, ad_account_id, raw_json, user_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    stat_date=excluded.stat_date,
                    ad_account_id=excluded.ad_account_id,
                    raw_json=excluded.raw_json,
                    synced_at=CURRENT_TIMESTAMP
            """, (record_id, stat_date, ad_account_id, raw_json_str, uid))
            count += 1
        return count

def get_raw_ad_stats(start_date: str = None, end_date: str = None, user_id: int = None) -> List[Dict[str, Any]]:
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
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        rows = conn.execute(
            f"SELECT raw_json FROM raw_ad_stats WHERE {' AND '.join(where)} ORDER BY stat_date DESC",
            params
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]

def save_raw_orders(records: List[Dict[str, Any]], user_id: int = None) -> int:
    """保存订单 API 原始记录（全字段），按 order_id 去重"""
    if not records:
        return 0
    uid = user_id or 1
    with get_conn() as conn:
        count = 0
        for r in records:
            order_id = str(r.get("orderNo") or r.get("order_id") or "")
            if not order_id:
                continue
            raw_json_str = json.dumps(r, ensure_ascii=False)
            conn.execute("""
                INSERT INTO raw_orders (order_id, raw_json, user_id)
                VALUES (?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    raw_json=excluded.raw_json, synced_at=CURRENT_TIMESTAMP
            """, (order_id, raw_json_str, uid))
            count += 1
        return count

def get_raw_orders(start_date: str = None, end_date: str = None, user_id: int = None) -> List[Dict[str, Any]]:
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
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        rows = conn.execute(
            f"SELECT raw_json FROM raw_orders WHERE {' AND '.join(where)} ORDER BY synced_at DESC",
            params
        ).fetchall()
        return [json.loads(r["raw_json"]) for r in rows]


# ====== Sync State ======

def get_last_sync_date(sync_type: str, user_id: int = None) -> Optional[str]:
    """获取上次同步日期，用于增量更新"""
    uid = user_id or 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = ? AND user_id = ?", (sync_type, uid)
        ).fetchone()
        return row["last_sync_date"] if row else None

def set_last_sync_date(sync_type: str, date_str: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (sync_type, user_id, last_sync_date, last_sync_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sync_type, user_id) DO UPDATE SET
                last_sync_date=excluded.last_sync_date, last_sync_at=CURRENT_TIMESTAMP
        """, (sync_type, uid, date_str))


def delete_sync_state(sync_type: str, user_id: int = None) -> None:
    """清除同步状态，用于全量重同步"""
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sync_state WHERE sync_type = ? AND user_id = ?",
            (sync_type, uid)
        )


# ====== Sync Interval ======

def get_sync_interval(user_id: int = None) -> int:
    """获取某用户的自动同步间隔（秒），默认 600（10分钟）。
    优先从 user_config 读取，fallback 到全局旧值。"""
    uid = user_id or 1
    # 优先从 user_config 读取
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM user_config WHERE user_id = ? AND key = 'sync_interval'",
            (uid,)
        ).fetchone()
        if row and row["value"]:
            try:
                return int(row["value"])
            except ValueError:
                pass
    # fallback 到旧的全局存储（sync_state 中的 'interval' 记录）
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = 'interval' AND user_id = 1"
        ).fetchone()
        if row and row["last_sync_date"]:
            try:
                return int(row["last_sync_date"])
            except ValueError:
                pass
    return 600

def set_sync_interval(seconds: int, user_id: int = None) -> None:
    """设置某用户的自动同步间隔（秒），写入 user_config"""
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_config (user_id, key, value) VALUES (?, 'sync_interval', ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (uid, str(seconds))
        )


# ====== Account Aliases ======

def get_account_aliases(user_id: int = None) -> Dict[str, str]:
    """返回 {account_id: alias} 映射"""
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT account_id, alias FROM account_aliases WHERE user_id = ?", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT account_id, alias FROM account_aliases").fetchall()
        return {r["account_id"]: r["alias"] for r in rows}

def set_account_alias(account_id: str, alias: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO account_aliases (account_id, user_id, alias, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id, user_id) DO UPDATE SET
                alias=excluded.alias, updated_at=CURRENT_TIMESTAMP
        """, (account_id, uid, alias))

def delete_account_alias(account_id: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM account_aliases WHERE account_id = ? AND user_id = ?", (account_id, uid)
        )


def delete_account_all(account_id: str, user_id: int = None) -> None:
    """删除账户的所有数据（别名、日报统计、原始数据）"""
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("DELETE FROM account_aliases WHERE account_id = ? AND user_id = ?", (account_id, uid))
        conn.execute("DELETE FROM ad_daily_stats WHERE ad_account = ? AND user_id = ?", (account_id, uid))
        conn.execute("DELETE FROM raw_ad_stats WHERE ad_account_id = ? AND user_id = ?", (account_id, uid))

def get_account_display_list(user_id: int = None) -> List[Dict[str, str]]:
    """返回账户列表（仅 pingykj 来源，含别名）"""
    with get_conn() as conn:
        aliases = get_account_aliases(user_id)
        result = []
        seen = set()

        if user_id is not None:
            raw_rows = conn.execute(
                "SELECT DISTINCT ad_account_id FROM raw_ad_stats WHERE user_id = ? ORDER BY ad_account_id",
                (user_id,)
            ).fetchall()
        else:
            raw_rows = conn.execute(
                "SELECT DISTINCT ad_account_id FROM raw_ad_stats ORDER BY ad_account_id"
            ).fetchall()
        for r in raw_rows:
            acct_id = r["ad_account_id"]
            if acct_id and acct_id not in seen:
                seen.add(acct_id)
                alias = aliases.get(acct_id, "")
                result.append({
                    "account_id": acct_id,
                    "alias": alias,
                    "display": alias if alias else acct_id,
                })

        return result


def log_sync(sync_type: str, status: str, records_count: int = 0,
             error_message: str = "", user_id: int = None) -> int:
    uid = user_id or 1
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO sync_logs (sync_type, status, records_count, error_message, user_id, finished_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sync_type, status, records_count, error_message or None, uid))
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


def save_novel_spend_snapshots(books: List[Dict[str, Any]]) -> int:
    """保存小说当日消耗快照（用于计算区间消耗增量）"""
    if not books:
        return 0
    today = time.strftime("%Y-%m-%d")
    count = 0
    with get_conn() as conn:
        for b in books:
            nid = b.get("novel_id")
            spend = b.get("book_ad_spend", 0) or 0
            if not nid:
                continue
            conn.execute("""
                INSERT OR REPLACE INTO novel_spend_snapshots (novel_id, snap_date, book_ad_spend)
                VALUES (?, ?, ?)
            """, (nid, today, spend))
            count += 1
    return count


def get_novel_spend_snapshot(novel_id: str, target_date: str) -> Optional[float]:
    """获取小说在指定日期或之前最近的消耗快照值"""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT book_ad_spend FROM novel_spend_snapshots
            WHERE novel_id = ? AND snap_date <= ?
            ORDER BY snap_date DESC LIMIT 1
        """, (novel_id, target_date)).fetchone()
        return row["book_ad_spend"] if row else None


def get_novel_books(page: int = 1, page_size: int = 20, keyword: str = None,
                    status_filter: str = None, sort_by: str = "create_time",
                    sort_order: str = "DESC") -> dict:
    """分页查询书籍列表，附加订单数和转化成本。支持排序"""
    with get_conn() as conn:
        where = []
        params = []
        if keyword:
            where.append("(nb.novel_name LIKE ? OR nb.author LIKE ? OR nb.novel_id LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
        if status_filter:
            where.append("nb.status = ?")
            params.append(status_filter)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM novel_books nb{where_clause}", params
        ).fetchone()["cnt"]

        # 排序字段映射（白名单防注入）
        sort_map = {
            "create_time": "nb.create_time", "book_ad_spend": "nb.book_ad_spend",
            "order_count": "order_count", "conversion_cost": "conversion_cost",
            "promotion_link_count": "nb.promotion_link_count", "word_count": "nb.word_count",
            "total_chapters": "nb.total_chapters", "novel_name": "nb.novel_name",
        }
        sort_col = sort_map.get(sort_by, "nb.create_time")
        sort_dir = "DESC" if sort_order.upper() == "DESC" else "ASC"

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""SELECT nb.*,
                COALESCE(oo.order_count, 0) AS order_count,
                CASE WHEN COALESCE(oo.order_count, 0) > 0
                     THEN ROUND(nb.book_ad_spend / CAST(oo.order_count AS REAL), 2)
                     ELSE NULL END AS conversion_cost
                FROM novel_books nb
                LEFT JOIN (
                    SELECT json_extract(customer_info, '$.novelId') AS nid,
                           COUNT(*) AS order_count
                    FROM orders WHERE status = '成功'
                    GROUP BY json_extract(customer_info, '$.novelId')
                ) oo ON nb.novel_id = oo.nid
                {where_clause} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?""",
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


# ====== Meta Accounts CRUD ======

def get_meta_accounts(user_id: int = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM meta_accounts WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM meta_accounts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

def get_meta_account(act_id: str, user_id: int = None) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT * FROM meta_accounts WHERE act_id = ? AND user_id = ?", (act_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM meta_accounts WHERE act_id = ?", (act_id,)
            ).fetchone()
        return dict(row) if row else None

def upsert_meta_account(act_id: str, act_name: str = "", access_token: str = "",
                        pingykj_account: str = "", status: str = "active",
                        user_id: int = None) -> None:
    uid = user_id or 1
    token_expires_at = (datetime.utcnow() + timedelta(days=60)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO meta_accounts (act_id, act_name, access_token, token_expires_at,
                pingykj_account, status, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(act_id, user_id) DO UPDATE SET
                act_name=excluded.act_name,
                access_token=excluded.access_token,
                token_expires_at=excluded.token_expires_at,
                pingykj_account=excluded.pingykj_account,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
        """, (act_id, act_name, access_token, token_expires_at, pingykj_account, status, uid))

def delete_meta_account(act_id: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM meta_accounts WHERE act_id = ? AND user_id = ?", (act_id, uid)
        )

def update_meta_account_status(act_id: str, status: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute(
            "UPDATE meta_accounts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE act_id = ? AND user_id = ?",
            (status, act_id, uid)
        )

def update_meta_token(act_id: str, access_token: str, user_id: int = None) -> None:
    uid = user_id or 1
    token_expires_at = (datetime.utcnow() + timedelta(days=60)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE meta_accounts SET access_token = ?, token_expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE act_id = ? AND user_id = ?",
            (access_token, token_expires_at, act_id, uid)
        )


# ====== Delivery Templates CRUD ======

def get_delivery_templates(user_id: int = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM delivery_templates WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM delivery_templates ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

def get_delivery_template(template_id: int, user_id: int = None) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT * FROM delivery_templates WHERE id = ? AND user_id = ?", (template_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM delivery_templates WHERE id = ?", (template_id,)
            ).fetchone()
        return dict(row) if row else None

def create_delivery_template(data: Dict[str, Any], user_id: int = None) -> int:
    uid = user_id or 1
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO delivery_templates (name, source, source_adset_id, targeting_json,
                placements_json, budget_type, budget_value, bid_strategy,
                optimization_goal, billing_event, conversion_event, ad_account_id, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("name"), data.get("source", "manual"),
            data.get("source_adset_id"), json.dumps(data.get("targeting", {}), ensure_ascii=False),
            json.dumps(data.get("placements", {}), ensure_ascii=False),
            data.get("budget_type", "daily_budget"), data.get("budget_value", 0),
            data.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
            data.get("optimization_goal", "OFFSITE_CONVERSIONS"),
            data.get("billing_event", "IMPRESSIONS"), data.get("conversion_event"),
            data.get("ad_account_id"), uid
        ))
        return cur.lastrowid

def update_delivery_template(template_id: int, data: Dict[str, Any],
                              user_id: int = None) -> None:
    with get_conn() as conn:
        if user_id is not None:
            conn.execute("""
                UPDATE delivery_templates SET
                    name=?, targeting_json=?, placements_json=?, budget_type=?,
                    budget_value=?, bid_strategy=?, optimization_goal=?, billing_event=?,
                    conversion_event=?, ad_account_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND user_id=?
            """, (
                data.get("name"), json.dumps(data.get("targeting", {}), ensure_ascii=False),
                json.dumps(data.get("placements", {}), ensure_ascii=False),
                data.get("budget_type", "daily_budget"), data.get("budget_value", 0),
                data.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
                data.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                data.get("billing_event", "IMPRESSIONS"), data.get("conversion_event"),
                data.get("ad_account_id"), template_id, user_id
            ))
        else:
            conn.execute("""
                UPDATE delivery_templates SET
                    name=?, targeting_json=?, placements_json=?, budget_type=?,
                    budget_value=?, bid_strategy=?, optimization_goal=?, billing_event=?,
                    conversion_event=?, ad_account_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (
                data.get("name"), json.dumps(data.get("targeting", {}), ensure_ascii=False),
                json.dumps(data.get("placements", {}), ensure_ascii=False),
                data.get("budget_type", "daily_budget"), data.get("budget_value", 0),
                data.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
                data.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                data.get("billing_event", "IMPRESSIONS"), data.get("conversion_event"),
                data.get("ad_account_id"), template_id
            ))

def delete_delivery_template(template_id: int, user_id: int = None) -> None:
    with get_conn() as conn:
        if user_id is not None:
            conn.execute("DELETE FROM delivery_templates WHERE id = ? AND user_id = ?", (template_id, user_id))
        else:
            conn.execute("DELETE FROM delivery_templates WHERE id = ?", (template_id,))


# ====== Delivery Queue CRUD ======

def add_to_delivery_queue(items: List[Dict[str, Any]], user_id: int = None) -> int:
    """批量添加素材到投放队列，返回添加条数"""
    if not items:
        return 0
    uid = user_id or 1
    with get_conn() as conn:
        count = 0
        for item in items:
            conn.execute("""
                INSERT INTO delivery_queue (batch_id, image_type, image_path,
                    image_prompt, overlay_text, status, user_id)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (
                item.get("batch_id"), item.get("image_type"), item.get("image_path"),
                item.get("image_prompt"), item.get("overlay_text"), uid
            ))
            count += 1
        return count

def get_delivery_queue(page: int = 1, page_size: int = 20,
                       status_filter: str = None, user_id: int = None) -> dict:
    with get_conn() as conn:
        where = []
        params = []
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM delivery_queue{where_clause}", params
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM delivery_queue{where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

def update_queue_status(queue_id: int, status: str, reviewer: str = "",
                        error_message: str = "") -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE delivery_queue SET status=?, reviewer=?, error_message=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (status, reviewer, error_message, queue_id))

def batch_approve_queue(ids: List[int], template_id: int, reviewer: str = "") -> None:
    with get_conn() as conn:
        for qid in ids:
            conn.execute("""
                UPDATE delivery_queue SET status='approved', template_id=?,
                reviewer=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
            """, (template_id, reviewer, qid))

def batch_reject_queue(ids: List[int], reviewer: str = "") -> None:
    with get_conn() as conn:
        for qid in ids:
            conn.execute("""
                UPDATE delivery_queue SET status='rejected', reviewer=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?
            """, (reviewer, qid))

def update_queue_delivery_result(queue_id: int, status: str,
                                  fb_campaign_id: str = None, fb_adset_id: str = None,
                                  fb_ad_id: str = None, fb_creative_id: str = None,
                                  delivery_params_json: str = None,
                                  error_message: str = None) -> None:
    with get_conn() as conn:
        conn.execute("""
            UPDATE delivery_queue SET status=?, fb_campaign_id=?, fb_adset_id=?,
            fb_ad_id=?, fb_creative_id=?, delivery_params_json=?,
            error_message=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (status, fb_campaign_id, fb_adset_id, fb_ad_id, fb_creative_id,
              delivery_params_json, error_message, queue_id))

def get_delivery_records(page: int = 1, page_size: int = 20,
                          status_filter: str = None, user_id: int = None) -> dict:
    """获取投放记录（已投放到 FB 的项）"""
    with get_conn() as conn:
        where = ["fb_ad_id IS NOT NULL"]
        params = []
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        where_clause = " WHERE " + " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM delivery_queue{where_clause}", params
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM delivery_queue{where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ====== Meta Insights 数据写入 ======

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def _extract_action_value(actions: list, action_type: str) -> float:
    """从 Meta actions 数组中提取指定 action_type 的 value"""
    if not actions:
        return 0.0
    for a in actions:
        if a.get("action_type") == action_type:
            return _safe_float(a.get("value", 0))
    return 0.0

def _extract_cost_per_action(cost_per_action: list, action_type: str) -> float:
    if not cost_per_action:
        return 0.0
    for a in cost_per_action:
        if a.get("action_type") == action_type:
            return _safe_float(a.get("value", 0))
    return 0.0

def upsert_meta_insights(act_id: str, insights_rows: List[Dict[str, Any]],
                         user_id: int = None) -> int:
    """批量写入 Meta Insights 数据到 ad_daily_stats，返回写入行数"""
    if not insights_rows:
        return 0
    uid = user_id or 1
    with get_conn() as conn:
        count = 0
        for r in insights_rows:
            date = r.get("date_start", "")
            if not date:
                continue
            purchases = _extract_action_value(r.get("actions"), "purchase")
            purchase_value = _extract_action_value(r.get("action_values"), "purchase")
            add_to_cart = _extract_action_value(r.get("actions"), "add_to_cart")

            conn.execute("""
                INSERT INTO ad_daily_stats (date, ad_account, source, meta_account_id,
                    total_spend, total_revenue, impressions, clicks,
                    ctr, cpm, cpc,
                    inline_link_clicks, inline_link_click_ctr,
                    add_to_cart, add_to_cart_cost,
                    purchases, cost_per_purchase, purchase_value, user_id)
                VALUES (?, ?, 'meta', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ad_account, source, user_id) DO UPDATE SET
                    total_spend=excluded.total_spend,
                    total_revenue=excluded.total_revenue,
                    impressions=excluded.impressions,
                    clicks=excluded.clicks,
                    ctr=excluded.ctr,
                    cpm=excluded.cpm,
                    cpc=excluded.cpc,
                    inline_link_clicks=excluded.inline_link_clicks,
                    inline_link_click_ctr=excluded.inline_link_click_ctr,
                    add_to_cart=excluded.add_to_cart,
                    add_to_cart_cost=excluded.add_to_cart_cost,
                    purchases=excluded.purchases,
                    cost_per_purchase=excluded.cost_per_purchase,
                    purchase_value=excluded.purchase_value,
                    synced_at=CURRENT_TIMESTAMP
            """, (
                date, act_id, act_id,
                _safe_float(r.get("spend")),
                purchase_value,
                _safe_int(r.get("impressions")),
                _safe_int(r.get("clicks")),
                _safe_float(r.get("ctr")),
                _safe_float(r.get("cpm")),
                _safe_float(r.get("cost_per_inline_link_click")),
                _safe_int(r.get("inline_link_clicks")),
                _safe_float(r.get("inline_link_click_ctr")),
                add_to_cart,
                _extract_cost_per_action(r.get("cost_per_action_type"), "add_to_cart"),
                purchases,
                _extract_cost_per_action(r.get("cost_per_action_type"), "purchase"),
                purchase_value,
                uid,
            ))
            count += 1
        return count

def get_meta_sync_state(act_id: str, user_id: int = None) -> Optional[str]:
    """获取 Meta 账户上次同步日期"""
    uid = user_id or 1
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = ? AND user_id = ?",
            (f"meta_{act_id}", uid)
        ).fetchone()
        return row["last_sync_date"] if row else None

def set_meta_sync_state(act_id: str, date_str: str, user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (sync_type, user_id, last_sync_date, last_sync_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sync_type, user_id) DO UPDATE SET
                last_sync_date=excluded.last_sync_date, last_sync_at=CURRENT_TIMESTAMP
        """, (f"meta_{act_id}", uid, date_str))


# ====== 爆款素材登记 CRUD ======

def add_hit_material(data: Dict[str, Any], user_id: int = None) -> int:
    """登记一个爆款素材，返回新记录 ID"""
    uid = user_id or 1
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO hit_materials (batch_id, image_url, video_url, prompt, label,
                novel_id, novel_name, ad_account, campaign_name, spend, order_count, revenue, roi,
                impressions, clicks, ctr, score, notes, tags, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("batch_id"), data.get("image_url"), data.get("video_url"),
            data.get("prompt"), data.get("label"), data.get("novel_id"),
            data.get("novel_name"), data.get("ad_account"), data.get("campaign_name"),
            data.get("spend", 0), data.get("order_count", 0), data.get("revenue", 0), data.get("roi", 0),
            data.get("impressions", 0), data.get("clicks", 0), data.get("ctr", 0),
            data.get("score", 0), data.get("notes"), data.get("tags"), uid
        ))
        return cur.lastrowid


def get_hit_material_by_url(image_url: str, user_id: int = None) -> Optional[Dict[str, Any]]:
    """根据 image_url 查找最近一次登记的数据"""
    if not image_url:
        return None
    with get_conn() as conn:
        if user_id is not None:
            row = conn.execute(
                "SELECT * FROM hit_materials WHERE image_url = ? AND user_id = ? ORDER BY registered_at DESC LIMIT 1",
                (image_url, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM hit_materials WHERE image_url = ? ORDER BY registered_at DESC LIMIT 1",
                (image_url,)
            ).fetchone()
        return dict(row) if row else None


def get_hit_materials(page: int = 1, page_size: int = 20, keyword: str = None,
                      sort_by: str = "registered_at", sort_order: str = "DESC",
                      user_id: int = None) -> dict:
    """分页查询爆款素材列表"""
    with get_conn() as conn:
        where = []
        params = []
        if keyword:
            where.append("(h.novel_name LIKE ? OR h.label LIKE ? OR h.notes LIKE ? OR h.tags LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw])
        if user_id is not None:
            where.append("h.user_id = ?")
            params.append(user_id)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        valid_sorts = {"registered_at", "spend", "revenue", "roi", "score", "ctr"}
        sort_col = "h." + (sort_by if sort_by in valid_sorts else "registered_at")
        sort_dir = "DESC" if sort_order.upper() == "DESC" else "ASC"
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM hit_materials h{where_clause}", params
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""SELECT h.*, CASE WHEN u.display_name IS NOT NULL AND u.display_name != ''
                THEN u.display_name ELSE COALESCE(u.username, '') END AS user_name
                FROM hit_materials h
                LEFT JOIN users u ON h.user_id = u.id
                {where_clause} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?""",
            params + [page_size, offset]
        ).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def update_hit_material(mid: int, data: Dict[str, Any], user_id: int = None) -> bool:
    """更新爆款素材"""
    with get_conn() as conn:
        if user_id is not None:
            conn.execute("""
                UPDATE hit_materials SET spend=?, order_count=?, revenue=?, roi=?, impressions=?, clicks=?,
                    ctr=?, score=?, notes=?, tags=?, ad_account=?, campaign_name=?
                WHERE id=? AND user_id=?
            """, (
                data.get("spend", 0), data.get("order_count", 0), data.get("revenue", 0), data.get("roi", 0),
                data.get("impressions", 0), data.get("clicks", 0), data.get("ctr", 0),
                data.get("score", 0), data.get("notes"), data.get("tags"),
                data.get("ad_account"), data.get("campaign_name"), mid, user_id,
            ))
        else:
            conn.execute("""
                UPDATE hit_materials SET spend=?, order_count=?, revenue=?, roi=?, impressions=?, clicks=?,
                    ctr=?, score=?, notes=?, tags=?, ad_account=?, campaign_name=?
                WHERE id=?
            """, (
                data.get("spend", 0), data.get("order_count", 0), data.get("revenue", 0), data.get("roi", 0),
                data.get("impressions", 0), data.get("clicks", 0), data.get("ctr", 0),
                data.get("score", 0), data.get("notes"), data.get("tags"),
                data.get("ad_account"), data.get("campaign_name"), mid,
            ))
        return True


def delete_hit_material(mid: int, user_id: int = None) -> bool:
    """删除爆款素材"""
    with get_conn() as conn:
        if user_id is not None:
            conn.execute("DELETE FROM hit_materials WHERE id = ? AND user_id = ?", (mid, user_id))
        else:
            conn.execute("DELETE FROM hit_materials WHERE id = ?", (mid,))
        return True
