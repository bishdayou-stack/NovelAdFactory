# 一键发布素材到 Meta（投放向导）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让生产中心/小说分析生成的素材（图+视频），通过投放向导配置成 1 系列 → N 广告组 → N 广告，一键以 PAUSED 草稿创建到 Meta。

**Architecture:** 在现有 delivery 引擎上补全前端 + 扩展 meta_api 字段 + 重构 delivery.py 为分层执行（1 系列 → N 组 → 组内 n 广告）。三类资产（主页/数据集/受众模版）用「discover → import 落库」模式，沿用 meta_accounts 先例。

**Tech Stack:** Python FastAPI（单文件 main.py）+ SQLite（database.py，无 ORM）+ meta_api.py（subprocess curl）+ 原生 JS/Tailwind（单文件 static/index.html）。

**Spec:** [../specs/2026-08-15-one-click-meta-delivery-design.md](../specs/2026-08-15-one-click-meta-delivery-design.md)

## Global Constraints

- 广告一律以 `status=PAUSED` 创建（草稿），绝不 ACTIVE。
- Meta API v25.0；HTTP 走 `meta_api._http_request`（curl subprocess + 代理），不要用 requests。
- 无单元测试框架；验证靠 `python -m py_compile` + curl 打 API + 前端手动走一遍。
- 前端是单文件 `static/index.html`，新增 UI 追加到该文件，不拆新文件。
- 素材 URL `/static/output/{batch_id}/{name}` 必须服务端转本地路径并校验，杜绝路径穿越。
- 已实测字段：Campaign 必传 `is_adset_budget_sharing_enabled`；AdSet 的 `destination_type=WEBSITE` / `promoted_object.pixel_id` / `attribution_spec` 均有效。

---

### Task 0: 核实剩余 Meta 字段（spike）

**Files:**
- 无代码改动；产出结论写入本任务

**Interfaces:**
- Produces: 明确的字段值，供 Task 1 的 meta_api 签名使用

**目标：** 用测试 token 对着 `act_1557179239285773` 试建草稿，敲定 4 个待核实字段。

- [ ] **Step 1: 核实 CBO（系列预算）字段**

试在 campaign 上加预算（CBO），看是否报错或成功：

```bash
TOKEN='<token>' ; P='-x http://127.0.0.1:10808'
curl -s $P -F "name=test-cbo" -F "objective=OUTCOME_SALES" -F "status=PAUSED" \
  -F "special_ad_categories=[]" -F "is_adset_budget_sharing_enabled=true" \
  -F "daily_budget=5000" -F "access_token=$TOKEN" \
  "https://graph.facebook.com/v25.0/act_1557179239285773/campaigns"
```

记录：CBO 是把 `daily_budget` 放 campaign 层（+ `is_adset_budget_sharing_enabled=true`），还是另有字段。结论决定 `delivery_campaigns` 的 budget 存储位置。

- [ ] **Step 2: 核实「单次成效目标费用」字段**

已建 campaign id `120249667236320344` 上试建 adset，分别试 `COST_CAP` 和 `TARGET_COST`：

```bash
curl -s $P -F "name=test-costcap" -F "campaign_id=120249667236320344" \
  -F "bid_strategy=COST_CAP" -F "bid_amount=500" \
  -F "billing_event=IMPRESSIONS" -F "optimization_goal=OFFSITE_CONVERSIONS" \
  -F "status=PAUSED" -F "targeting={}" -F "destination_type=WEBSITE" \
  -F 'promoted_object={"pixel_id":"927626999901271","custom_event_type":"PURCHASE"}' \
  -F "access_token=$TOKEN" "https://graph.facebook.com/v25.0/act_1557179239285773/adsets"
```

记录：`COST_CAP`+`bid_amount` 还是 `TARGET_COST`+`bid_amount` 能过。

- [ ] **Step 3: 核实归因「互动观看」event_type**

在 Step 2 成功的 adset 上，试 `attribution_spec` 加第三项 `{event_type: ..., window_days:1}`，枚举 `D2S`/`ENGAGED_VIEW`/`VIEW_THROUGH`，看哪个不报错。

- [ ] **Step 4: 确认合创/多广告主/动态素材默认关**

