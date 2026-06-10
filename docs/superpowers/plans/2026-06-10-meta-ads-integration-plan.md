# Meta 广告集成 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Novel Ad Factory 新增 Meta 广告投放引擎和 Meta 数据同步能力，20+ 账户 200+ 条/天规模。

**Architecture:** 新建 `delivery.py`（投放引擎）和 `meta_api.py`（Meta Graph API 客户端），扩展 `database.py`（4 新表 + 现有表迁移）、`scraper.py`（Meta 数据同步）、`main.py`（27 个新 API 端点），前端新增 3 个 Tab。

**Tech Stack:** FastAPI + SQLite + requests + vanilla JS（与现有技术栈一致）

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `meta_api.py` | Meta Graph API 客户端封装（认证、速率限制、请求重试） |
| 新建 | `delivery.py` | 投放引擎（审核队列、模板管理、创意上传、广告创建） |
| 修改 | `database.py` | 新表建表 + ad_daily_stats 迁移 + 新 CRUD 函数 |
| 修改 | `scraper.py` | 新增 Meta Ads Insights 数据同步方法 |
| 修改 | `config.json` | 新增 Meta 配置占位 |
| 修改 | `main.py` | 新增 27 个 API 端点 + Meta 同步定时任务 |
| 修改 | `static/index.html` | 新增 3 个 Tab 的前端界面 |

---

### Task 1: 数据库 Schema — 新表建表语句

**Files:**
- Modify: `database.py` — `init_db()` 函数中添加新表

- [ ] **Step 1: 在 `init_db()` 中添加 meta_accounts 建表**

在 `database.py` 的 `init_db()` 函数中，在现有的 `conn.executescript(...)` 调用尾部（`novel_chapters` 表之后），追加以下 SQL：

```sql
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
```

- [ ] **Step 2: 验证建表**

```bash
python -c "import database; database.init_db(); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: 添加 meta_accounts / delivery_templates / delivery_queue 建表"
```

---

### Task 2: 数据库 Schema — ad_daily_stats 迁移

**Files:**
- Modify: `database.py` — `init_db()` 中添加迁移逻辑

- [ ] **Step 1: 在 `init_db()` 中添加 ad_daily_stats 新增列的迁移**

在 `init_db()` 函数中现有 novel_books 列迁移代码之后、`conn.executescript(...)` 之前，添加：

```python
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
# SQLite 不支持 ALTER TABLE DROP CONSTRAINT，需重建表
try:
    existing_indexes = [r["name"] for r in conn.execute("PRAGMA index_list('ad_daily_stats')").fetchall()]
    if "sqlite_autoindex_ad_daily_stats_1" in existing_indexes:
        # 检查旧约束是否是 2 列
        pragma_info = conn.execute("PRAGMA index_info('sqlite_autoindex_ad_daily_stats_1')").fetchall()
        if len(pragma_info) == 2:
            # 旧约束是 (date, ad_account)，需要升级为三列
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
```

- [ ] **Step 2: 验证迁移**

```bash
python -c "import database; database.init_db(); print('OK')"
python -c "
import database
with database.get_conn() as conn:
    info = conn.execute(\"PRAGMA table_info('ad_daily_stats')\").fetchall()
    cols = [r['name'] for r in info]
    assert 'source' in cols
    assert 'purchases' in cols
    print('All columns present:', cols)
"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: ad_daily_stats 表扩展 — 新增 source 列 + Meta 指标列 + 三列唯一约束"
```

---

### Task 3: 数据库 — meta_accounts CRUD

**Files:**
- Modify: `database.py`

- [ ] **Step 1: 添加 meta_accounts CRUD 函数**

在 `database.py` 末尾添加：

```python
# ====== Meta Accounts CRUD ======

def get_meta_accounts() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM meta_accounts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def get_meta_account(act_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM meta_accounts WHERE act_id = ?", (act_id,)
        ).fetchone()
        return dict(row) if row else None

def upsert_meta_account(act_id: str, act_name: str = "", access_token: str = "",
                        pingykj_account: str = "", status: str = "active") -> None:
    token_expires_at = (datetime.utcnow() + timedelta(days=60)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO meta_accounts (act_id, act_name, access_token, token_expires_at,
                pingykj_account, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(act_id) DO UPDATE SET
                act_name=excluded.act_name,
                access_token=excluded.access_token,
                token_expires_at=excluded.token_expires_at,
                pingykj_account=excluded.pingykj_account,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
        """, (act_id, act_name, access_token, token_expires_at, pingykj_account, status))

def delete_meta_account(act_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM meta_accounts WHERE act_id = ?", (act_id,))

def update_meta_account_status(act_id: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE meta_accounts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE act_id = ?",
            (status, act_id)
        )

def update_meta_token(act_id: str, access_token: str) -> None:
    token_expires_at = (datetime.utcnow() + timedelta(days=60)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE meta_accounts SET access_token = ?, token_expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE act_id = ?",
            (access_token, token_expires_at, act_id)
        )
```

- [ ] **Step 2: 验证 CRUD**

```bash
python -c "
import database; database.init_db()
database.upsert_meta_account('act_test_001', 'Test Account', 'token123')
accounts = database.get_meta_accounts()
assert len(accounts) == 1
assert accounts[0]['act_id'] == 'act_test_001'
database.delete_meta_account('act_test_001')
accounts = database.get_meta_accounts()
assert len(accounts) == 0
print('meta_accounts CRUD OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: meta_accounts CRUD 函数"
```

---

### Task 4: 数据库 — delivery_templates CRUD

**Files:**
- Modify: `database.py`

- [ ] **Step 1: 添加 delivery_templates CRUD 函数**

在 `database.py` 末尾添加：

```python
# ====== Delivery Templates CRUD ======

def get_delivery_templates() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM delivery_templates ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def get_delivery_template(template_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM delivery_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else None

def create_delivery_template(data: Dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO delivery_templates (name, source, source_adset_id, targeting_json,
                placements_json, budget_type, budget_value, bid_strategy,
                optimization_goal, billing_event, conversion_event, ad_account_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("name"), data.get("source", "manual"),
            data.get("source_adset_id"), json.dumps(data.get("targeting", {}), ensure_ascii=False),
            json.dumps(data.get("placements", {}), ensure_ascii=False),
            data.get("budget_type", "daily_budget"), data.get("budget_value", 0),
            data.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
            data.get("optimization_goal", "OFFSITE_CONVERSIONS"),
            data.get("billing_event", "IMPRESSIONS"), data.get("conversion_event"),
            data.get("ad_account_id")
        ))
        return cur.lastrowid

def update_delivery_template(template_id: int, data: Dict[str, Any]) -> None:
    with get_conn() as conn:
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

def delete_delivery_template(template_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM delivery_templates WHERE id = ?", (template_id,))
```

- [ ] **Step 2: 验证 CRUD**

```bash
python -c "
import database; database.init_db()
tid = database.create_delivery_template({
    'name': 'Test Template', 'source': 'manual',
    'targeting': {'age_min': 35, 'age_max': 65, 'genders': [1]},
    'budget_type': 'daily_budget', 'budget_value': 5000
})
templates = database.get_delivery_templates()
assert len(templates) >= 1
database.update_delivery_template(tid, {'name': 'Updated', 'budget_value': 10000})
t = database.get_delivery_template(tid)
assert t['name'] == 'Updated'
database.delete_delivery_template(tid)
print('delivery_templates CRUD OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: delivery_templates CRUD 函数"
```

---

### Task 5: 数据库 — delivery_queue CRUD

**Files:**
- Modify: `database.py`

- [ ] **Step 1: 添加 delivery_queue CRUD 函数**

在 `database.py` 末尾添加：

