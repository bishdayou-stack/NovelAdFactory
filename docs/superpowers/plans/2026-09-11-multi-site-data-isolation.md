# 多书城（站点 A / B / 合计）数据隔离 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让同一套系统同时对接两个书城后台（A: `hw.manage.pingykj.com`、B: `manage.relishnovel.com`），数据分开存储，界面可切换看 A / 看 B / 看合计（默认合计）。

**Architecture:** 单库 + `site` 维度。pingykj 来源的表增加 `site` 列（`TEXT NOT NULL DEFAULT 'a'`），唯一键加入 `site`；同步按站点进行（会话按 `(user_id, site)` 分开）；查询加 `site` 过滤，**`site=None` 即合计（不加过滤）**——表内既有的 `GROUP BY novel_id` + `SUM(book_ad_spend)` 天然完成"同本小说去重 + 消耗相加"，无需任何合并代码。

**Tech Stack:** Python 3 + FastAPI + SQLite（无 ORM，`database.py` contextmanager 连接）+ 原生 JS 单文件前端（`static/index.html`）。

**Spec:** `docs/superpowers/specs/2026-09-11-multi-site-data-isolation-design.md`

## Global Constraints

- `site` 取值：`'a'`（书城 A / pingykj）、`'b'`（书城 B / relishnovel）。**老数据一律为 `'a'`**。
- 站点定义放 `config.json` 的 `meta.pingykj_sites`（`update.sh` 会保留 `meta` 块，服务器改域名不会被 pull 覆盖）。
- **查询不加 `site` 过滤 = 合计**。合计不做任何跨站去重/相加的显式代码。
- **不新增用户凭据列**：两书城账号密码相同，复用 `users.pingykj_username` / `pingykj_password_encrypted`。
- Meta 来源的表与查询**不参与** `site` 维度，保持原样。
- 前端**合计模式只读**：写操作（同步、删除账户、改别名等）禁用并提示先切到具体站点。
- 本仓库**没有单元测试框架**，验证用可运行的 assert 脚本（放 `scripts/`），沿用既有约定。
- 迁移前必须**备份 `data/dashboard.db`**；迁移在单事务内完成。

---

### Task 1: 站点配置读取

**Files:**
- Modify: `scraper.py:13-29`（`BASE_PATH` / `BASE_URL` / `_get_proxy_url` 附近）
- Create: `scripts/verify_sites_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `scraper.DEFAULT_SITES: List[Dict[str, str]]`
  - `scraper.get_sites() -> List[Dict[str, str]]`（返回 `[{"key","name","base_url","content_url"}, ...]`）
  - `scraper.site_base_url(site: str) -> str`
  - `scraper.site_content_url(site: str) -> str`
  - `scraper.DEFAULT_SITE: str = "a"`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_sites_config.py`：

```python
# -*- coding: utf-8 -*-
"""验证站点配置读取：默认值、config.json 覆盖、未知站点回落。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))
import scraper

def main():
    sites = scraper.get_sites()
    keys = [s["key"] for s in sites]
    assert keys[:2] == ["a", "b"], f"默认应有 a/b 两个站点，实际 {keys}"
    assert scraper.site_base_url("a") == "https://hw.manage.pingykj.com", scraper.site_base_url("a")
    assert scraper.site_content_url("a") == "https://hw.manage.api.pingykj.com", scraper.site_content_url("a")
    assert scraper.site_base_url("b") == "https://manage.relishnovel.com", scraper.site_base_url("b")
    assert scraper.site_content_url("b") == "https://manage.api.relishnovel.com", scraper.site_content_url("b")
    # 未知站点回落到默认站点，不抛异常
    assert scraper.site_base_url("zzz") == scraper.site_base_url("a")
    assert scraper.site_content_url("") == scraper.site_content_url("a")
    print("OK: 站点配置读取正确")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_sites_config.py`
Expected: FAIL —— `AttributeError: module 'scraper' has no attribute 'get_sites'`

- [ ] **Step 3: 实现配置读取**

在 `scraper.py` 顶部（`BASE_URL = ...` 那一行替换掉）：

```python
# ====== 站点（书城）配置 ======
# 站点定义优先取 config.json 的 meta.pingykj_sites（update.sh 保留 meta 块，服务器改域名不被覆盖），
# 未配置时回落到下面默认值。
DEFAULT_SITE = "a"
DEFAULT_SITES = [
    {"key": "a", "name": "A站(pingykj)",
     "base_url": "https://hw.manage.pingykj.com",
     "content_url": "https://hw.manage.api.pingykj.com"},
    {"key": "b", "name": "B站(relishnovel)",
     "base_url": "https://manage.relishnovel.com",
     "content_url": "https://manage.api.relishnovel.com"},
]


def get_sites() -> List[Dict[str, str]]:
    """返回站点列表；config.json 的 meta.pingykj_sites 优先，缺项用默认值补齐。"""
    sites = []
    try:
        config = json.loads((BASE_PATH / "config.json").read_text(encoding="utf-8"))
        sites = config.get("meta", {}).get("pingykj_sites") or []
    except Exception:
        sites = []
    out = []
    for s in sites:
        if not isinstance(s, dict) or not s.get("key"):
            continue
        out.append({
            "key": str(s["key"]),
            "name": str(s.get("name") or s["key"]),
            "base_url": str(s.get("base_url") or "").rstrip("/"),
            "content_url": str(s.get("content_url") or "").rstrip("/"),
        })
    return out or [dict(d) for d in DEFAULT_SITES]


def _site_conf(site: str) -> Dict[str, str]:
    sites = get_sites()
    key = (site or DEFAULT_SITE).strip() or DEFAULT_SITE
    for s in sites:
        if s["key"] == key:
            return s
    return sites[0]   # 未知站点回落到第一个（不存在则不可能，get_sites 必返回非空）


def site_base_url(site: str) -> str:
    """站点后台 base（登录/广告/订单/书籍列表）。"""
    return _site_conf(site)["base_url"]


def site_content_url(site: str) -> str:
    """站点章节内容接口 base。"""
    return _site_conf(site)["content_url"]
```