结论：本期不传对应字段即默认关（Meta 广告默认非合创、非多广告主、非动态创意）。无需再测，记录即可。

- [ ] **Step 5: 把结论回填到 Task 1 的签名注释里，提交**

（无代码改动，此任务以记录结论结束；不单独 commit。）

---

### Task 1: meta_api.py 扩展

**Files:**
- Modify: `meta_api.py`

**Interfaces:**
- Produces:
  - `get_pixels(act_id, access_token) -> (Optional[List[Dict]], Optional[str])`
  - `get_saved_audiences(act_id, access_token) -> (Optional[List[Dict]], Optional[str])`
  - `create_campaign(act_id, access_token, name, objective="OUTCOME_SALES", status="PAUSED", special_ad_categories=None, is_adset_budget_sharing_enabled=False)`
  - `create_adset(..., destination_type="WEBSITE", promoted_object=None, attribution_spec=None, bid_amount=None)`
  - `create_ad(..., call_to_action_type="LEARN_MORE")`

- [ ] **Step 1: 加 `get_pixels` 和 `get_saved_audiences`**

在 `meta_api.py` 的 `discover_pages`（约 512 行）之后追加：

```python
def get_pixels(act_id: str, access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """列出广告账户下的数据集(Pixel)。返回 [{"id","name"}]。"""
    _check_rate(act_id)
    data, err = _http_request("GET", f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/adspixels",
                              params={"fields": "id,name", "access_token": access_token})
    if err:
        return None, err
    return data.get("data", []), None


def get_saved_audiences(act_id: str, access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """列出账户保存的受众模版。返回 [{"id","name","targeting"}]。"""
    _check_rate(act_id)
    data, err = _http_request("GET", f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/saved_audiences",
                              params={"fields": "id,name,targeting", "access_token": access_token})
    if err:
        return None, err
    return data.get("data", []), None
```

- [ ] **Step 2: 扩展 `create_campaign` 加 `is_adset_budget_sharing_enabled`**

在现有 `create_campaign`（约 304 行）的 body 里加：

```python
    if is_adset_budget_sharing_enabled is not None:
        body["is_adset_budget_sharing_enabled"] = "true" if is_adset_budget_sharing_enabled else "false"
```

并把签名改为 `create_campaign(act_id, access_token, name, objective="OUTCOME_SALES", status="PAUSED", special_ad_categories=None, is_adset_budget_sharing_enabled=None)`。

- [ ] **Step 3: 扩展 `create_adset`**

签名追加 `destination_type="WEBSITE"`, `promoted_object=None`, `attribution_spec=None`, `bid_amount=None`。body 里加：

```python
    body["destination_type"] = destination_type
    if promoted_object:
        body["promoted_object"] = json.dumps(promoted_object)
    if attribution_spec:
        body["attribution_spec"] = json.dumps(attribution_spec)
    if bid_amount:
        body["bid_amount"] = str(bid_amount)
```

（Task 0 若确认 CBO 字段不同，在此一并补上。）

- [ ] **Step 4: 扩展 `create_ad` 加 `call_to_action`**

在 `create_ad` 的 `object_story_spec["link_data"]` 里加：

```python
    if call_to_action_type:
        object_story_spec["link_data"]["call_to_action"] = {
            "type": call_to_action_type,
            "value": {"link": link_url},
        }
```

- [ ] **Step 5: 语法校验**

```bash
python -m py_compile meta_api.py && echo OK
```

- [ ] **Step 6: 提交**

```bash
git add meta_api.py && git commit -m "feat(meta_api): 数据集/受众模版查询 + 广告创建字段扩展"
```

---

### Task 2: database.py 建表与 CRUD

**Files:**
- Modify: `database.py`

**Interfaces:**
- Produces（供 Task 3/4 使用）:
  - `upsert_meta_page(page_id, page_name, bm_id, user_id)`
  - `get_meta_pages(user_id=None) -> List[Dict]`
  - `upsert_meta_pixel(pixel_id, pixel_name, act_id, user_id)`
  - `get_meta_pixels(user_id=None) -> List[Dict]`
  - `upsert_meta_saved_audience(audience_id, name, act_id, targeting_json, user_id)`
  - `get_meta_saved_audiences(user_id=None) -> List[Dict]`
  - `create_delivery_campaign(...) -> int`
  - `get_delivery_campaigns(user_id) -> List[Dict]`
  - `create_delivery_adset(...) -> int`
  - `get_delivery_adsets(campaign_id) -> List[Dict]`
  - `update_delivery_adset_fb_id(adset_id, fb_adset_id)`