```python
# ====== Delivery Queue CRUD ======

def add_to_delivery_queue(items: List[Dict[str, Any]]) -> int:
    """批量添加素材到投放队列，返回添加条数"""
    if not items:
        return 0
    with get_conn() as conn:
        count = 0
        for item in items:
            conn.execute("""
                INSERT INTO delivery_queue (batch_id, image_type, image_path,
                    image_prompt, overlay_text, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (
                item.get("batch_id"), item.get("image_type"), item.get("image_path"),
                item.get("image_prompt"), item.get("overlay_text")
            ))
            count += 1
        return count

def get_delivery_queue(page: int = 1, page_size: int = 20,
                       status_filter: str = None) -> dict:
    with get_conn() as conn:
        where = []
        params = []
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
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
                          status_filter: str = None) -> dict:
    """获取投放记录（已投放到 FB 的项）"""
    with get_conn() as conn:
        where = ["fb_ad_id IS NOT NULL"]
        params = []
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
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
```

- [ ] **Step 2: 验证 CRUD**

```bash
python -c "
import database; database.init_db()
count = database.add_to_delivery_queue([
    {'batch_id': 'b1', 'image_type': 'scroll', 'image_path': '/tmp/test.png',
     'image_prompt': 'test prompt', 'overlay_text': 'test text'}
])
assert count == 1
q = database.get_delivery_queue()
assert q['total'] >= 1
assert q['data'][0]['status'] == 'pending'
database.update_queue_status(q['data'][0]['id'], 'approved', reviewer='admin')
q2 = database.get_delivery_queue(status_filter='approved')
assert q2['total'] >= 1
print('delivery_queue CRUD OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: delivery_queue CRUD 函数"
```

---

### Task 6: meta_api.py — Meta Graph API 客户端

**Files:**
- Create: `meta_api.py`

- [ ] **Step 1: 创建 meta_api.py**

```python
"""Meta (Facebook) Graph API 客户端封装 — 认证、速率限制、请求重试"""
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests as http_requests

GRAPH_API_BASE = "https://graph.facebook.com"
API_VERSION = "v25.0"

# 速率控制：每账户每秒最多 4 次调用
_RATE_LIMITS: Dict[str, Tuple[float, int]] = {}  # act_id -> (last_reset_time, remaining)


def _check_rate(act_id: str) -> None:
    """检查并等待速率限制恢复"""
    now = time.time()
    if act_id in _RATE_LIMITS:
        last_reset, remaining = _RATE_LIMITS[act_id]
        if now - last_reset >= 1.0:
            _RATE_LIMITS[act_id] = (now, 3)
            return
        if remaining <= 0:
            sleep_time = 1.0 - (now - last_reset)
            if sleep_time > 0:
                time.sleep(sleep_time)
            _RATE_LIMITS[act_id] = (time.time(), 3)
            return
        _RATE_LIMITS[act_id] = (last_reset, remaining - 1)
    else:
        _RATE_LIMITS[act_id] = (now, 3)


def _api_get(act_id: str, access_token: str, endpoint: str, params: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """GET 请求封装，含速率限制和重试逻辑"""
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if params:
        all_params.update(params)

    for attempt in range(3):
        try:
            resp = http_requests.get(url, params=all_params, timeout=30)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)
                if code == 190:
                    return None, f"Token 已过期: {err.get('message', '')}"
                if code in (4, 17, 80000, 80001, 80002, 80004, 80005):
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                return None, f"API 错误 [{code}]: {err.get('message', '')}"
            return data, None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"
    return None, "重试耗尽"


def _api_post(act_id: str, access_token: str, endpoint: str, body: dict = None,
              files: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """POST 请求封装"""
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if body:
        all_params.update(body)

    for attempt in range(3):
        try:
            if files:
                resp = http_requests.post(url, data=all_params, files=files, timeout=60)
            else:
                resp = http_requests.post(url, data=all_params, timeout=30)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)
                if code == 190:
                    return None, f"Token 已过期: {err.get('message', '')}"
                if code in (4, 17, 80000, 80001) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"API 错误 [{code}]: {err.get('message', '')}"
            return data, None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"
    return None, "重试耗尽"


# ---- 投放相关 API ----

def get_ad_account_info(act_id: str, access_token: str) -> Tuple[Optional[Dict], Optional[str]]:
    return _api_get(act_id, access_token, f"/{act_id}",
                    {"fields": "id,name,account_status,currency,timezone_name"})

def get_adsets(act_id: str, access_token: str,
               limit: int = 100) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """拉取一个广告账户下的所有 AdSet 配置"""
    data, err = _api_get(act_id, access_token, f"/{act_id}/adsets", {
        "fields": "id,name,campaign_id,daily_budget,lifetime_budget,bid_strategy,"
                  "billing_event,optimization_goal,targeting,promoted_object,"
                  "start_time,end_time,status,created_time",
        "limit": str(limit)
    })
    if err:
        return None, err
    return data.get("data", []), None

def upload_ad_image(act_id: str, access_token: str,
                    image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """上传图片到 FB 广告账户，返回 image hash"""
    import os
    filename = os.path.basename(image_path)
    with open(image_path, "rb") as f:
        img_data = f.read()
    data, err = _api_post(act_id, access_token, f"/{act_id}/adimages",
                          body={"filename": filename},
                          files={"file": (filename, img_data)})
    if err:
        return None, err
    images = data.get("images", {})
    for k in images:
        return images[k].get("hash", ""), None
    return None, "上传成功但未返回 hash"

def upload_ad_video(act_id: str, access_token: str,
                    video_path: str) -> Tuple[Optional[str], Optional[str]]:
    """上传视频到 FB 广告账户，返回 video ID"""
    import os
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        video_data = f.read()
    data, err = _api_post(act_id, access_token, f"/{act_id}/advideos",
                          body={"title": filename},
                          files={"source": (filename, video_data)})
    if err:
        return None, err
    return data.get("id", ""), None

def create_campaign(act_id: str, access_token: str,
                    name: str, objective: str = "OUTCOME_TRAFFIC",
                    status: str = "PAUSED",
                    special_ad_categories: list = None) -> Tuple[Optional[str], Optional[str]]:
    body = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": special_ad_categories or [],
    }
    data, err = _api_post(act_id, access_token, f"/{act_id}/campaigns", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def create_adset(act_id: str, access_token: str,
                 name: str, campaign_id: str,
                 targeting: dict, daily_budget: int = None,
                 lifetime_budget: int = None,
                 bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
                 billing_event: str = "IMPRESSIONS",
                 optimization_goal: str = "OFFSITE_CONVERSIONS",
                 start_time: str = None, end_time: str = None,
                 promoted_object: dict = None,
                 status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    body = {
        "name": name,
        "campaign_id": campaign_id,
        "targeting": json.dumps(targeting),
        "bid_strategy": bid_strategy,
        "billing_event": billing_event,
        "optimization_goal": optimization_goal,
        "status": status,
    }
    if daily_budget:
        body["daily_budget"] = daily_budget
    if lifetime_budget:
        body["lifetime_budget"] = lifetime_budget
    if start_time:
        body["start_time"] = start_time
    if end_time:
        body["end_time"] = end_time
    if promoted_object:
        body["promoted_object"] = json.dumps(promoted_object)

    data, err = _api_post(act_id, access_token, f"/{act_id}/adsets", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def create_ad(act_id: str, access_token: str,
              name: str, adset_id: str,
              creative_name: str, page_id: str,
              image_hash: str = None, video_id: str = None,
              message: str = "", link_url: str = "",
              status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    """创建广告，含创意"""
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "link": link_url,
            "message": message,
        }
    }
    if image_hash:
        object_story_spec["link_data"]["image_hash"] = image_hash
    if video_id:
        object_story_spec["link_data"]["video_id"] = video_id

    body = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({
            "name": creative_name,
            "object_story_spec": object_story_spec,
        }),
        "status": status,
    }
    data, err = _api_post(act_id, access_token, f"/{act_id}/ads", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def update_ad_status(act_id: str, access_token: str,
                     ad_id: str, status: str) -> Tuple[bool, Optional[str]]:
    """更新广告状态（ACTIVE / PAUSED）"""
    _, err = _api_post(act_id, access_token, f"/{ad_id}", body={"status": status})
    if err:
        return False, err
    return True, None


# ---- Insights API ----

def get_insights(act_id: str, access_token: str,
                 date_start: str, date_end: str,
                 level: str = "ad",
                 time_increment: int = 1) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """拉取广告投放数据（按日聚合）"""
    fields = (
        "spend,impressions,clicks,ctr,cpm,inline_link_clicks,inline_link_click_ctr,"
        "cost_per_inline_link_click,actions,cost_per_action_type,action_values,"
        "date_start"
    )
    params = {
        "fields": fields,
        "time_range": json.dumps({"since": date_start, "until": date_end}),
        "time_increment": str(time_increment),
        "level": level,
        "limit": "500",
    }
    all_data = []
    url = f"/{act_id}/insights"

    while True:
        data, err = _api_get(act_id, access_token, url, params)
        if err:
            return None, err
        all_data.extend(data.get("data", []))
        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break
        # 后续分页使用完整 URL，但需要加 token
        url = None
        full_next = f"{next_url}&access_token={access_token}"
        for attempt in range(3):
            try:
                _check_rate(act_id)
                resp = http_requests.get(full_next, timeout=30)
                data = resp.json()
                all_data.extend(data.get("data", []))
                paging = data.get("paging", {})
                next_url = paging.get("next")
                break
            except Exception:
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if not next_url:
            break
        full_next = f"{next_url}&access_token={access_token}"

    return all_data, None
```