同时把 `config.json` 的 `meta` 块加上（作为新装模板；服务器上按需改）：

```json
"pingykj_sites": [
  {"key": "a", "name": "A站(pingykj)", "base_url": "https://hw.manage.pingykj.com", "content_url": "https://hw.manage.api.pingykj.com"},
  {"key": "b", "name": "B站(relishnovel)", "base_url": "https://manage.relishnovel.com", "content_url": "https://manage.api.relishnovel.com"}
]
```

- [ ] **Step 4: 运行确认通过**

Run: `python scripts/verify_sites_config.py`
Expected: `OK: 站点配置读取正确`

- [ ] **Step 5: Commit**

```bash
git add scraper.py config.json scripts/verify_sites_config.py
git commit -m "feat(site): 站点(书城)配置读取 —— A/B 两个书城地址可配置，默认值内置"
```

> 注意：本步骤之后 `scraper.BASE_URL` / `scraper._CONTENT_API` 已不存在，Task 4 会改掉所有引用；
> 在此之前 `scraper.py` 内对 `BASE_URL` 的 5 处引用会 `NameError`，这是预期的中间态，Task 4 修复。
> **若要保持每步可运行，可把 `BASE_URL = site_base_url(DEFAULT_SITE)` 与 `_CONTENT_API = site_content_url(DEFAULT_SITE)` 作为过渡常量保留到 Task 4 结束再删。**

---

### Task 2: 数据层迁移（site 列 + 唯一键重建）

**Files:**
- Modify: `database.py:457-482`（`ad_daily_stats` DDL）、`:566-577`（`orders`）、`:579-588`（`sync_logs`）、`:590-606`（`raw_ad_stats`/`raw_orders`）、`:608-622`（`sync_state`/`account_aliases`）、`:624-669`（三张 `novel_*`）
- Modify: `database.py:367-430`（`init_db`，调用新迁移）
- Create: `scripts/verify_site_migration.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `database.SITE_DEFAULT = "a"`
  - `database._migrate_site_isolation(conn) -> None`（幂等）
  - 所有 pingykj 来源表含 `site TEXT NOT NULL DEFAULT 'a'` 列，唯一键含 `site`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_site_migration.py` —— 用**旧 schema + 样本数据**建临时库，跑迁移，断言行数不丢、`site` 值正确、跨站点同 key 能共存：

```python
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
OLD_SCHEMA = """
CREATE TABLE ad_daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date DATE NOT NULL, ad_account TEXT NOT NULL,
    total_spend REAL DEFAULT 0, total_revenue REAL DEFAULT 0, ad_count INTEGER DEFAULT 0,
    impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, extra_data TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT DEFAULT 'pingykj',
    meta_account_id TEXT, ctr REAL, cpm REAL, cpc REAL, inline_link_clicks INTEGER,
    inline_link_click_ctr REAL, add_to_cart INTEGER, add_to_cart_cost REAL,
    purchases INTEGER, cost_per_purchase REAL, purchase_value REAL, user_id INTEGER DEFAULT 1,
    UNIQUE(date, ad_account, source, user_id));
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
INSERT INTO ad_daily_stats (date, ad_account, total_spend, user_id) VALUES ('2026-09-01','acc1',10.5,1);
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
    n = conn.execute("SELECT COUNT(*) c FROM novel_books").fetchone()["c"]
    assert n == 2, f"重复迁移后行数异常 {n}"
    conn.close()

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OK: site 迁移正确（数据不丢、默认 a、跨站共存、幂等）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_site_migration.py`
Expected: FAIL —— `AssertionError: ad_daily_stats 缺 site 列`

- [ ] **Step 3: 基础 schema 加 site 列（新装库直接带上）**

在 `database.py` 的 `conn.executescript("""...""")`（`:456` 起）里，为下列表加上 `site TEXT NOT NULL DEFAULT 'a'` 并更新唯一键：

- `ad_daily_stats`（`:481`）：列尾加 `site TEXT NOT NULL DEFAULT 'a',`，唯一键改 `UNIQUE(date, ad_account, source, user_id, site)`
- `orders`（`:568`）：`order_id TEXT UNIQUE NOT NULL` 改为 `order_id TEXT NOT NULL`，列尾加 `site TEXT NOT NULL DEFAULT 'a'`，并加 `UNIQUE(site, order_id)`
- `sync_logs`（`:586` 后）：加 `site TEXT NOT NULL DEFAULT 'a'`
- `raw_ad_stats`（`:597`）：`UNIQUE(record_id)` → `UNIQUE(site, record_id)`，加 site 列
- `raw_orders`（`:602`）：同 orders 的处理，`UNIQUE(site, order_id)`
- `sync_state`（`:613`）：主键改 `PRIMARY KEY (sync_type, user_id, site)`，加 site 列
- `account_aliases`（`:621`）：主键改 `PRIMARY KEY (account_id, user_id, site)`，加 site 列
- `novel_books`（`:626`）：`novel_id TEXT UNIQUE NOT NULL` → `novel_id TEXT NOT NULL` + `UNIQUE(site, novel_id)`，加 site 列
- `novel_chapters`（`:659`）：`UNIQUE(novel_id, chapter_no)` → `UNIQUE(site, novel_id, chapter_no)`
- `novel_spend_snapshots`（`:668`）：`UNIQUE(novel_id, snap_date)` → `UNIQUE(site, novel_id, snap_date)`