- [ ] **Step 1: 在 `init_db()` 建 5 张新表**

在 `database.py` 的 `init_db()`（建表区）追加：

```sql
CREATE TABLE IF NOT EXISTS meta_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT UNIQUE NOT NULL,
    page_name TEXT,
    bm_id TEXT DEFAULT '',
    user_id INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta_pixels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pixel_id TEXT UNIQUE NOT NULL,
    pixel_name TEXT,
    act_id TEXT DEFAULT '',
    user_id INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta_saved_audiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audience_id TEXT UNIQUE NOT NULL,
    audience_name TEXT,
    act_id TEXT DEFAULT '',
    targeting_json TEXT DEFAULT '{}',
    user_id INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS delivery_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    objective TEXT DEFAULT 'OUTCOME_SALES',
    budget_strategy TEXT DEFAULT 'adset',
    is_adset_budget_sharing_enabled INTEGER DEFAULT 0,
    page_id TEXT DEFAULT '',
    link_url TEXT DEFAULT '',
    call_to_action TEXT DEFAULT 'LEARN_MORE',
    status TEXT DEFAULT 'draft',
    fb_campaign_id TEXT DEFAULT '',
    user_id INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS delivery_adsets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    name TEXT,
    ad_account_id TEXT DEFAULT '',
    pixel_id TEXT DEFAULT '',
    audience_id TEXT DEFAULT '',
    daily_budget INTEGER DEFAULT 0,
    bid_strategy TEXT DEFAULT 'LOWEST_COST_WITHOUT_CAP',
    bid_amount INTEGER DEFAULT 0,
    optimization_goal TEXT DEFAULT 'OFFSITE_CONVERSIONS',
    billing_event TEXT DEFAULT 'IMPRESSIONS',
    destination_type TEXT DEFAULT 'WEBSITE',
    custom_event_type TEXT DEFAULT 'PURCHASE',
    attribution_spec_json TEXT DEFAULT '',
    targeting_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'draft',
    fb_adset_id TEXT DEFAULT '',
    user_id INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: 迁移 delivery_queue 加 `adset_id`**

在 `init_db()` 现有迁移区（`ALTER TABLE` 附近，约 762-771 行）追加：

```python
cols = [r["name"] for r in conn.execute("PRAGMA table_info(delivery_queue)").fetchall()]
if "adset_id" not in cols:
    conn.execute("ALTER TABLE delivery_queue ADD COLUMN adset_id INTEGER DEFAULT 0")
```

（若 `init_db()` 里已有通用的 `PRAGMA table_info` 迁移写法，沿用那个写法。）

- [ ] **Step 3: 写 6 个 upsert/get 资产函数**

沿用 `upsert_meta_account`/`get_meta_accounts` 的写法（约 1583-1581 行）：

```python
def upsert_meta_page(page_id, page_name="", bm_id="", user_id=None):
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO meta_pages (page_id, page_name, bm_id, user_id)
            VALUES (?,?,?,?)
            ON CONFLICT(page_id) DO UPDATE SET page_name=excluded.page_name,
                bm_id=excluded.bm_id, user_id=excluded.user_id, updated_at=CURRENT_TIMESTAMP
        """, (page_id, page_name, bm_id, uid))

def get_meta_pages(user_id=None):
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute("SELECT * FROM meta_pages WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM meta_pages ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
```

`upsert_meta_pixel` / `get_meta_pixels` / `upsert_meta_saved_audience` / `get_meta_saved_audiences` 按同模式实现（字段见建表 SQL）。

- [ ] **Step 4: 写 delivery_campaigns / delivery_adsets CRUD**

```python
def create_delivery_campaign(name, objective="OUTCOME_SALES", budget_strategy="adset",
                             is_adset_budget_sharing_enabled=0, page_id="", link_url="",
                             call_to_action="LEARN_MORE", user_id=None):
    uid = user_id or 1
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO delivery_campaigns (name, objective, budget_strategy,
                is_adset_budget_sharing_enabled, page_id, link_url, call_to_action, user_id)
            VALUES (?,?,?,?,?,?,?,?)""",
            (name, objective, budget_strategy, is_adset_budget_sharing_enabled,
             page_id, link_url, call_to_action, uid))
        return cur.lastrowid