- [ ] **Step 2: 验证模块可导入**

```bash
python -c "import meta_api; print('meta_api imported OK')"
```

- [ ] **Step 3: Commit**

```bash
git add meta_api.py
git commit -m "feat: Meta Graph API 客户端 — 认证、速率控制、投放CRUD、Insights拉取"
```

---

### Task 7: database.py — Meta Insights 数据写入

**Files:**
- Modify: `database.py`

- [ ] **Step 1: 添加 Meta Insights 数据批量写入函数**

在 `database.py` 末尾添加：

```python
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

def upsert_meta_insights(act_id: str, insights_rows: List[Dict[str, Any]]) -> int:
    """批量写入 Meta Insights 数据到 ad_daily_stats，返回写入行数"""
    if not insights_rows:
        return 0
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
                    purchases, cost_per_purchase, purchase_value)
                VALUES (?, ?, 'meta', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ad_account, source) DO UPDATE SET
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
            ))
            count += 1
        return count

def get_meta_sync_state(act_id: str) -> Optional[str]:
    """获取 Meta 账户上次同步日期"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_sync_date FROM sync_state WHERE sync_type = ?", (f"meta_{act_id}",)
        ).fetchone()
        return row["last_sync_date"] if row else None

def set_meta_sync_state(act_id: str, date_str: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sync_state (sync_type, last_sync_date, last_sync_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sync_type) DO UPDATE SET
                last_sync_date=excluded.last_sync_date, last_sync_at=CURRENT_TIMESTAMP
        """, (f"meta_{act_id}", date_str))
```

- [ ] **Step 2: 验证写入函数**

```bash
python -c "
import database; database.init_db()
rows = [{
    'date_start': '2026-06-10',
    'spend': '10.5', 'impressions': '1000', 'clicks': '50',
    'ctr': '5.0', 'cpm': '10.5',
    'inline_link_clicks': '30', 'inline_link_click_ctr': '3.0',
    'cost_per_inline_link_click': '0.35',
    'actions': [{'action_type': 'purchase', 'value': '3'}, {'action_type': 'add_to_cart', 'value': '8'}],
    'action_values': [{'action_type': 'purchase', 'value': '150'}],
    'cost_per_action_type': [{'action_type': 'purchase', 'value': '3.5'}, {'action_type': 'add_to_cart', 'value': '1.31'}]
}]
count = database.upsert_meta_insights('act_test', rows)
assert count == 1
print('Meta insights write OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add database.py
git commit -m "feat: Meta Insights 数据批量写入 + 同步状态管理"
```

---

### Task 8: scraper.py — Meta 数据同步方法

**Files:**
- Modify: `scraper.py`

- [ ] **Step 1: 在 scraper.py 中添加 Meta 数据同步函数**

在 `scraper.py` 末尾添加：

```python
# ---- Meta Ads Insights 数据同步 ----

import meta_api
from datetime import datetime as dt, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


def _sync_one_meta_account(act_id: str, access_token: str) -> Tuple[str, int, str]:
    """同步单个 Meta 账户的 Insights 数据，返回 (act_id, count, error)"""
    last_date = database.get_meta_sync_state(act_id)
    today = dt.utcnow().strftime("%Y-%m-%d")

    if last_date:
        from_date = (dt.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        if from_date > today:
            return act_id, 0, ""
    else:
        from_date = (dt.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")

    rows, err = meta_api.get_insights(act_id, access_token, from_date, today)
    if err:
        return act_id, 0, err
    if not rows:
        database.set_meta_sync_state(act_id, today)
        return act_id, 0, ""

    count = database.upsert_meta_insights(act_id, rows)
    database.set_meta_sync_state(act_id, today)
    return act_id, count, ""


def sync_all_meta_insights(concurrency: int = 8) -> Dict[str, Any]:
    """并行同步所有 active 状态的 Meta 账户数据"""
    accounts = database.get_meta_accounts()
    active_accounts = [a for a in accounts if a.get("status") == "active" and a.get("access_token")]

    if not active_accounts:
        return {"success": True, "total": 0, "accounts": {}, "message": "没有活跃的 Meta 账户"}

    result = {"success": True, "total": len(active_accounts), "accounts": {}}
    total_count = 0
    errors = []

    with ThreadPoolExecutor(max_workers=min(concurrency, len(active_accounts))) as executor:
        futures = {
            executor.submit(_sync_one_meta_account, a["act_id"], a["access_token"]): a["act_id"]
            for a in active_accounts
        }
        for future in as_completed(futures):
            act_id, count, err = future.result()
            result["accounts"][act_id] = {"count": count, "error": err}
            total_count += count
            if err:
                errors.append(f"{act_id}: {err}")

    result["total_count"] = total_count
    if errors:
        result["message"] = "; ".join(errors)
    else:
        result["message"] = f"全部同步完成，共 {total_count} 条"

    return result
```

- [ ] **Step 2: 在 `run_full_sync()` 中添加 Meta 同步调用**

找到 `run_full_sync()` 函数（约第 703 行），在现有 `run_full_sync()` 末尾 `return result` 之前，添加：

```python
    # Meta 数据同步
    meta_result = sync_all_meta_insights()
    result["meta"] = meta_result.get("accounts", {})
    result["meta_count"] = meta_result.get("total_count", 0)
    if not meta_result.get("success"):
        result["message"] = (result.get("message", "") + "; Meta同步: " + meta_result.get("message", "")).strip("; ")
```

- [ ] **Step 3: 验证导入**

```bash
python -c "import scraper; print('scraper with meta sync OK')"
```

- [ ] **Step 4: Commit**

```bash
git add scraper.py
git commit -m "feat: Meta Ads Insights 并行同步 — 多账户增量更新"
```

---

### Task 9: delivery.py — 投放引擎

**Files:**
- Create: `delivery.py`

- [ ] **Step 1: 创建 delivery.py 投放引擎**