- [ ] **Step 4: 实现迁移函数**

在 `database.py` 的 `_ensure_user_id_columns` 之后新增：

```python
SITE_DEFAULT = "a"

# 需要重建（唯一键含 site）的表：表名 -> 新 DDL。
# 新 DDL 必须包含 site 列，且唯一键/主键含 site。
_SITE_REBUILD_DDL = {
    "ad_daily_stats": """
        CREATE TABLE ad_daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date DATE NOT NULL, ad_account TEXT NOT NULL,
            total_spend REAL DEFAULT 0, total_revenue REAL DEFAULT 0, ad_count INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, extra_data TEXT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source TEXT DEFAULT 'pingykj',
            meta_account_id TEXT, ctr REAL, cpm REAL, cpc REAL, inline_link_clicks INTEGER,
            inline_link_click_ctr REAL, add_to_cart INTEGER, add_to_cart_cost REAL,
            purchases INTEGER, cost_per_purchase REAL, purchase_value REAL, user_id INTEGER DEFAULT 1,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(date, ad_account, source, user_id, site))""",
    "orders": """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, order_date DATE,
            amount REAL, status TEXT, customer_info TEXT, ad_account TEXT, extra_data TEXT,
            user_id INTEGER DEFAULT 1, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, order_id))""",
    "raw_ad_stats": """
        CREATE TABLE raw_ad_stats (
            record_id TEXT NOT NULL, stat_date TEXT NOT NULL, ad_account_id TEXT NOT NULL,
            raw_json TEXT NOT NULL, user_id INTEGER DEFAULT 1,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, record_id))""",
    "raw_orders": """
        CREATE TABLE raw_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, raw_json TEXT NOT NULL,
            user_id INTEGER DEFAULT 1, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, order_id))""",
    "sync_state": """
        CREATE TABLE sync_state (
            sync_type TEXT NOT NULL, user_id INTEGER NOT NULL DEFAULT 1, last_sync_date TEXT,
            last_sync_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', PRIMARY KEY (sync_type, user_id, site))""",
    "account_aliases": """
        CREATE TABLE account_aliases (
            account_id TEXT NOT NULL, user_id INTEGER NOT NULL DEFAULT 1, alias TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', PRIMARY KEY (account_id, user_id, site))""",
    "novel_books": """
        CREATE TABLE novel_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL, novel_name TEXT,
            author TEXT, cover_url TEXT, status TEXT, category TEXT, intro TEXT,
            total_chapters INTEGER DEFAULT 0, create_time TEXT, book_ad_spend REAL DEFAULT 0,
            promotion_link_count INTEGER DEFAULT 0, source TEXT, region TEXT, tags TEXT,
            recommend INTEGER DEFAULT 0, exclusive_status TEXT, create_by TEXT,
            word_count INTEGER DEFAULT 0, collect_num INTEGER DEFAULT 0, locale_code TEXT,
            raw_json TEXT, synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, novel_id))""",
    "novel_chapters": """
        CREATE TABLE novel_chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL, chapter_no INTEGER,
            chapter_name TEXT, content TEXT, word_count INTEGER DEFAULT 0, raw_json TEXT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, novel_id, chapter_no))""",
    "novel_spend_snapshots": """
        CREATE TABLE novel_spend_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id TEXT NOT NULL, snap_date DATE NOT NULL,
            book_ad_spend REAL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            site TEXT NOT NULL DEFAULT 'a', UNIQUE(site, novel_id, snap_date))""",
}

# 只需补列（无唯一键约束涉及 site）的表
_SITE_ADD_COLUMN = ["sync_logs"]


def _rebuild_table_with_site(conn, table: str, create_sql: str) -> None:
    """把表重建为带 site 列的版本。列顺序按 PRAGMA 实际列序取，避免 ALTER 过的表列序错位。"""
    tmp = f"{table}__pre_site"
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
    conn.execute(f"ALTER TABLE {table} RENAME TO {tmp}")
    conn.execute(create_sql)
    collist = ", ".join(cols)
    conn.execute(f"INSERT INTO {table} ({collist}, site) SELECT {collist}, ? FROM {tmp}", (SITE_DEFAULT,))
    conn.execute(f"DROP TABLE {tmp}")


def _migrate_site_isolation(conn) -> None:
    """一次性迁移：为 pingykj 来源的表加 site 列（老数据为 'a'），唯一键含 site。幂等。"""
    for table, ddl in _SITE_REBUILD_DDL.items():
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        except Exception:
            continue                                   # 表不存在，跳过
        if not cols or "site" in cols:
            continue                                   # 已是新结构
        print(f"[database] 重建 {table}：唯一键加入 site...")
        _rebuild_table_with_site(conn, table, ddl)

    for table in _SITE_ADD_COLUMN:
        try:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
            if cols and "site" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN site TEXT NOT NULL DEFAULT '{SITE_DEFAULT}'")
        except Exception:
            pass
```

- [ ] **Step 5: 在 init_db 中调用（在既有迁移之后）**

`database.py:420-423` 附近，改为：

```python
            _migrate_user_isolation(conn)
            _ensure_user_id_columns(conn)
            _migrate_site_isolation(conn)      # 新增：site 维度
```

- [ ] **Step 6: 运行确认通过**

Run: `python scripts/verify_site_migration.py`
Expected: `OK: site 迁移正确（数据不丢、默认 a、跨站共存、幂等）`

- [ ] **Step 7: 备份生产库并冒烟**

```bash
cp data/dashboard.db data/dashboard.db.bak-$(date +%Y%m%d)
python -c "import database; database.init_db(); print('migrated')"
```
Expected: 打印若干 `[database] 重建 xxx：唯一键加入 site...`，最后 `migrated`；再用 sqlite 核对各表行数与备份一致。