def get_delivery_campaigns(user_id=None):
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute("SELECT * FROM delivery_campaigns WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM delivery_campaigns ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

def create_delivery_adset(campaign_id, name, ad_account_id="", pixel_id="", audience_id="",
                          daily_budget=0, bid_strategy="LOWEST_COST_WITHOUT_CAP", bid_amount=0,
                          optimization_goal="OFFSITE_CONVERSIONS", billing_event="IMPRESSIONS",
                          destination_type="WEBSITE", custom_event_type="PURCHASE",
                          attribution_spec_json="", targeting_json="{}", user_id=None):
    uid = user_id or 1
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO delivery_adsets (campaign_id, name, ad_account_id, pixel_id, audience_id,
                daily_budget, bid_strategy, bid_amount, optimization_goal, billing_event,
                destination_type, custom_event_type, attribution_spec_json, targeting_json, user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (campaign_id, name, ad_account_id, pixel_id, audience_id, daily_budget, bid_strategy,
             bid_amount, optimization_goal, billing_event, destination_type, custom_event_type,
             attribution_spec_json, targeting_json, uid))
        return cur.lastrowid

def get_delivery_adsets(campaign_id):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM delivery_adsets WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        return [dict(r) for r in rows]

def update_delivery_adset_fb_id(adset_id, fb_adset_id):
    with get_conn() as conn:
        conn.execute("UPDATE delivery_adsets SET fb_adset_id = ? WHERE id = ?", (fb_adset_id, adset_id))
```

- [ ] **Step 5: 语法校验**

```bash
python -m py_compile database.py && echo OK
```

- [ ] **Step 6: 提交**

```bash
git add database.py && git commit -m "feat(db): 主页/数据集/受众模版表 + 投放系列/广告组表"
```

---

### Task 3: 资产发现/导入路由（main.py）

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `get_meta_pages` / `upsert_meta_page` / `get_meta_pixels` / `upsert_meta_pixel` / `get_meta_saved_audiences` / `upsert_meta_saved_audience`；`meta_api.get_pixels` / `meta_api.get_saved_audiences` / `meta_api.discover_pages`
- Produces: `GET /api/meta/pages`, `POST /api/meta/pages/import`, `GET /api/meta/pixels`, `POST /api/meta/pixels/import`, `GET /api/meta/saved-audiences`, `POST /api/meta/saved-audiences/import`

- [ ] **Step 1: 主页列表 + 导入路由**

```python
@app.get("/api/meta/pages")
def _get_meta_pages(user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    return database.get_meta_pages(uid)

class ImportPagesBody(BaseModel):
    pages: list  # [{"page_id","page_name","bm_id"}]

@app.post("/api/meta/pages/import")
def _import_meta_pages(body: ImportPagesBody, user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    for p in body.pages:
        database.upsert_meta_page(p.get("page_id",""), p.get("page_name",""), p.get("bm_id",""), uid)
    return {"success": True, "count": len(body.pages)}
```

主页发现复用现有 `discover_pages`（已存在于 `meta_api`），前端「发现」直接调现有 discover 接口拿 `/me/accounts` 结果，再走 import。

- [ ] **Step 2: 数据集发现 + 导入路由**

```python
@app.post("/api/meta/pixels/discover")
def _discover_meta_pixels(body: PixelDiscoverBody, user: dict = Depends(get_current_user)):
    pixels, err = meta_api.get_pixels(body.act_id, body.access_token or _load_meta_default_token())
    if err:
        return {"success": False, "message": err}
    return {"success": True, "pixels": pixels}

class ImportPixelsBody(BaseModel):
    pixels: list  # [{"pixel_id","pixel_name","act_id"}]

@app.post("/api/meta/pixels/import")
def _import_meta_pixels(body: ImportPixelsBody, user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    for p in body.pixels:
        database.upsert_meta_pixel(p.get("pixel_id",""), p.get("pixel_name",""), p.get("act_id",""), uid)
    return {"success": True, "count": len(body.pixels)}

@app.get("/api/meta/pixels")
def _get_meta_pixels(user: dict = Depends(get_current_user)):
    return database.get_meta_pixels(_opt_user_id(user))
```

其中 `PixelDiscoverBody(BaseModel)` 含 `act_id: str`、`access_token: str = ""`。

- [ ] **Step 3: 受众模版发现 + 导入路由**

同 Step 2 模式，用 `meta_api.get_saved_audiences`，body `{audience_id, audience_name, act_id, targeting_json}`。

- [ ] **Step 4: 语法校验**

```bash
python -m py_compile main.py && echo OK
```

- [ ] **Step 5: curl 冒烟测（用真实 token）**

```bash
curl -s -H "Authorization: Bearer <session>" http://127.0.0.1:8000/api/meta/pixels
```

期望返回已导入的 pixels 列表。

- [ ] **Step 6: 提交**

```bash
git add main.py && git commit -m "feat(meta): 主页/数据集/受众模版发现导入路由"
```

---

### Task 4: 投放向导后端 + 分层投放（main.py + delivery.py）

**Files:**
- Modify: `delivery.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `get_delivery_campaigns` / `get_delivery_adsets` / `create_delivery_campaign` / `create_delivery_adset` / `update_delivery_adset_fb_id`；`meta_api.create_campaign` / `create_adset` / `create_ad` / `upload_ad_image` / `upload_ad_video`；`database.add_to_delivery_queue`
- Produces: `delivery.resolve_output_path(url, output_root)`；`delivery.submit_delivery_campaign(campaign_id, user_id)`；路由 `POST /api/delivery/campaigns`, `GET /api/delivery/campaigns`, `POST /api/delivery/adsets`, `GET /api/delivery/adsets/{campaign_id}`, `POST /api/delivery/publish`

- [ ] **Step 1: `resolve_output_path`（URL → 本地路径 + 防穿越）**

在 `delivery.py` 顶部追加：

```python
from pathlib import Path

def resolve_output_path(url: str, output_root) -> Optional[str]:
    """把 /static/output/{batch_id}/{name} 转成本地路径并校验存在。
    只允许相对路径在 output_root 内，防路径穿越。"""
    if not url or not url.startswith("/static/output/"):
        return None
    rel = url[len("/static/output/"):]
    p = Path(output_root) / rel
    # resolve 后必须仍在 output_root 内
    try:
        p = p.resolve()
    except Exception:
        return None
    root = Path(output_root).resolve()
    if not str(p).startswith(str(root)) or not p.is_file():
        return None
    return str(p)
```

- [ ] **Step 2: 写 `submit_delivery_campaign`（分层执行）**

替换 `delivery.py` 里 `submit_delivery_batch` 的调用方之前，新增函数：

```python
def submit_delivery_campaign(campaign_id: int, user_id: int = None):
    """按 1 系列 → N 广告组 → 组内 n 广告 分层创建，全部 PAUSED。返回 (batch_id, error)。"""
    batch_id = uuid.uuid4().hex[:12]
    with database.get_conn() as conn:
        camp = conn.execute("SELECT * FROM delivery_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        adsets = conn.execute("SELECT * FROM delivery_adsets WHERE campaign_id = ?", (campaign_id,)).fetchall()
        queue = conn.execute("SELECT * FROM delivery_queue WHERE adset_id IN (SELECT id FROM delivery_adsets WHERE campaign_id = ?)", (campaign_id,)).fetchall()
    if not camp:
        return "", "系列不存在"
    camp = dict(camp)
    adsets = [dict(a) for a in adsets]
    queue = [dict(q) for q in queue]

    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        total = len(queue)
        _push_event(batch_id, "start", {"total": total})
        act_id = adsets[0]["ad_account_id"] if adsets else ""
        token = _get_token(act_id, user_id)
        if not token:
            _push_event(batch_id, "complete", {"completed": 0, "failed": total, "error": "无有效 token"})
            _delivery_events[batch_id].set(); return

        # 1. 建系列
        fb_campaign_id, err = meta_api.create_campaign(
            act_id, token, camp["name"], objective=camp.get("objective","OUTCOME_SALES"),
            status="PAUSED", special_ad_categories=[],
            is_adset_budget_sharing_enabled=(camp.get("is_adset_budget_sharing_enabled") == 1))
        if err:
            _push_event(batch_id, "complete", {"completed": 0, "failed": total, "error": f"建系列失败: {err}"})
            _delivery_events[batch_id].set(); return

        completed = 0; failed = 0
        # 2. 逐广告组
        for adset in adsets:
            targeting = json.loads(adset.get("targeting_json") or "{}")
            attribution = json.loads(adset.get("attribution_spec_json") or "[]") or None
            promoted = {"pixel_id": adset["pixel_id"], "custom_event_type": adset.get("custom_event_type","PURCHASE")} if adset.get("pixel_id") else None
            fb_adset_id, err = meta_api.create_adset(
                act_id, token, adset["name"], fb_campaign_id,
                targeting=targeting, daily_budget=adset.get("daily_budget") or None,
                bid_strategy=adset.get("bid_strategy","LOWEST_COST_WITHOUT_CAP"),
                billing_event=adset.get("billing_event","IMPRESSIONS"),
                optimization_goal=adset.get("optimization_goal","OFFSITE_CONVERSIONS"),
                promoted_object=promoted, destination_type=adset.get("destination_type","WEBSITE"),
                attribution_spec=attribution, bid_amount=adset.get("bid_amount") or None,
                status="PAUSED")
            if err:
                failed += 1
                _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告组失败: {err}"})
                continue
            database.update_delivery_adset_fb_id(adset["id"], fb_adset_id)
            # 3. 组内逐广告
            ad_items = [q for q in queue if q.get("adset_id") == adset["id"]]
            for item in ad_items:
                r = _deliver_one(item, {**adset, "ad_account_id": act_id, "page_id": camp.get("page_id",""), "link_url": camp.get("link_url",""), "call_to_action": camp.get("call_to_action","LEARN_MORE")}, user_id)
                # _deliver_one 内部已把 fb_campaign_id 换成传入值？——见 Step 3 说明
                ...
```

> **说明：** 现有 `_deliver_one` 会自己建 Campaign/AdSet/Ad。分层模式下不应让它再建系列和组。Step 3 重构 `_deliver_one`，或新增 `_deliver_ad_to_adset(item, adset_id, camp, token)` 只做「上传创意 → 建 Ad」两步。

- [ ] **Step 3: 新增 `_deliver_ad_to_adset`（只建 Ad）**

```python
def _deliver_ad_to_adset(queue_item, fb_adset_id, act_id, token, page_id, link_url,
                         call_to_action, user_id=None):
    """只上传创意 + 建 Ad（系列/广告组已由上层建好）。"""
    result = {"queue_id": queue_item["id"], "status": "failed"}
    image_path = queue_item.get("image_path", "")
    is_video = image_path.lower().endswith(".mp4")
    # 上传创意
    if is_video:
        video_id, err = meta_api.upload_ad_video(act_id, token, image_path)
        result["fb_creative_id"] = video_id
        image_hash, video_id = None, video_id
    else:
        image_hash, err = meta_api.upload_ad_image(act_id, token, image_path)
        result["fb_creative_id"] = image_hash
        video_id = None
    if err:
        result["error"] = f"上传创意失败: {err}"
        return result
    ad_id, err = meta_api.create_ad(
        act_id, token, queue_item.get("overlay_text","")[:30] or queue_item["id"],
        fb_adset_id, creative_name=queue_item["id"], page_id=page_id,
        image_hash=image_hash, video_id=video_id,
        message=queue_item.get("overlay_text",""), link_url=link_url,
        call_to_action_type=call_to_action, status="PAUSED")
    if err:
        result["error"] = f"建广告失败: {err}"
        return result
    result["fb_ad_id"] = ad_id
    result["status"] = "delivered"
    return result
```

- [ ] **Step 4: 路由（campaign/adsets CRUD + publish）**

在 `main.py` 的 delivery 路由区（约 4910 行后）追加：

```python
class DeliveryCampaignBody(BaseModel):
    name: str
    objective: str = "OUTCOME_SALES"
    budget_strategy: str = "adset"          # 'adset' | 'campaign'
    is_adset_budget_sharing_enabled: int = 0
    page_id: str = ""
    link_url: str = ""
    call_to_action: str = "LEARN_MORE"

@app.post("/api/delivery/campaigns")
def _create_delivery_campaign(body: DeliveryCampaignBody, user: dict = Depends(get_current_user)):
    cid = database.create_delivery_campaign(body.name, body.objective, body.budget_strategy,
        body.is_adset_budget_sharing_enabled, body.page_id, body.link_url, body.call_to_action, _opt_user_id(user))
    return {"success": True, "id": cid}

@app.get("/api/delivery/campaigns")
def _list_delivery_campaigns(user: dict = Depends(get_current_user)):
    return database.get_delivery_campaigns(_opt_user_id(user))

class DeliveryAdsetBody(BaseModel):
    campaign_id: int
    name: str = ""
    ad_account_id: str = ""
    pixel_id: str = ""
    audience_id: str = ""
    daily_budget: int = 0
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    bid_amount: int = 0
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    billing_event: str = "IMPRESSIONS"
    destination_type: str = "WEBSITE"
    custom_event_type: str = "PURCHASE"
    attribution_spec_json: str = ""
    targeting_json: str = "{}"

@app.post("/api/delivery/adsets")
def _create_delivery_adset(body: DeliveryAdsetBody, user: dict = Depends(get_current_user)):
    aid = database.create_delivery_adset(body.campaign_id, body.name, body.ad_account_id,
        body.pixel_id, body.audience_id, body.daily_budget, body.bid_strategy, body.bid_amount,
        body.optimization_goal, body.billing_event, body.destination_type, body.custom_event_type,
        body.attribution_spec_json, body.targeting_json, _opt_user_id(user))
    return {"success": True, "id": aid}

@app.get("/api/delivery/adsets/{campaign_id}")
def _list_delivery_adsets(campaign_id: int, user: dict = Depends(get_current_user)):
    return database.get_delivery_adsets(campaign_id)

@app.post("/api/delivery/publish")
def _publish_delivery_campaign(body: PublishBody, user: dict = Depends(get_current_user)):
    batch_id, err = delivery.submit_delivery_campaign(body.campaign_id, _opt_user_id(user))
    if err:
        return {"success": False, "message": err}
    return {"success": True, "batch_id": batch_id}
```

其中 `PublishBody(BaseModel)` 含 `campaign_id: int`。素材进 `delivery_queue` 时带 `adset_id` 与本地路径（`image_path` 用 `delivery.resolve_output_path` 转换）。

- [ ] **Step 5: 语法校验**

```bash
python -m py_compile main.py delivery.py && echo OK
```

- [ ] **Step 6: 冒烟测**

用 admin session 建一个 series + 一个 adset + 一条 queue（`image_path` 用现有 `D:\每日小说\100\100-1.png`），调 `/api/delivery/publish`，观察 SSE `progress` 到 `complete`，数据库 `delivery_queue.fb_ad_id` 落值。

- [ ] **Step 7: 提交**

```bash
git add delivery.py main.py && git commit -m "feat(delivery): 1-N-N 分层投放 + 发布端点"
```

---

### Task 5: 前端投放向导（static/index.html）

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `GET /api/meta/accounts`、`GET /api/meta/pages`、`GET /api/meta/pixels`、`GET /api/meta/saved-audiences`、`POST /api/delivery/campaigns`、`POST /api/delivery/adsets`、`POST /api/delivery/queue`、`POST /api/delivery/publish`、`GET /api/delivery/stream/{batch_id}`

- [ ] **Step 1: 加导航 + tab 面板**

在 `switchTab` 的 tabs 数组（约 1926 行）加 `delivery-wizard`；加 nav 按钮 `id="nav-delivery-wizard"`（标签「投放向导」）；加 `tab-delivery-wizard` 面板（三列步骤布局：系列 → 广告组 → 广告）。

- [ ] **Step 2: 写向导 JS（三个函数，走现有 fetch 模式）**

```javascript
var _dwCampaignId = null;
var _dwAdsCount = 0;   // 每个广告组要放的广告数

function dwCreateCampaign() {
  // 读系列表单：name/objective/budget_strategy/page_id(主页下拉)/link_url/call_to_action
  // POST /api/delivery/campaigns → 存 _dwCampaignId
}

function dwAddAdset() {
  // 读广告组表单：name/ad_account_id(账户下拉)/pixel_id(数据集下拉)/
  //   audience_id(受众模版下拉)/daily_budget/optimization_goal/attribution_spec
  // POST /api/delivery/adsets → 追加到广告组列表
}

function dwPublish(assetUrls) {
  // assetUrls: 生产中心/小说分析传入的素材 URL 数组
  // 按广告组数均分素材，逐条 POST /api/delivery/queue {image_path(已由后端解析或前端传URL), adset_id, overlay_text}
  // 然后 POST /api/delivery/publish {campaign_id}
  // 打开 SSE /api/delivery/stream/{batch_id} 渲染进度
}
```

下拉数据源：主页 `GET /api/meta/pages`；数据集 `GET /api/meta/pixels`；受众模版 `GET /api/meta/saved-audiences`；账户 `GET /api/meta/accounts`。

> **说明：** `image_path` 传 URL 还是本地路径？为安全，`/api/delivery/queue` 应调用 `delivery.resolve_output_path(url)` 把前端传来的 `/static/output/...` URL 转成本地路径再入库。若现有 `add_to_delivery_queue` 不做转换，则在 Task 4 的 publish 端点或 queue 端点里加转换。

- [ ] **Step 3: 发布进度渲染**

复用现有 SSE 模式（参照 `startSSEStream` 约 3343 行），渲染 `start/progress/complete` 事件，完成后列出各层 fb_*_id。

- [ ] **Step 4: 前端手动验证**

启动服务，走一遍：建系列 → 建 2 个广告组 → 从生产中心选 4 张图 → 均分到 2 组 → 发布 → 看进度 → Meta 后台确认 PAUSED 草稿。

- [ ] **Step 5: 提交**

```bash
git add static/index.html && git commit -m "feat(frontend): 投放向导三步配置 + 发布"
```

---

### Task 6: 生产中心/小说分析 发布入口

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: `dwPublish(assetUrls)`（Task 5）；素材 URL 从生产中心 `imageGallery`/`videoList` 和小说分析 `naGeneratedImages` 收集

- [ ] **Step 1: 生产中心加「发布到 Meta」按钮**

在 `imageGallery`/`videoList` 容器上方加按钮 `id="genPublishBtn"`，点击收集当前批次所有图/视频 URL，`switchTab('delivery-wizard')` 并把 URL 传给向导（存 `window._dwAssets`），向导广告步展示勾选。

- [ ] **Step 2: 小说分析加「发布到 Meta」按钮**

`naGeneratedImages` 上方加 `id="naPublishBtn"`，同上，收集 `naGeneratedImages` 里所有 `img` 的 `data-url`，跳向导。

- [ ] **Step 3: 手动验证两个入口**

生成一批 → 点发布 → 向导里能勾选素材 → 走完发布。

- [ ] **Step 4: 提交**

```bash
git add static/index.html && git commit -m "feat(frontend): 生产中心/小说分析接入投放向导入口"
```

---

## Self-Review

- **Spec 覆盖：** 资产三表(Task2/3) ✓；三层字段映射(Task1/4) ✓；1-N-N 分层(Task4) ✓；URL→路径(Task4 Step1) ✓；投放向导(Task5) ✓；生产/分析入口(Task6) ✓；待核实项(Task0) ✓。
- **类型一致：** `resolve_output_path`、`submit_delivery_campaign`、`_deliver_ad_to_adset` 在 Task4 定义并在 Task5 前端调用契约一致；DB 函数名 Task2 定义、Task3/4 使用一致。
- **占位符：** 无 TBD；Task0 的 4 个字段在 Step 里给了具体待测命令。