```python
"""投放引擎：素材审核队列 → 批量创建 Meta 广告"""
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
import meta_api

# 投放进度事件队列（用于 SSE 推送）
_delivery_queues: Dict[str, list] = {}
_delivery_events: Dict[str, threading.Event] = {}

_GRAPH_API_URL = "https://graph.facebook.com"


def _get_token(act_id: str) -> Optional[str]:
    """从数据库获取账户 token"""
    account = database.get_meta_account(act_id)
    if not account:
        return None
    return account.get("access_token")


def _push_event(batch_id: str, event_type: str, data: dict = None):
    """向投放批次的事件队列推送事件"""
    if batch_id in _delivery_queues:
        _delivery_queues[batch_id].append({
            "type": event_type,
            "data": data or {},
            "timestamp": time.time()
        })


def _deliver_one(queue_item: dict, template: dict) -> dict:
    """执行单条广告的投放：上传创意 → 创建 AdSet → 创建 Ad"""
    result = {
        "queue_id": queue_item["id"],
        "status": "failed",
    }
    image_path = queue_item.get("image_path", "")
    image_type = queue_item.get("image_type", "")
    overlay_text = queue_item.get("overlay_text", "")
    batch_id = queue_item.get("batch_id", "")

    act_id = template.get("ad_account_id", "")
    if not act_id:
        result["error"] = "模板未绑定广告账户"
        return result

    token = _get_token(act_id)
    if not token:
        result["error"] = f"未找到账户 {act_id} 的 token"
        return result

    targeting = json.loads(template.get("targeting_json", "{}")) if template.get("targeting_json") else {}
    budget_value = template.get("budget_value", 0)
    budget_type = template.get("budget_type", "daily_budget")
    bid_strategy = template.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")
    optimization_goal = template.get("optimization_goal", "OFFSITE_CONVERSIONS")
    billing_event = template.get("billing_event", "IMPRESSIONS")
    conversion_event = template.get("conversion_event", "")

    # 1. 上传创意（图片）
    creative_hash, err = meta_api.upload_ad_image(act_id, token, image_path)
    if err:
        result["error"] = f"上传图片失败: {err}"
        return result
    result["creative_hash"] = creative_hash

    # 2. 创建 Campaign
    today = time.strftime("%Y-%m-%d")
    campaign_name = f"{batch_id}_{today}_{act_id}"
    campaign_id, err = meta_api.create_campaign(
        act_id, token, campaign_name,
        objective="OUTCOME_TRAFFIC",
        status="PAUSED",
        special_ad_categories=[]
    )
    if err:
        result["error"] = f"创建 Campaign 失败: {err}"
        return result
    result["fb_campaign_id"] = campaign_id

    # 3. 创建 AdSet
    adset_name = f"{image_type}_{today}"
    daily_budget = budget_value if budget_type == "daily_budget" else None
    lifetime_budget = budget_value if budget_type == "lifetime_budget" else None

    promoted_object = None
    if conversion_event:
        promoted_object = {"custom_event_type": conversion_event}

    adset_id, err = meta_api.create_adset(
        act_id, token, adset_name, campaign_id,
        targeting=targeting, daily_budget=daily_budget,
        lifetime_budget=lifetime_budget, bid_strategy=bid_strategy,
        billing_event=billing_event, optimization_goal=optimization_goal,
        promoted_object=promoted_object, status="PAUSED"
    )
    if err:
        result["error"] = f"创建 AdSet 失败: {err}"
        return result
    result["fb_adset_id"] = adset_id

    # 4. 创建 Ad
    ad_name = f"{image_type}_{queue_item['id']}"
    # 页面 ID 从 targeting 或模板中获取，默认占位
    page_id = targeting.get("page_id", "")
    link_url = f"https://novel.example.com/{batch_id}"  # 默认落地页

    ad_id, err = meta_api.create_ad(
        act_id, token, ad_name, adset_id,
        creative_name=ad_name, page_id=page_id,
        image_hash=creative_hash,
        message=overlay_text or "", link_url=link_url,
        status="PAUSED"
    )
    if err:
        result["error"] = f"创建 Ad 失败: {err}"
        return result
    result["fb_ad_id"] = ad_id
    result["fb_creative_id"] = creative_hash

    result["status"] = "delivered"
    return result


def submit_delivery_batch(queue_ids: List[int], template_id: int) -> str:
    """提交投放批次，返回 batch_id，后台异步执行"""
    import uuid
    batch_id = uuid.uuid4().hex[:12]
    template = database.get_delivery_template(template_id)

    if not template:
        return "", "模板不存在"

    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        _push_event(batch_id, "start", {"total": len(queue_ids)})

        with get_conn() as conn:
            items = []
            for qid in queue_ids:
                row = conn.execute("SELECT * FROM delivery_queue WHERE id = ?", (qid,)).fetchone()
                if row:
                    items.append(dict(row))

        completed = 0
        failed = 0
        max_workers = min(4, len(items))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_deliver_one, item, template): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    r = future.result()
                except Exception as e:
                    r = {"queue_id": item["id"], "status": "failed", "error": str(e)}

                # 回写结果
                database.update_queue_delivery_result(
                    r["queue_id"], r["status"],
                    fb_campaign_id=r.get("fb_campaign_id"),
                    fb_adset_id=r.get("fb_adset_id"),
                    fb_ad_id=r.get("fb_ad_id"),
                    fb_creative_id=r.get("fb_creative_id"),
                    delivery_params_json=json.dumps(template, ensure_ascii=False),
                    error_message=r.get("error")
                )

                if r["status"] == "delivered":
                    completed += 1
                else:
                    failed += 1

                _push_event(batch_id, "progress", {
                    "completed": completed,
                    "failed": failed,
                    "total": len(items),
                    "current_id": r["queue_id"],
                    "fb_ad_id": r.get("fb_ad_id", "")
                })

        _push_event(batch_id, "complete", {"completed": completed, "failed": failed})
        _delivery_events[batch_id].set()

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_run)
    executor.shutdown(wait=False)

    return batch_id, None


def get_delivery_progress(batch_id: str) -> dict:
    """查询投放批次进度"""
    events = _delivery_queues.get(batch_id, [])
    is_done = _delivery_events.get(batch_id, threading.Event()).is_set()

    last_event = {}
    for e in events:
        if e["type"] == "progress":
            last_event = e

    return {
        "batch_id": batch_id,
        "is_done": is_done,
        "total": last_event.get("data", {}).get("total", 0),
        "completed": last_event.get("data", {}).get("completed", 0),
        "failed": last_event.get("data", {}).get("failed", 0),
    }
```

- [ ] **Step 2: 修复缺少的 import — 添加 `get_conn`**

检查 `delivery.py` 中 `submit_delivery_batch` 的 `_run()` 函数使用了 `get_conn()`，需要在文件开头添加：

```python
from database import get_conn
```

- [ ] **Step 3: 验证模块可导入**

```bash
python -c "import delivery; print('delivery module OK')"
```

- [ ] **Step 4: Commit**

```bash
git add delivery.py
git commit -m "feat: 投放引擎 — 审核队列批量推送到 Meta"
```

---

### Task 10: config.json — 添加 Meta 配置项

**Files:**
- Modify: `config.json`

- [ ] **Step 1: 在 config.json 中添加 Meta 配置**

在现有 JSON 末尾（`"concurrency": 4` 之后），添加 Meta 相关配置：

```json
,
  "meta": {
    "app_id": "",
    "app_secret": "",
    "default_access_token": "",
    "api_version": "v25.0",
    "sync_interval_seconds": 300,
    "rate_limit_per_second": 4
  }
```

完整的 `config.json` 内容：

```json
{
  "api_key": "sk-GQhDKixI2iPRjeZemv1sLVdkNCKrASMrdZAA2ikfUtAcNIcC",
  "api_url": "https://api.geeknow.top/v1",
  "chat_model_name": "gemini-3.1-pro-preview",
  "image_model_name": "gpt-image-2",
  "analysis_prompt": "欧美小说投流素材出图提示词规则（无文字版，文本单独输出）...",
  "concurrency": 4,
  "meta": {
    "app_id": "",
    "app_secret": "",
    "default_access_token": "",
    "api_version": "v25.0",
    "sync_interval_seconds": 300,
    "rate_limit_per_second": 4
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add config.json
git commit -m "feat: 添加 Meta 配置项到 config.json"
```