- [ ] **Step 8: Commit**

```bash
git add database.py scripts/verify_site_migration.py
git commit -m "feat(site): 数据层 site 维度迁移——老数据归 'a'，唯一键含 site，幂等可重跑"
```

---

### Task 3: 数据层读写函数加 site 参数

**Files:**
- Modify: `database.py:1217-1720`（读写函数）
- Create: `scripts/verify_site_io.py`

**Interfaces:**
- Consumes: `database.SITE_DEFAULT`（Task 2）
- Produces: 下列函数签名末尾统一增加 `site: str = None`；`site=None` 表示不过滤（合计）。

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_site_io.py`：

```python
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

    # sync_state 按站点独立
    database.set_last_sync_date("ads", "2026-09-01", user_id=1, site="a")
    database.set_last_sync_date("ads", "2026-09-02", user_id=1, site="b")
    assert database.get_last_sync_date("ads", user_id=1, site="a") == "2026-09-01"
    assert database.get_last_sync_date("ads", user_id=1, site="b") == "2026-09-02"

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 读写按 site 隔离，合计=去重+相加")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_site_io.py`
Expected: FAIL —— `TypeError: upsert_ad_stats() got an unexpected keyword argument 'site'`

- [ ] **Step 3: 改写入函数（14 个）**

统一模式（以 `upsert_ad_stats` 为例，`database.py:1217`）：

```python
def upsert_ad_stats(rows: List[Dict[str, Any]], user_id: int = None, site: str = None) -> int:
    site = site or SITE_DEFAULT
    ...
    for row in rows:
        conn.execute("""
            INSERT INTO ad_daily_stats (date, ad_account, total_spend, ..., user_id, site)
            VALUES (?, ?, ?, ..., ?, ?)
            ON CONFLICT(date, ad_account, source, user_id, site) DO UPDATE SET ...
        """, (..., site))
```

按同样模式改这些函数（**`ON CONFLICT` 目标必须同步加上 `site`**）：

| 函数 | 行号 | 冲突/主键目标 |
|---|---|---|
| `upsert_ad_stats` | 1217 | `(date, ad_account, source, user_id, site)` |
| `upsert_orders` | 1247 | `(site, order_id)` |
| `save_raw_ad_stats` | 1286 | `(site, record_id)` |
| `save_raw_orders` | 1332 | `(site, order_id)` |
| `set_last_sync_date` | 1385 | `(sync_type, user_id, site)` |
| `delete_sync_state` | 1396 | WHERE 加 `site = ?` |
| `set_account_alias` | 1459 | `(account_id, user_id, site)` |
| `delete_account_alias` | 1469 | WHERE 加 `site = ?` |
| `delete_account_all` | 1477 | WHERE 加 `site = ?` |
| `log_sync` | 1515 | 仅多写一列 |
| `upsert_novel_books` | 1529 | `(site, novel_id)` |
| `save_novel_spend_snapshots` | 1579 | `(site, novel_id, snap_date)` |
| `upsert_novel_chapters` | 1674 | `(site, novel_id, chapter_no)` |

> **`delete_*` 的语义**：`site=None` 时必须**只删默认站点**还是**全删**？——按"合并不做写操作"的约束，
> 这些函数一律 `site = site or SITE_DEFAULT`（默认只作用于 A 站），调用方总是显式传站点。

- [ ] **Step 4: 改读取函数（13 个）**

统一模式：签名加 `site: str = None`；`site` 非空时在 WHERE 里加 `AND site = ?`；**`site=None` 不加过滤**。

| 函数 | 行号 | 说明 |
|---|---|---|
| `get_raw_ad_stats` | 1312 | WHERE 加 site |
| `get_raw_orders` | 1353 | WHERE 加 site |
| `get_last_sync_date` | 1376 | `site = site or SITE_DEFAULT`（游标是写操作的一部分，必须落到具体站点）|
| `get_account_aliases` | 1448 | `site = site or SITE_DEFAULT` |
| `get_account_display_list` | 1485 | `site=None` 合并两站账户（按 `account_id` 去重）|
| `get_novel_spend_snapshot` | 1599 | WHERE 加 site（`site = site or SITE_DEFAULT`）|
| `get_novel_books` | 1610 | WHERE 加 site；**合计由 GROUP BY novel_id + SUM(book_ad_spend) 天然完成** |
| `get_novel_book` | 1658 | `site = site or SITE_DEFAULT` |
| `get_all_novel_ids` | 1666 | WHERE 加 site |
| `get_novel_chapters` | 1696 | WHERE 加 site |
| `get_novel_chapter` | 1712 | 按 id 取，不涉及 site |
| `get_novel_chapter_count` | 1720 | WHERE 加 site |

**`get_novel_books` 关键实现**（合计时去重 + 消耗相加）：

```python
def get_novel_books(page: int = 1, page_size: int = 20, keyword: str = None,
                    ..., site: str = None) -> dict:
    where, params = [], []
    if site:
        where.append("nb.site = ?"); params.append(site)
    ...
    # 合计（site=None）时按 novel_id 聚合：同一本书两站的 book_ad_spend 相加
    sql = f"""
        SELECT nb.novel_id, MAX(nb.novel_name) AS novel_name, ...,
               SUM(nb.book_ad_spend) AS book_ad_spend,
               SUM(nb.order_count)   AS order_count
        FROM novel_books nb ...
        GROUP BY nb.novel_id
    """
```
> 单站点时 `GROUP BY novel_id` 结果相同，可统一走聚合分支，无需 if/else。

- [ ] **Step 5: 运行确认通过**