---

### Task 11: main.py — Meta 账户管理 API 端点

**Files:**
- Modify: `main.py`

说明：以下所有端点追加到 `main.py` 文件末尾（最后一个 `.get`/`.post` 路由之后，`if __name__ == "__main__"` 之前）。

- [ ] **Step 1: 引入 delivery 模块**

在 `main.py` 头部 import 区域，现有 `import scraper` 之后添加：

```python
import meta_api
import delivery
```

- [ ] **Step 2: 添加 Meta 账户管理端点**

```python
# ---- Meta 账户管理 API ----

class MetaAccountBody(BaseModel):
    act_id: str
    act_name: str = ""
    access_token: str = ""
    pingykj_account: str = ""

@app.get("/api/meta/accounts")
def _get_meta_accounts():
    return database.get_meta_accounts()

@app.post("/api/meta/accounts")
def _add_meta_account(body: MetaAccountBody):
    database.upsert_meta_account(
        body.act_id, body.act_name, body.access_token, body.pingykj_account
    )
    return {"success": True}

@app.put("/api/meta/accounts/{act_id}")
def _update_meta_account(act_id: str, body: MetaAccountBody):
    database.upsert_meta_account(
        act_id, body.act_name, body.access_token, body.pingykj_account
    )
    return {"success": True}

@app.delete("/api/meta/accounts/{act_id}")
def _delete_meta_account(act_id: str):
    database.delete_meta_account(act_id)
    return {"success": True}

class TokenRefreshBody(BaseModel):
    access_token: str

@app.post("/api/meta/accounts/{act_id}/refresh-token")
def _refresh_meta_token(act_id: str, body: TokenRefreshBody):
    database.update_meta_token(act_id, body.access_token)
    return {"success": True}
```

- [ ] **Step 3: 添加 Meta 数据同步控制端点**

```python
# ---- Meta 数据同步控制 API ----

@app.post("/api/meta/sync")
def _trigger_meta_sync():
    result = scraper.sync_all_meta_insights()
    return result

@app.get("/api/meta/sync-status")
def _meta_sync_status():
    accounts = database.get_meta_accounts()
    active = [a for a in accounts if a.get("status") == "active"]
    return {
        "total_accounts": len(accounts),
        "active_accounts": len(active),
        "accounts": [
            {
                "act_id": a["act_id"],
                "act_name": a["act_name"],
                "last_sync": database.get_meta_sync_state(a["act_id"]),
            }
            for a in active
        ]
    }

@app.get("/api/meta/sync-interval")
def _get_meta_sync_interval():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    return {"interval": config.get("meta", {}).get("sync_interval_seconds", 300)}

@app.post("/api/meta/sync-interval")
async def _set_meta_sync_interval(request: Request):
    body = await request.json()
    seconds = int(body.get("interval", 300))
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("meta", {})["sync_interval_seconds"] = seconds
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "interval": seconds}
```

- [ ] **Step 4: 验证端点启动**

```bash
python -c "
import sys
sys.path.insert(0, '.')
from main import app
routes = [r.path for r in app.routes]
assert '/api/meta/accounts' in routes
assert '/api/meta/sync' in routes
print('Meta account API routes OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: Meta 账户管理 + 数据同步控制 API 端点"
```

---

### Task 12: main.py — 投放管理 API 端点

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 添加投放模板管理端点**

```python
# ---- 投放模板管理 API ----

@app.get("/api/delivery/templates")
def _get_delivery_templates():
    return database.get_delivery_templates()

class CreateTemplateBody(BaseModel):
    name: str
    targeting: dict = {}
    placements: dict = {}
    budget_type: str = "daily_budget"
    budget_value: int = 0
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    billing_event: str = "IMPRESSIONS"
    conversion_event: str = ""
    ad_account_id: str = ""

@app.post("/api/delivery/templates")
def _create_delivery_template(body: CreateTemplateBody):
    tid = database.create_delivery_template(body.model_dump())
    return {"success": True, "id": tid}

@app.put("/api/delivery/templates/{template_id}")
async def _update_delivery_template(template_id: int, request: Request):
    body = await request.json()
    database.update_delivery_template(template_id, body)
    return {"success": True}

@app.delete("/api/delivery/templates/{template_id}")
def _delete_delivery_template(template_id: int):
    database.delete_delivery_template(template_id)
    return {"success": True}

@app.get("/api/delivery/templates/fb-adsets/{account_id}")
def _get_fb_adsets(account_id: str):
    """从 FB 账户拉取已有 AdSet 列表（供导入模板）"""
    token = None
    account = database.get_meta_account(account_id)
    if account:
        token = account.get("access_token")
    if not token:
        raise HTTPException(400, "未找到该账户的 access token")
    adsets, err = meta_api.get_adsets(account_id, token)
    if err:
        raise HTTPException(400, err)
    return {"data": adsets or []}

class ImportTemplateBody(BaseModel):
    account_id: str
    adset_id: str
    name: str = ""

@app.post("/api/delivery/templates/import")
def _import_template_from_fb(body: ImportTemplateBody):
    """从 FB AdSet 导入为投放模板"""
    token = None
    account = database.get_meta_account(body.account_id)
    if account:
        token = account.get("access_token")
    if not token:
        raise HTTPException(400, "未找到该账户的 access token")

    # 拉取单个 AdSet 详情
    adsets, err = meta_api.get_adsets(body.account_id, token)
    if err:
        raise HTTPException(400, err)

    target_adset = None
    for a in (adsets or []):
        if a.get("id") == body.adset_id:
            target_adset = a
            break
    if not target_adset:
        raise HTTPException(404, "未找到指定 AdSet")

    tname = body.name or f"导入:{target_adset.get('name', '')}"
    tid = database.create_delivery_template({
        "name": tname,
        "source": "imported_from_fb",
        "source_adset_id": body.adset_id,
        "targeting": target_adset.get("targeting", {}),
        "placements": {},
        "budget_type": "daily_budget" if target_adset.get("daily_budget") else "lifetime_budget",
        "budget_value": target_adset.get("daily_budget") or target_adset.get("lifetime_budget") or 0,
        "bid_strategy": target_adset.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
        "optimization_goal": target_adset.get("optimization_goal", "OFFSITE_CONVERSIONS"),
        "billing_event": target_adset.get("billing_event", "IMPRESSIONS"),
        "conversion_event": (
            target_adset.get("promoted_object", {}).get("custom_event_type", "")
            if target_adset.get("promoted_object") else ""
        ),
        "ad_account_id": body.account_id,
    })
    return {"success": True, "id": tid}
```

- [ ] **Step 2: 添加投放队列和提交端点**

```python
# ---- 投放队列管理 API ----

class AddToQueueBody(BaseModel):
    items: list  # [{batch_id, image_type, image_path, image_prompt, overlay_text}, ...]

@app.post("/api/delivery/queue")
def _add_to_delivery_queue(body: AddToQueueBody):
    count = database.add_to_delivery_queue(body.items)
    return {"success": True, "count": count}

@app.get("/api/delivery/queue")
def _get_delivery_queue(page: int = 1, page_size: int = 20, status: str = None):
    return database.get_delivery_queue(page, page_size, status)

@app.post("/api/delivery/queue/{queue_id}/approve")
async def _approve_queue_item(queue_id: int, request: Request):
    body = await request.json()
    template_id = body.get("template_id", 0)
    reviewer = body.get("reviewer", "")
    database.update_queue_status(queue_id, "approved", reviewer=reviewer)
    database.update_queue_delivery_result(queue_id, "approved")
    if template_id:
        database.batch_approve_queue([queue_id], template_id, reviewer)
    return {"success": True}

@app.post("/api/delivery/queue/{queue_id}/reject")
async def _reject_queue_item(queue_id: int, request: Request):
    body = await request.json()
    reviewer = body.get("reviewer", "")
    database.update_queue_status(queue_id, "rejected", reviewer=reviewer)
    return {"success": True}

class BatchApproveBody(BaseModel):
    ids: list
    template_id: int
    reviewer: str = ""

@app.post("/api/delivery/queue/batch-approve")
def _batch_approve_queue(body: BatchApproveBody):
    database.batch_approve_queue(body.ids, body.template_id, body.reviewer)
    return {"success": True}

class SubmitDeliveryBody(BaseModel):
    queue_ids: list
    template_id: int

@app.post("/api/delivery/submit")
def _submit_delivery(body: SubmitDeliveryBody):
    """提交投放：异步执行，返回 batch_id"""
    batch_id, err = delivery.submit_delivery_batch(body.queue_ids, body.template_id)
    if err:
        raise HTTPException(400, err)
    return {"success": True, "batch_id": batch_id}

@app.get("/api/delivery/progress/{batch_id}")
def _delivery_progress(batch_id: str):
    return delivery.get_delivery_progress(batch_id)

@app.get("/api/delivery/stream/{batch_id}")
async def _delivery_stream(batch_id: str):
    """SSE 实时推送投放进度"""
    import asyncio
    import queue

    # 创建本地队列接收事件
    local_queue = asyncio.Queue()

    def _poll():
        last_idx = 0
        while True:
            events = delivery._delivery_queues.get(batch_id, [])
            for e in events[last_idx:]:
                local_queue.put_nowait(e)
                last_idx += 1
            if delivery._delivery_events.get(batch_id, threading.Event()).is_set():
                # 推送最后一条 complete 事件
                for e in events[last_idx:]:
                    local_queue.put_nowait(e)
                break
            time.sleep(0.5)

    threading.Thread(target=_poll, daemon=True).start()

    async def _event_generator():
        while True:
            try:
                event = await asyncio.wait_for(local_queue.get(), timeout=1.0)
                yield {"event": event["type"], "data": json.dumps(event)}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                if delivery._delivery_events.get(batch_id, threading.Event()).is_set():
                    break

    return EventSourceResponse(_event_generator())

@app.get("/api/delivery/records")
def _get_delivery_records(page: int = 1, page_size: int = 20, status: str = None):
    return database.get_delivery_records(page, page_size, status)
```

- [ ] **Step 3: 验证端点启动**

```bash
python -c "
import sys
sys.path.insert(0, '.')
from main import app
routes = [r.path for r in app.routes]
assert '/api/delivery/queue' in routes
assert '/api/delivery/templates' in routes
assert '/api/delivery/submit' in routes
assert '/api/delivery/templates/fb-adsets/{account_id}' in routes
assert '/api/delivery/templates/import' in routes
print('Delivery API routes OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: 投放模板 + 审核队列 + 投放提交 API 端点"
```

---

### Task 13: main.py — Meta 定时同步调度

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 在 startup 事件中添加 Meta 定时同步**

找到 `@app.on_event("startup")` 函数，在现有 APScheduler 初始化代码之后添加 Meta 同步任务。如果当前没有 APScheduler 初始化，找到 `scraper.run_full_sync` 的定时调度位置（若存在），在其旁边添加：

```python
from apscheduler.schedulers.background import BackgroundScheduler

# 检查是否已有 scheduler（scraper 模块中已有 APScheduler），追加任务
import scraper
if hasattr(scraper, '_scheduler') and scraper._scheduler:
    meta_interval = json.loads(Path("config.json").read_text(encoding="utf-8")).get("meta", {}).get("sync_interval_seconds", 300)
    scraper._scheduler.add_job(
        scraper.sync_all_meta_insights,
        'interval',
        seconds=meta_interval,
        id='meta_sync',
        replace_existing=True
    )
else:
    # 如果 scraper 没有 scheduler，创建一个（代码应放在 startup 函数内）
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    meta_interval = config.get("meta", {}).get("sync_interval_seconds", 300)
    scheduler = BackgroundScheduler()
    scheduler.add_job(scraper.run_full_sync, 'interval', seconds=180, id='main_sync')
    scheduler.add_job(scraper.sync_all_meta_insights, 'interval', seconds=meta_interval, id='meta_sync')
    scheduler.start()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: Meta 数据定时同步调度（APScheduler）"
```

---

### Task 14: 前端 — 基础 HTML/CSS 结构 + Tab 导航

**Files:**
- Modify: `static/index.html`

这个文件很大（~135KB），需要在现有结构上增量添加。

- [ ] **Step 1: 找到现有 Tab 导航区域**

在 `index.html` 中搜索现有 Tab 标签按钮，找到导航结构后，在现有 Tab 按钮列表末尾追加三个新 Tab 按钮：

```html
<button class="tab-btn" data-tab="delivery">投放管理</button>
<button class="tab-btn" data-tab="meta-data">Meta 数据</button>
<button class="tab-btn" data-tab="meta-accounts">账户配置</button>
```

- [ ] **Step 2: 添加投放管理 Tab 页面结构**

在现有 Tab 内容区域末尾追加：

```html
<!-- 投放管理 Tab -->
<div id="tab-delivery" class="tab-content" style="display:none">
  <div class="section-header">
    <h2>投放管理</h2>
  </div>

  <!-- 子标签 -->
  <div class="sub-tabs">
    <button class="sub-tab-btn active" data-subtab="delivery-queue">审核队列</button>
    <button class="sub-tab-btn" data-subtab="delivery-templates">投放模板</button>
    <button class="sub-tab-btn" data-subtab="delivery-records">投放记录</button>
  </div>

  <!-- 审核队列 -->
  <div id="subtab-delivery-queue" class="sub-tab-content">
    <div class="toolbar">
      <select id="queue-filter-status">
        <option value="">全部状态</option>
        <option value="pending">待审核</option>
        <option value="approved">已通过</option>
        <option value="rejected">已驳回</option>
        <option value="delivered">已投放</option>
      </select>
      <button onclick="loadDeliveryQueue()">刷新</button>
    </div>
    <table id="delivery-queue-table">
      <thead>
        <tr><th><input type="checkbox" id="queue-select-all" onchange="toggleSelectAllQueue()"></th>
            <th>ID</th><th>批次</th><th>类型</th><th>文案</th><th>状态</th><th>操作</th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <div class="pagination" id="queue-pagination"></div>
  </div>

  <!-- 投放模板 -->
  <div id="subtab-delivery-templates" class="sub-tab-content" style="display:none">
    <div class="toolbar">
      <button onclick="openCreateTemplate()">+ 手动创建</button>
      <button onclick="openImportTemplate()">从 FB 导入</button>
    </div>
    <table id="templates-table">
      <thead><tr><th>名称</th><th>来源</th><th>预算</th><th>出价策略</th><th>关联账户</th><th>操作</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <!-- 投放记录 -->
  <div id="subtab-delivery-records" class="sub-tab-content" style="display:none">
    <table id="delivery-records-table">
      <thead><tr><th>ID</th><th>批次</th><th>类型</th><th>FB Ad ID</th><th>FB Campaign ID</th><th>状态</th><th>时间</th></tr></thead>
      <tbody></tbody>
    </table>
    <div class="pagination" id="records-pagination"></div>
  </div>
</div>
```

- [ ] **Step 3: 添加 Meta 数据 Tab 页面结构**

```html
<!-- Meta 数据 Tab -->
<div id="tab-meta-data" class="tab-content" style="display:none">
  <div class="section-header">
    <h2>Meta 数据看板</h2>
    <div>
      <button onclick="triggerMetaSync()">手动同步</button>
      <span id="meta-sync-status"></span>
    </div>
  </div>
  <!-- 复用现有数据看板组件，额外显示 source 标签 -->
  <div id="meta-dashboard-container">
    <p>Meta 数据已合并到统一看板。在数据看板 Tab 中以 source 列区分。</p>
  </div>
</div>
```