Run: `python scripts/verify_site_io.py`
Expected: `OK: 读写按 site 隔离，合计=去重+相加`

- [ ] **Step 6: Commit**

```bash
git add database.py scripts/verify_site_io.py
git commit -m "feat(site): 库读写函数支持 site —— 单站点过滤，site=None 合计(去重+相加)"
```

---

### Task 4: scraper 会话与同步按站点

**Files:**
- Modify: `scraper.py:107-119`（`_user_sessions`/`ScraperSession`）、`:170`、`:224`、`:261`、`:330`、`:395-446`（会话管理）、`:506-660`（同步）、`:844-1000`（小说）
- Create: `scripts/verify_scraper_site.py`

**Interfaces:**
- Consumes: `scraper.site_base_url` / `site_content_url`（Task 1）、`database.*(site=...)`（Task 3）
- Produces:
  - `_user_sessions: Dict[Tuple[int, str], ScraperSession]`
  - `_get_or_create_session(user_id: int, site: str) -> Tuple[Optional[ScraperSession], str]`
  - `keepalive_all_sessions() -> Dict[Tuple[int, str], bool]`
  - `clear_user_session(user_id: int, site: str) -> None`
  - `fetch_captcha_for_user(user_id: int, site: str)`
  - `login_via_api_for_user(user_id, username, password, site)`
  - `sync_ads(user_id, site)`、`sync_orders(user_id, site)`、`sync_novel_books(user_id=None, full_sync=False, site=None)`、`sync_missing_chapters(user_id=None, site=None)`、`sync_novel_chapters(novel_id, site=None)`、`sync_all_novel_content(novel_id=None, concurrency=8, site=None)`、`run_full_sync(user_id=None, site=None)`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_scraper_site.py`（不联网，只校验会话键与 URL 拼接按站点分开）：

```python
# -*- coding: utf-8 -*-
"""验证 scraper 会话按 (user_id, site) 分开、URL 按站点取。"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))
import scraper

def main():
    scraper._user_sessions.clear()
    s_a = scraper.ScraperSession("u", "p", site="a")
    s_b = scraper.ScraperSession("u", "p", site="b")
    assert s_a.base_url == "https://hw.manage.pingykj.com", s_a.base_url
    assert s_b.base_url == "https://manage.relishnovel.com", s_b.base_url
    scraper._user_sessions[(1, "a")] = s_a
    scraper._user_sessions[(1, "b")] = s_b
    assert len(scraper._user_sessions) == 2, "同名用户的两站点会话必须分开"
    print("OK: 会话按 (user, site) 分开，URL 按站点取")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_scraper_site.py`
Expected: FAIL —— `TypeError: ScraperSession.__init__() got an unexpected keyword argument 'site'`

- [ ] **Step 3: 会话改为按 (user_id, site)**

`scraper.py:107`：

```python
_user_sessions: Dict[Any, "ScraperSession"] = {}      # 键: (user_id, site)
```

`ScraperSession.__init__`（`:113`）增加 `site: str = None` 参数，并把 `self.base_url = site_base_url(self.site)`、`self.content_url = site_content_url(self.site)`；**类内所有裸用 `BASE_URL` 的地方改用 `self.base_url`**。

`_get_or_create_session`（`:395`）：

```python
def _get_or_create_session(user_id: int, site: str = None) -> Tuple[Optional[ScraperSession], str]:
    site = site or DEFAULT_SITE
    key = (user_id, site)
    if key in _user_sessions:
        session = _user_sessions[key]
        ...
        del _user_sessions[key]
    ...
    _user_sessions[key] = session
```

`keepalive_all_sessions`（`:422`）、`clear_user_session`（`:440`）、`fetch_captcha_for_user`（`:1010`）、
`login_via_api_for_user`（`:1033`）同步改为按 `(user_id, site)`。

- [ ] **Step 4: 模块级 URL 常量改为按站点取**

- 删除 `BASE_URL = ...`（若 Task 1 保留了过渡常量，此处删除）
- `:170`、`:224`、`:261`、`:330`、`:844` 的 `f"{BASE_URL}..."` 改为 `f"{session.base_url}..."`（无 session 上下文处用 `site_base_url(site)`）
- `:656` `_CONTENT_API` 删除；`:959` 改为 `f"{site_content_url(site)}{_CONTENT_PATH}..."`

- [ ] **Step 5: 同步函数加 site 参数**

- `sync_ads(user_id, site=None)`：内部 `_get_or_create_session(user_id, site)`，写库全部带 `site=site`
- `sync_orders(user_id, site=None)`：同上
- `sync_novel_books(user_id=None, full_sync=False, site=None)`：同上；`upsert_novel_books(rows, site=site)`、`save_novel_spend_snapshots(..., site=site)`
- `sync_novel_chapters(novel_id, site=None)`、`sync_missing_chapters(user_id=None, site=None)`、`sync_all_novel_content(..., site=None)`
- `reset_sync_state(user_id, site=None)`
- `run_full_sync(user_id=None, site=None)`：把 `site` 透传给 ads/orders/novels 三个子步骤，`log_sync(..., site=site)`

- [ ] **Step 6: 运行确认通过**

Run: `python scripts/verify_scraper_site.py`
Expected: `OK: 会话按 (user, site) 分开，URL 按站点取`

- [ ] **Step 7: 全仓检查无残留裸常量**

Run: `grep -nE "BASE_URL|_CONTENT_API" scraper.py`
Expected: 无输出（全部已改为按站点取）

- [ ] **Step 8: Commit**

```bash
git add scraper.py scripts/verify_scraper_site.py
git commit -m "feat(site): scraper 会话与同步按站点——(user,site) 会话，URL 与落库均带 site"
```

---

### Task 5: analytics 查询加 site 过滤

**Files:**
- Modify: `analytics.py:24-31`（新增 `_add_site_filter`）、`:35`、`:121`、`:177`、`:209`、`:224`、`:307`、`:352`、`:622`（8 个 pingykj 函数）
- Create: `scripts/verify_analytics_site.py`

**Interfaces:**
- Consumes: `database.get_*(site=...)`（Task 3）
- Produces: `_add_site_filter(where, params, site, prefix="")`；下列函数签名末尾加 `site: str = None`（`None` = 合计）

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_analytics_site.py`：

```python
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

    a = analytics.get_summary(user_id=1, site="a")
    b = analytics.get_summary(user_id=1, site="b")
    all_ = analytics.get_summary(user_id=1, site=None)
    assert a["total_spend"] == 10.0, a
    assert b["total_spend"] == 5.0, b
    assert all_["total_spend"] == 15.0, f"合计应为 15，实际 {all_['total_spend']}"
    assert all_["total_revenue"] == 26.0, all_

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 看板按 site 过滤，合计=两站相加")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_analytics_site.py`
Expected: FAIL —— `TypeError: get_summary() got an unexpected keyword argument 'site'`

- [ ] **Step 3: 实现 `_add_site_filter`**

`analytics.py:31` 之后：

```python
def _add_site_filter(where: List[str], params: List, site: str, prefix: str = ""):
    """添加站点过滤；site 为空表示合计（不过滤）"""
    if site:
        col = f"{prefix}site" if prefix else "site"
        where.append(f"{col} = ?")
        params.append(site)
```

- [ ] **Step 4: 8 个 pingykj 函数加 site**

在每个函数签名末尾加 `site: str = None`，并在**已有的 `_add_user_filter(where, params, user_id)` 调用旁边**加
`_add_site_filter(where, params, site)`（带 prefix 的用同样 prefix，如 `meta_*` 不加）。

| 函数 | 行号 | 备注 |
|---|---|---|
| `get_summary` | 35 | 两处 where（`where` 与 `order_where`）**都要加** |
| `get_daily_stats` | 121 | |
| `get_trend` | 177 | |
| `get_accounts` | 209 | 透传给 `database.get_account_display_list(user_id, site=site)` |
| `get_account_ranking` | 224 | |
| `detect_anomalies` | 270 | |
| `get_orders` | 307 | |
| `get_novel_stats` | 352 | 透传 `site` 给 `database.get_novel_spend_snapshot(..., site=site)` 与 `get_novel_books` |
| `get_user_ranking` | 622 | 注意它的过滤是正向 `source='pingykj'`，加 site 时用同一别名 |

**合计时无需任何额外代码**：不加 site 条件，`GROUP BY` 自然把两站数据聚到一起。

- [ ] **Step 5: 运行确认通过**

Run: `python scripts/verify_analytics_site.py`
Expected: `OK: 看板按 site 过滤，合计=两站相加`

- [ ] **Step 6: Commit**

```bash
git add analytics.py scripts/verify_analytics_site.py
git commit -m "feat(site): 看板查询支持 site 过滤，site=None 合计（聚合天然完成）"
```

---

### Task 6: main.py 路由透传 site

**Files:**
- Modify: `main.py:4131-4160`（别名/账户）、`:4166-4210`（小说）、`:4403-4520`（看板）、`:3958-4120`（scraper 路由）
- Create: `scripts/verify_routes_site.py`

**Interfaces:**
- Consumes: Task 5 的 `analytics.*(site=...)`、Task 4 的 `scraper.*(site=...)`
- Produces: 下列路由新增 `site: str = Query(default=None)`，空串归一为 `None`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_routes_site.py`（用 FastAPI TestClient，无需真实书城）：

```python
# -*- coding: utf-8 -*-
"""验证看板路由接受 site 参数并透传（用 TestClient + 直接登录）。"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