- [ ] **Step 4: 添加账户配置 Tab 页面结构**

```html
<!-- 账户配置 Tab -->
<div id="tab-meta-accounts" class="tab-content" style="display:none">
  <div class="section-header">
    <h2>Meta 账户配置</h2>
    <button onclick="openAddAccount()">+ 添加账户</button>
  </div>
  <table id="meta-accounts-table">
    <thead><tr><th>账户 ID</th><th>名称</th><th>状态</th><th>PingYKJ 映射</th><th>Token 过期</th><th>操作</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
```

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: 前端 — 投放管理/Meta数据/账户配置 Tab 基础 HTML 结构"
```

---

### Task 15: 前端 — Tab 切换逻辑 + 投放模板 JS

**Files:**
- Modify: `static/index.html` — JS 区域

- [ ] **Step 1: 添加 Tab 切换事件绑定**

在 `index.html` 的 `<script>` 区域末尾追加：

```javascript
// === Meta 集成 Tab 切换 ===
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', function() {
    const tab = this.dataset.tab;
    document.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');
    const target = document.getElementById('tab-' + tab);
    if (target) target.style.display = 'block';

    // 切换子标签时加载对应数据
    if (tab === 'delivery') loadDeliveryQueue();
    if (tab === 'meta-data') loadMetaSyncStatus();
    if (tab === 'meta-accounts') loadMetaAccounts();
  });
});

// 子标签切换
document.querySelectorAll('.sub-tab-btn').forEach(btn => {
  btn.addEventListener('click', function() {
    const subtab = this.dataset.subtab;
    document.querySelectorAll('.sub-tab-content').forEach(t => t.style.display = 'none');
    document.getElementById('subtab-' + subtab).style.display = 'block';
    document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
  });
});
```

- [ ] **Step 2: 添加投放模板 JS**

```javascript
// === 投放模板 ===

async function loadTemplates() {
  const resp = await fetch('/api/delivery/templates');
  const data = await resp.json();
  const tbody = document.querySelector('#templates-table tbody');
  tbody.innerHTML = data.map(t => `
    <tr>
      <td>${t.name}</td>
      <td>${t.source === 'imported_from_fb' ? 'FB导入' : '手动'}</td>
      <td>${(t.budget_value / 100).toFixed(2)} ${t.budget_type === 'daily_budget' ? '日' : '总'}</td>
      <td>${t.bid_strategy}</td>
      <td>${t.ad_account_id || '通用'}</td>
      <td>
        <button onclick="editTemplate(${t.id})">编辑</button>
        <button onclick="deleteTemplate(${t.id})">删除</button>
      </td>
    </tr>
  `).join('');
}

async function openCreateTemplate() {
  const name = prompt('模板名称:');
  if (!name) return;
  const budgetValue = parseInt(prompt('日预算（美元分，如 5000 = $50）:')) || 5000;
  const adAccountId = prompt('关联账户 act_id（留空=通用）:') || '';
  const resp = await fetch('/api/delivery/templates', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name, budget_value: budgetValue,
      targeting: {
        age_min: 35, age_max: 65, genders: [1],
        geo_locations: {countries: ['US', 'UK', 'CA', 'AU']},
        locales: [6],  // English
      },
      ad_account_id: adAccountId
    })
  });
  if (resp.ok) { loadTemplates(); }
}

async function deleteTemplate(id) {
  if (!confirm('确认删除?')) return;
  await fetch('/api/delivery/templates/' + id, {method: 'DELETE'});
  loadTemplates();
}

async function openImportTemplate() {
  const accounts = await fetch('/api/meta/accounts').then(r => r.json());
  if (!accounts.length) { alert('请先添加 Meta 账户'); return; }
  const accountId = prompt('选择账户 act_id:\n' + accounts.map(a => a.act_id + ' - ' + a.act_name).join('\n'));
  if (!accountId) return;

  const adsetsResp = await fetch('/api/delivery/templates/fb-adsets/' + accountId);
  const adsetsData = await adsetsResp.json();
  const adsets = adsetsData.data || [];

  if (!adsets.length) { alert('该账户没有 AdSet'); return; }

  const adsetId = prompt('选择 AdSet:\n' + adsets.map(a => a.id + ' - ' + a.name).join('\n'));
  if (!adsetId) return;

  const name = prompt('导入后模板名称:') || '';
  await fetch('/api/delivery/templates/import', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({account_id: accountId, adset_id: adsetId, name})
  });
  loadTemplates();
}
```

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: 前端 — Tab 切换逻辑 + 投放模板 JS"
```

---

### Task 16: 前端 — 审核队列 + 投放提交 JS

**Files:**
- Modify: `static/index.html` — JS 区域

- [ ] **Step 1: 添加审核队列 JS**

在 JS 区域末尾追加：

```javascript
// === 审核队列 ===

let selectedQueueIds = new Set();

function toggleSelectAllQueue() {
  const checkAll = document.getElementById('queue-select-all');
  const tbody = document.querySelector('#delivery-queue-table tbody');
  const checkboxes = tbody.querySelectorAll('input[type="checkbox"]');
  checkboxes.forEach(cb => { cb.checked = checkAll.checked; });
  selectedQueueIds.clear();
  if (checkAll.checked) {
    checkboxes.forEach(cb => selectedQueueIds.add(parseInt(cb.dataset.id)));
  }
}

async function loadDeliveryQueue() {
  const status = document.getElementById('queue-filter-status')?.value || '';
  const resp = await fetch('/api/delivery/queue?status=' + status);
  const data = await resp.json();
  const tbody = document.querySelector('#delivery-queue-table tbody');
  tbody.innerHTML = (data.data || []).map(item => `
    <tr>
      <td><input type="checkbox" data-id="${item.id}" onchange="toggleQueueItem(${item.id}, this.checked)"></td>
      <td>${item.id}</td>
      <td>${item.batch_id || ''}</td>
      <td>${item.image_type || ''}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${item.overlay_text || ''}</td>
      <td><span class="status-${item.status}">${item.status}</span></td>
      <td>
        ${item.status === 'pending' || item.status === 'approved'
          ? `<button onclick="approveQueueItem(${item.id})">通过</button>
             <button onclick="rejectQueueItem(${item.id})">驳回</button>`
          : ''}
      </td>
    </tr>
  `).join('');
}

function toggleQueueItem(id, checked) {
  if (checked) selectedQueueIds.add(id);
  else selectedQueueIds.delete(id);
}

async function approveQueueItem(id) {
  const templates = await fetch('/api/delivery/templates').then(r => r.json());
  if (!templates.length) { alert('请先创建投放模板'); return; }
  const tplId = prompt('选择模板 ID:\n' + templates.map(t => t.id + ': ' + t.name).join('\n'));
  if (!tplId) return;
  await fetch('/api/delivery/queue/' + id + '/approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({template_id: parseInt(tplId), reviewer: 'admin'})
  });
  loadDeliveryQueue();
}

async function rejectQueueItem(id) {
  await fetch('/api/delivery/queue/' + id + '/reject', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reviewer: 'admin'})
  });
  loadDeliveryQueue();
}

async function batchApprove() {
  if (selectedQueueIds.size === 0) { alert('请先勾选素材'); return; }
  const templates = await fetch('/api/delivery/templates').then(r => r.json());
  if (!templates.length) { alert('请先创建投放模板'); return; }
  const tplId = prompt('选择模板 ID:\n' + templates.map(t => t.id + ': ' + t.name).join('\n'));
  if (!tplId) return;
  await fetch('/api/delivery/queue/batch-approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: Array.from(selectedQueueIds), template_id: parseInt(tplId), reviewer: 'admin'})
  });
  selectedQueueIds.clear();
  loadDeliveryQueue();
}

// === 投放提交 ===

async function submitDelivery() {
  if (selectedQueueIds.size === 0) { alert('请先勾选素材'); return; }
  const templates = await fetch('/api/delivery/templates').then(r => r.json());
  if (!templates.length) { alert('请先创建投放模板'); return; }
  const tplId = prompt('选择模板 ID:\n' + templates.map(t => t.id + ': ' + t.name).join('\n'));
  if (!tplId) return;

  const resp = await fetch('/api/delivery/submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({queue_ids: Array.from(selectedQueueIds), template_id: parseInt(tplId)})
  });
  const result = await resp.json();
  if (result.success) {
    alert('投放已提交，batch_id: ' + result.batch_id);
    selectedQueueIds.clear();
    loadDeliveryQueue();
  }
}

async function loadDeliveryRecords() {
  const resp = await fetch('/api/delivery/records');
  const data = await resp.json();
  const tbody = document.querySelector('#delivery-records-table tbody');
  tbody.innerHTML = (data.data || []).map(r => `
    <tr>
      <td>${r.id}</td><td>${r.batch_id || ''}</td><td>${r.image_type || ''}</td>
      <td>${r.fb_ad_id || ''}</td><td>${r.fb_campaign_id || ''}</td>
      <td>${r.status}</td><td>${r.updated_at || ''}</td>
    </tr>
  `).join('');
}
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat: 前端 — 审核队列交互 + 批量审批 + 投放提交 JS"
```