def main():
    import main
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    for path in ["/api/dashboard/summary", "/api/dashboard/daily-stats",
                 "/api/dashboard/trend", "/api/dashboard/accounts",
                 "/api/dashboard/account-ranking", "/api/dashboard/orders",
                 "/api/dashboard/novel-stats", "/api/novels/list"]:
        for site in ["", "a", "b"]:
            rr = c.get(path, params={"site": site} if site else {}, headers=h)
            assert rr.status_code == 200, f"{path}?site={site} -> {rr.status_code} {rr.text[:200]}"
    print("OK: 看板与小说路由均接受 site 参数")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/verify_routes_site.py`
Expected: PASS 也可能通过（FastAPI 默认忽略未知 query 参数）—— 因此**本脚本只保证"加了 site 不报错"**；
真正的过滤验证由 Task 5 的脚本与 Task 9 的端到端承担。

- [ ] **Step 3: 看板与小说路由加 site**

统一模式（以 `/api/dashboard/summary` 为例）：

```python
@app.get("/api/dashboard/summary")
def api_dashboard_summary(
    start: str = Query(default=None),
    end: str = Query(default=None),
    account: str = Query(default=None),
    keyword: str = Query(default=None),
    site: str = Query(default=None),
    user: dict = Depends(get_current_user),
):
    uid = _opt_user_id(user)
    return analytics.get_summary(start_date=start, end_date=end, account=account,
                                 keyword=keyword, user_id=uid, site=(site or None))
```

按下表逐个加参数并透传：

| 路由 | 行号 | 目标函数 |
|---|---|---|
| `/api/dashboard/summary` | 4403 | `analytics.get_summary(..., site=)` |
| `/api/dashboard/daily-stats` | 4416 | `get_daily_stats(..., site=)` |
| `/api/dashboard/accounts` | 4433 | `get_accounts(user_id, site=)` |
| `/api/dashboard/trend` | 4440 | `get_trend(..., site=)` |
| `/api/dashboard/orders` | 4452 | `get_orders(..., site=)` |
| `/api/dashboard/account-ranking` | 4467 | `get_account_ranking(..., site=)` |
| `/api/dashboard/anomalies` | 4482 | `detect_anomalies(..., site=)` |
| `/api/dashboard/user-ranking` | 4490 | `get_user_ranking(..., site=)` |
| `/api/dashboard/novel-stats` | 4500 | `get_novel_stats(..., site=)` |
| `/api/novels/list` | 4166 | `database.get_novel_books(..., site=)` |
| `/api/novels/{id}/chapters` | 4192 | `database.get_novel_chapters(..., site=(site or None))` |
| `/api/dashboard/account-aliases` | 4131 | `get_account_aliases(user_id, site=(site or SITE_DEFAULT))` |

- [ ] **Step 4: scraper 路由加 site**

- `POST /api/scraper/sync`（`:3986`）：加 `site: str = Query(default=None)`；空 = **遍历所有站点**逐个 `run_full_sync(uid, site)`
- `POST /api/scraper/reset-sync`（`:4045`）：同上
- `GET /api/scraper/captcha`（`:3958`）、`POST /api/scraper/login`（`:3973`）、`GET /api/scraper/session-status`（`:4089`）、`POST /api/scraper/logout`（`:4098`）：加 `site`，空则用 `DEFAULT_SITE`
- `/api/novels/sync-books`（`:4216`）、`/api/novels/sync-books-full`（`:4225`）、`/api/novels/sync-content`（`:4315`）：加 `site` 透传

- [ ] **Step 5: 运行验证**

Run: `python scripts/verify_routes_site.py`
Expected: `OK: 看板与小说路由均接受 site 参数`

- [ ] **Step 6: Commit**

```bash
git add main.py scripts/verify_routes_site.py
git commit -m "feat(site): 看板/小说/scraper 路由接受 site 参数并透传"
```

---

### Task 7: 定时任务按站点遍历

**Files:**
- Modify: `main.py:236-340`（`_auto_sync_all_users` / `_auto_full_novel_sync` / `_auto_chapter_sync`）

**Interfaces:**
- Consumes: `scraper.get_sites()`（Task 1）、`scraper.run_full_sync(uid, site)`（Task 4）
- Produces: 定时任务对每个站点各跑一次

- [ ] **Step 1: 改 `_auto_sync_all_users`（main.py:236）**

```python
def _auto_sync_all_users():
    for u in database.list_active_users_with_credentials():
        uid = u["id"]
        interval = database.get_sync_interval(uid) or 180
        for s in scraper.get_sites():
            site = s["key"]
            last = database.get_last_sync_date("sync_all", user_id=uid, site=site)
            if last and (datetime.now() - _parse_ts(last)).total_seconds() < interval:
                continue
            try:
                scraper.run_full_sync(uid, site=site)
            except Exception as e:
                print(f"[AUTO SYNC] user={uid} site={site} 失败: {e}")
```

> 节流游标用 `sync_all` + site 维度（`set_last_sync_date("sync_all", ..., site=site)`），
> 与 `run_full_sync` 内部写的 `ads`/`orders`/`novels` 游标互不干扰。

- [ ] **Step 2: 改 `_auto_full_novel_sync`（main.py:293）与 `_auto_chapter_sync`（main.py:323）**

同样在 `for s in scraper.get_sites():` 里循环，把 `site=s["key"]` 传下去。

- [ ] **Step 3: 启动冒烟**

Run: `python -c "import main; print('scheduler ok')"`
Expected: `scheduler ok`（无 import/语法错误）

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(site): 定时同步按站点遍历——每个书城各自节流同步"
```

---

### Task 8: 前端站点下拉 + 请求带 site + 合计只读

**Files:**
- Modify: `static/index.html`（顶栏加下拉；看板/小说 fetch 带 `site`；写操作在合计模式禁用）

**Interfaces:**
- Consumes: Task 6 的路由 `site` 参数
- Produces: `window.currentSite`（`''` = 合计 / `'a'` / `'b'`）、`siteParam()`、`applySiteParam(url)`

- [ ] **Step 1: 加站点下拉与状态**

在顶栏（`nav-*` 一组按钮附近）插入：

```html
<div class="flex items-center gap-2 mr-3">
  <span class="text-[11px] text-slate-400">站点</span>
  <select id="siteSelect" class="rounded-lg border border-slate-200 px-2 py-1 text-[12px] bg-white">
    <option value="">合计</option>
    <option value="a">A站</option>
    <option value="b">B站</option>
  </select>
</div>
```

脚本部分（放在看板脚本之前）：

```javascript
window.currentSite = localStorage.getItem('current_site') || '';   // 默认合计

function siteParam() { return window.currentSite || ''; }

// 给看板/小说接口的 URL 拼上 site 参数
function applySiteParam(url) {
  var s = siteParam();
  if (!s) return url;
  if (!/^\/api\/(dashboard|novels)\//.test(url)) return url;
  return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'site=' + encodeURIComponent(s);
}

window.isAggregate = function () { return !window.currentSite; };

document.getElementById('siteSelect').addEventListener('change', function () {
  window.currentSite = this.value;
  localStorage.setItem('current_site', this.value);
  // 刷新当前 tab 的数据
  if (typeof refreshDashboard === 'function') refreshDashboard();
  if (typeof loadNovelBooks === 'function') loadNovelBooks();
  if (typeof loadNovelStats === 'function') loadNovelStats();
  applyAggregateReadOnly();
});

// 合计模式下禁用写操作
function applyAggregateReadOnly() {
  var ro = window.isAggregate();
  ['btnSync', 'btnResetSync', 'saveAliasBtn'].forEach(function (id) {   // 按实际按钮 id 调整
    var el = document.getElementById(id);
    if (el) { el.disabled = ro; el.title = ro ? '合计模式下只读，请先切换到具体站点' : ''; }
  });
}
```