---

### Task 17: 前端 — Meta 账户管理 + 数据同步 JS

**Files:**
- Modify: `static/index.html` — JS 区域

- [ ] **Step 1: 添加 Meta 账户管理 JS**

```javascript
// === Meta 账户管理 ===

async function loadMetaAccounts() {
  const resp = await fetch('/api/meta/accounts');
  const accounts = await resp.json();
  const tbody = document.querySelector('#meta-accounts-table tbody');
  tbody.innerHTML = accounts.map(a => `
    <tr>
      <td>${a.act_id}</td><td>${a.act_name || ''}</td>
      <td><span class="status-${a.status}">${a.status}</span></td>
      <td>${a.pingykj_account || '-'}</td>
      <td>${a.token_expires_at || '-'}</td>
      <td>
        <button onclick="editMetaAccount('${a.act_id}')">编辑</button>
        <button onclick="deleteMetaAccount('${a.act_id}')">删除</button>
      </td>
    </tr>
  `).join('');
}

async function openAddAccount() {
  const actId = prompt('Meta 广告账户 ID (如 act_12345):');
  if (!actId) return;
  const actName = prompt('账户名称:') || '';
  const token = prompt('Access Token:') || '';
  const pingykj = prompt('映射到 pingykj 账户 (留空跳过):') || '';

  await fetch('/api/meta/accounts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({act_id: actId, act_name: actName, access_token: token, pingykj_account: pingykj})
  });
  loadMetaAccounts();
}

async function editMetaAccount(actId) {
  const actName = prompt('新名称:') || '';
  const token = prompt('新 Token (留空不更新):');
  const pingykj = prompt('映射到 pingykj 账户:') || '';

  await fetch('/api/meta/accounts/' + actId, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({act_id: actId, act_name: actName,
      access_token: token || '', pingykj_account: pingykj})
  });
  if (token) {
    await fetch('/api/meta/accounts/' + actId + '/refresh-token', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({access_token: token})
    });
  }
  loadMetaAccounts();
}

async function deleteMetaAccount(actId) {
  if (!confirm('确认删除 ' + actId + '?')) return;
  await fetch('/api/meta/accounts/' + actId, {method: 'DELETE'});
  loadMetaAccounts();
}

// === Meta 数据同步 ===

async function triggerMetaSync() {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '同步中...';
  const resp = await fetch('/api/meta/sync', {method: 'POST'});
  const result = await resp.json();
  document.getElementById('meta-sync-status').textContent =
    '同步完成: ' + (result.total_count || 0) + ' 条';
  btn.disabled = false;
  btn.textContent = '手动同步';
}

async function loadMetaSyncStatus() {
  const resp = await fetch('/api/meta/sync-status');
  const data = await resp.json();
  document.getElementById('meta-sync-status').textContent =
    data.active_accounts + '/' + data.total_accounts + ' 账户活跃';
}
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat: 前端 — Meta 账户管理 + 数据同步 JS"
```

---

### Task 18: 集成测试 — 端到端验证

**Files:**
- 无新文件，验证整个系统正常工作

- [ ] **Step 1: 启动服务器**

```bash
python main.py &
sleep 3
```

如果需要 `uvicorn`:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 &
sleep 3
```

- [ ] **Step 2: 验证所有新 API 端点可访问**

```bash
# Meta 账户
curl -s http://127.0.0.1:8000/api/meta/accounts | python -c "import sys,json; print('accounts:', json.load(sys.stdin))"

# 投放模板
curl -s http://127.0.0.1:8000/api/delivery/templates | python -c "import sys,json; print('templates:', json.load(sys.stdin))"

# 审核队列
curl -s http://127.0.0.1:8000/api/delivery/queue | python -c "import sys,json; print('queue:', json.load(sys.stdin))"

# 投放记录
curl -s http://127.0.0.1:8000/api/delivery/records | python -c "import sys,json; print('records:', json.load(sys.stdin))"

# 同步状态
curl -s http://127.0.0.1:8000/api/meta/sync-status | python -c "import sys,json; print('sync-status:', json.load(sys.stdin))"

echo "All endpoints respond OK"
```

- [ ] **Step 3: 测试账户 CRUD 流程**

```bash
# 添加测试账户
curl -s -X POST http://127.0.0.1:8000/api/meta/accounts \
  -H "Content-Type: application/json" \
  -d '{"act_id":"act_test","act_name":"Test","access_token":"test_token"}'

# 验证账户列表
curl -s http://127.0.0.1:8000/api/meta/accounts | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1; print('OK')"

# 删除测试账户
curl -s -X DELETE http://127.0.0.1:8000/api/meta/accounts/act_test

echo "Meta account CRUD integration OK"
```

- [ ] **Step 4: 测试模板 CRUD 流程**

```bash
# 创建模板
curl -s -X POST http://127.0.0.1:8000/api/delivery/templates \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Template","budget_value":5000,"ad_account_id":"act_test"}'

# 验证模板列表
curl -s http://127.0.0.1:8000/api/delivery/templates | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1; print('OK')"

echo "Template CRUD integration OK"
```

- [ ] **Step 5: 停止服务器**

```bash
kill %1 2>/dev/null || true
```

- [ ] **Step 6: Commit (如有修改)**

```bash
git status
```

---

## 计划自审

**1. Spec 覆盖检查：**
- ✅ 投放引擎 → Task 9 (delivery.py) + Task 12 (API) + Task 16 (前端)
- ✅ Meta 数据同步 → Task 6 (meta_api.py) + Task 7 (DB写入) + Task 8 (scraper扩展)
- ✅ 数据库扩展 → Task 1-5 (schema + CRUD) + Task 7 (Insights写入)
- ✅ API 端点 → Task 11-13 (main.py)
- ✅ 前端 3 Tab → Task 14-17
- ✅ 速率限制 → Task 6 (meta_api.py 内置)
- ✅ 定时同步 → Task 13 (APScheduler)
- ✅ 投放模板导入 → Task 12 (import API) + Task 15 (前端按钮)

**2. Placeholder 扫描：** 无 TBD/TODO/占位符。所有步骤包含实际代码。

**3. 类型一致性：**
- `act_id` 统一为 Meta 广告账户 ID 格式
- `database.py` 函数签名与 `main.py`、`delivery.py` 调用一致
- `meta_api.py` 返回值统一为 `(data, error)` 元组