- [ ] **Step 2: 在全局 fetch 拦截器中统一拼 site**

在 `static/index.html:1630` 的拦截器里，`return origFetch.call(...)` 之前加一行：

```javascript
        if (typeof applySiteParam === 'function' && (!options.method || options.method.toUpperCase() === 'GET')) {
          url = applySiteParam(url);
        }
```
> 只对 GET 拼接；同步等写操作由 `applyAggregateReadOnly()` 在合计模式下禁用。

- [ ] **Step 3: 初始化时套用只读状态**

页面初始化处（`loadApiConfig().then(...)` 附近）加：

```javascript
    document.getElementById('siteSelect').value = window.currentSite;
    applyAggregateReadOnly();
```

- [ ] **Step 4: 手工验证**

启动 `bash start.sh`（或 `uvicorn main:app --port 8000`），浏览器打开前端：
1. 站点下拉默认「合计」；切 A / B，看板与小说列表数据随之变化（B 未同步时为空属正常）
2. 合计的消耗 = A + B；小说列表里同 `novel_id` 只有一行，消耗为两站相加
3. 合计模式下同步/删除类按钮置灰并提示

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat(site): 前端站点下拉(合计/A/B)——看板与小说请求带 site，合计只读"
```

---

### Task 9: 端到端验证

**Files:**
- Create: `scripts/verify_site_e2e.md`（人工核对清单，非代码）

- [ ] **Step 1: 分别同步两个站点**

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scraper/sync?site=a" -H "Authorization: Bearer <token>"
curl -s -X POST "http://127.0.0.1:8000/api/scraper/sync?site=b" -H "Authorization: Bearer <token>"
```
Expected: 两次都返回成功；B 站需要先在配置里确认账号密码（与 A 相同）。

- [ ] **Step 2: 核对库内 site 分布**

```bash
python -c "
import database
with database.get_conn() as c:
    for t in ['ad_daily_stats','orders','novel_books','novel_chapters']:
        print(t, dict(c.execute(f'SELECT site, COUNT(*) FROM {t} GROUP BY site').fetchall()))
"
```
Expected: 每个表都能看到 `a` 与 `b` 两组（B 站同步成功时）。

- [ ] **Step 3: 核对合计 = A + B**

```bash
python -c "
import analytics
for site in ['a','b',None]:
    r = analytics.get_summary(site=site)
    print('site=',site, 'spend=', r['total_spend'], 'revenue=', r['total_revenue'])
"
```
Expected: `site=a` + `site=b` 的数值 = `site=None` 的数值。

- [ ] **Step 4: 核对小说去重与消耗相加**

```bash
python -c "
import database
a = {b['novel_id']: b['book_ad_spend'] for b in database.get_novel_books(page_size=9999, site='a')['data']}
b = {x['novel_id']: x['book_ad_spend'] for x in database.get_novel_books(page_size=9999, site='b')['data']}
allb = {x['novel_id']: x['book_ad_spend'] for x in database.get_novel_books(page_size=9999, site=None)['data']}
dup = set(a) & set(b)
print('重复小说数:', len(dup))
for nid in list(dup)[:5]:
    assert abs(allb[nid] - (a[nid] + b[nid])) < 0.01, (nid, a[nid], b[nid], allb[nid])
print('OK: 重复小说消耗 = A + B')
"
```
Expected: `OK: 重复小说消耗 = A + B`

- [ ] **Step 5: 汇报并提交清单**

把上述结果整理进 `scripts/verify_site_e2e.md` 并提交：

```bash
git add scripts/verify_site_e2e.md
git commit -m "test(site): 多书城端到端验证清单与实测结果"
```

---

## Self-Review

**Spec 覆盖检查**

| Spec 要求 | 对应任务 |
|---|---|
| 站点配置（`meta.pingykj_sites`，默认值，换域名不改代码） | Task 1 |
| 表加 `site` 列、唯一键重建、迁移备份与幂等 | Task 2 |
| 读写函数按 site 隔离、合计=不过滤 | Task 3 |
| 同步按站点（会话、URL、游标、凭据复用） | Task 4 |
| 看板 12 函数加 site 过滤，合计自动聚合 | Task 5 |
| 路由透传 site | Task 6 |
| 定时任务按站点遍历 | Task 7 |
| 前端「合计/A/B」下拉、合计只读 | Task 8 |
| 端到端验证（合计=相加、小说去重+相加） | Task 9 |
| Meta 不参与 site | 不涉及（Task 5 只改 pingykj 的 8 个函数，`meta_*` 不动） |
| 不新增凭据列 | Task 4 复用 `users.pingykj_*` |

**占位符扫描**：无 TBD/TODO；每个代码步骤均给出可执行代码或精确到 文件:行号 的改动清单。

**类型一致性**：`site` 参数在所有层均为 `str`；`site=None` 在 database/analytics 统一表示"不过滤"，
在"写"与"游标"类函数统一回落 `SITE_DEFAULT`（`'a'`）。Task 1/3/4/5 的函数名与签名前后一致。

**已知需在执行时确认的点**：
- Task 8 的按钮 id（`btnSync` / `btnResetSync` / `saveAliasBtn`）需按 `static/index.html` 实际 id 校正。
- Task 6 的 `verify_routes_site.py` 因 FastAPI 忽略未知 query 参数，不能证明过滤生效，真正验证在 Task 9。
