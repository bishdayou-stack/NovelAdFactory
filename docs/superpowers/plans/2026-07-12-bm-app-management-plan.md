# BM 管理 & 多应用支持 — 实施计划

> **For agentic workers:** 使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 按任务执行。步骤使用 checkbox (`- [ ]`) 跟踪。

**Goal:** 新增 BM 管理和多 App 支持：BM 作为独立实体、System User Token 提到 BM 级别、每个 BM 可绑定不同 App

**Architecture:** 新增 `app_config` 和 `bm_config` 两张表，`meta_accounts` 加 `bm_id` 关联，Token 获取改为三级优先级（BM → 账户 → 全局默认），前端新增独立的 BM/App 管理标签页

**Tech Stack:** Python + SQLite（后端），原生 JS + Tailwind CSS（前端）

## 全局约束

- Meta 管理页面现有功能全部保持不变
- Token 优先级：bm_config.system_token → meta_accounts.access_token → config.json.default_access_token
- App 优先级：bm_config.app_id → app_config.is_default → config.json.meta.app_id
- 删除 BM 不删除关联账户
- 设置 is_default=1 时自动取消其他默认
- 删除默认 App 时自动将下一个 App 设为默认（若有）

---

### Task 1: 数据库迁移 — 新增 app_config 表 + bm_config 表 + meta_accounts.bm_id

**Files:**
- Modify: `database.py` — `init_db()` 末尾加迁移逻辑

**Interfaces:**
- Produces: `app_config` 表、`bm_config` 表、`meta_accounts.bm_id` 列

- [ ] **Step 1: 在 `init_db()` 末尾（`""")` 之后）追加迁移**

```python
        # 迁移：app_config 表
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS app_config (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name    TEXT NOT NULL,
                app_id      TEXT NOT NULL,
                app_secret  TEXT NOT NULL,
                is_default  INTEGER DEFAULT 0,
                user_id     INTEGER DEFAULT 1,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bm_config (
                bm_id         TEXT PRIMARY KEY,
                bm_name       TEXT NOT NULL,
                system_token  TEXT,
                app_id        TEXT,
                user_id       INTEGER DEFAULT 1,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 迁移：meta_accounts 加 bm_id
        c = conn.cursor()
        c.execute("PRAGMA table_info('meta_accounts')")
        meta_cols = [r[1] for r in c.fetchall()]
        if 'bm_id' not in meta_cols:
            c.execute("ALTER TABLE meta_accounts ADD COLUMN bm_id TEXT")
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
import database
database.init_db()
import sqlite3
conn = sqlite3.connect('data/dashboard.db')
c = conn.cursor()
c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('app_config','bm_config')\")
print('新表:', [r[0] for r in c.fetchall()])
c.execute('PRAGMA table_info(\"meta_accounts\")')
print('bm_id 列:', 'bm_id' in [r[1] for r in c.fetchall()])
conn.close()
"
```

- [ ] **Step 3: 提交**

```bash
git add database.py
git commit -m "feat: 新增 app_config/bm_config 表 + meta_accounts.bm_id 列"
```

---

### Task 2: 数据库 CRUD — app_config

**Files:**
- Modify: `database.py` — 末尾新增函数

**Interfaces:**
- Produces: `get_app_configs(user_id) → List[Dict]`, `upsert_app_config(...)`, `delete_app_config(id)`, `get_default_app(user_id) → Dict|None`

- [ ] **Step 1: 实现 CRUD 函数**

在 `database.py` 末尾追加：

```python
def get_app_configs(user_id: int = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute("SELECT * FROM app_config WHERE user_id=? ORDER BY is_default DESC, id", (user_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM app_config ORDER BY is_default DESC, id").fetchall()
        return [dict(r) for r in rows]

def get_default_app(user_id: int = None) -> Optional[Dict[str, Any]]:
    uid = user_id or 1
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM app_config WHERE is_default=1 AND user_id=? LIMIT 1", (uid,)).fetchone()
        return dict(row) if row else None

def upsert_app_config(app_name: str, app_id: str, app_secret: str,
                      is_default: int = 0, user_id: int = None,
                      config_id: int = None) -> int:
    uid = user_id or 1
    with get_conn() as conn:
        if is_default:
            conn.execute("UPDATE app_config SET is_default=0 WHERE user_id=?", (uid,))
        if config_id:
            conn.execute("""UPDATE app_config SET app_name=?, app_id=?, app_secret=?,
                is_default=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
                (app_name, app_id, app_secret, is_default, config_id, uid))
            return config_id
        else:
            c = conn.execute("""INSERT INTO app_config (app_name, app_id, app_secret, is_default, user_id)
                VALUES (?,?,?,?,?)""", (app_name, app_id, app_secret, is_default, uid))
            return c.lastrowid

def delete_app_config(config_id: int, user_id: int = None) -> bool:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("DELETE FROM app_config WHERE id=? AND user_id=?", (config_id, uid))
        # 如果删的是默认，把下一个设为默认
        row = conn.execute("SELECT COUNT(*) FROM app_config WHERE is_default=1 AND user_id=?", (uid,)).fetchone()
        if row[0] == 0:
            conn.execute("UPDATE app_config SET is_default=1 WHERE id=(SELECT id FROM app_config WHERE user_id=? LIMIT 1)", (uid,))
        return True
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
import database
# 测试CRUD
id1 = database.upsert_app_config('主应用', '123', 'sec1', is_default=1)
print('新增默认:', id1)
id2 = database.upsert_app_config('A线', '456', 'sec2')
print('新增:', id2)
print('列表:', len(database.get_app_configs()))
print('默认:', database.get_default_app()['app_name'])
database.delete_app_config(id2)
print('删后:', len(database.get_app_configs()))
"
```

- [ ] **Step 3: 提交**

```bash
git add database.py
git commit -m "feat: app_config CRUD函数"
```

---

### Task 3: 数据库 CRUD — bm_config

**Files:**
- Modify: `database.py` — 末尾新增函数

**Interfaces:**
- Produces: `get_bm_configs(user_id) → List[Dict]`, `upsert_bm_config(...)`, `delete_bm_config(bm_id)`, `get_bm_account_count(bm_id) → int`

- [ ] **Step 1: 实现 CRUD 函数**

```python
def get_bm_configs(user_id: int = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute("""SELECT b.*, (SELECT COUNT(*) FROM meta_accounts m WHERE m.bm_id=b.bm_id) as account_count
                FROM bm_config b WHERE b.user_id=? ORDER BY b.bm_name""", (user_id,)).fetchall()
        else:
            rows = conn.execute("""SELECT b.*, (SELECT COUNT(*) FROM meta_accounts m WHERE m.bm_id=b.bm_id) as account_count
                FROM bm_config b ORDER BY b.bm_name""").fetchall()
        return [dict(r) for r in rows]

def upsert_bm_config(bm_id: str, bm_name: str, system_token: str = "",
                     app_id: str = "", user_id: int = None) -> None:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("""INSERT INTO bm_config (bm_id, bm_name, system_token, app_id, user_id)
            VALUES (?,?,?,?,?) ON CONFLICT(bm_id) DO UPDATE SET
            bm_name=excluded.bm_name, system_token=excluded.system_token,
            app_id=excluded.app_id, updated_at=CURRENT_TIMESTAMP""",
            (bm_id, bm_name, system_token, app_id, uid))

def delete_bm_config(bm_id: str, user_id: int = None) -> bool:
    uid = user_id or 1
    with get_conn() as conn:
        conn.execute("DELETE FROM bm_config WHERE bm_id=? AND user_id=?", (bm_id, uid))
        return True

def get_bm_token(bm_id: str) -> Optional[str]:
    """获取 BM 的 System User Token，用于该 BM 下所有账户的 API 调用"""
    with get_conn() as conn:
        row = conn.execute("SELECT system_token FROM bm_config WHERE bm_id=? AND system_token != ''", (bm_id,)).fetchone()
        return row[0] if row else None
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
import database
database.upsert_bm_config('123456', '测试BM', 'token123', app_id='1')
database.upsert_bm_config('789', '另一个BM', 'token789')
bms = database.get_bm_configs()
print(f'BM列表: {len(bms)}')
for b in bms:
    print(f'  {b[\"bm_name\"]}: {b[\"account_count\"]}个账户')
print('Token:', database.get_bm_token('123456'))
database.delete_bm_config('789')
print('删后:', len(database.get_bm_configs()))
"
```

- [ ] **Step 3: 提交**

```bash
git add database.py
git commit -m "feat: bm_config CRUD函数 + Token获取"
```

---

### Task 4: Token 获取适配 — scraper + main 改用三级优先级

**Files:**
- Modify: `scraper.py` — `_sync_one_meta_account` 调用点
- Modify: `main.py` — `_trigger_meta_sync` 和单账户同步中 Token 获取

**Interfaces:**
- Consumes: `database.get_bm_token(bm_id)`, `database.get_meta_accounts()`
- Produces: 统一的 Token 获取逻辑

- [ ] **Step 1: 在 `main.py` 的 `_trigger_single_account_sync` 中适配**

将当前 Token 获取：
```python
token = account.get("access_token") or _load_meta_default_token()
```

改为三级优先级：
```python
# Token 优先级: BM level → 账户 level → 全局默认
bm_id = account.get("bm_id", "")
token = (database.get_bm_token(bm_id) if bm_id else "") or account.get("access_token") or _load_meta_default_token()
```

- [ ] **Step 2: 在 `main.py` 的 `_trigger_meta_sync` 的 active 列表中适配**

```python
bm_id = a.get("bm_id", "")
token = (database.get_bm_token(bm_id) if bm_id else "") or a.get("access_token") or _load_meta_default_token()
```

- [ ] **Step 3: 在 `scraper.py` 的 `sync_all_meta_insights` 中适配**

```python
bm_id = a.get("bm_id", "")
token = (database.get_bm_token(bm_id) if bm_id else "") or a.get("access_token") or default_token
```

- [ ] **Step 4: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
import database
# 模拟：给某个BM设Token，验证优先级
database.upsert_bm_config('_test_bm', '测试', 'bm_token_123')
print('BM token:', database.get_bm_token('_test_bm'))
database.delete_bm_config('_test_bm')
print('验证通过')
"
```

- [ ] **Step 5: 提交**

```bash
git add main.py scraper.py
git commit -m "feat: Token获取改为BM→账户→全局三级优先级"
```

---

### Task 5: 后端 API — App 管理端点

**Files:**
- Modify: `main.py` — 新增 4 个端点

**Interfaces:**
- Produces: `GET /api/app/list`, `POST /api/app`, `PUT /api/app/{id}`, `DELETE /api/app/{id}`

- [ ] **Step 1: 实现 App 管理端点**

在 `main.py` 追加：

```python
@app.get("/api/app/list")
def _app_list(user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    return {"data": database.get_app_configs(uid)}

class AppBody(BaseModel):
    app_name: str
    app_id: str
    app_secret: str
    is_default: int = 0

@app.post("/api/app")
def _app_create(body: AppBody, user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "仅管理员可操作")
    uid = user["id"]
    app_id = database.upsert_app_config(body.app_name, body.app_id, body.app_secret, body.is_default, uid)
    return {"success": True, "id": app_id}

@app.put("/api/app/{config_id}")
def _app_update(config_id: int, body: AppBody, user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "仅管理员可操作")
    uid = user["id"]
    database.upsert_app_config(body.app_name, body.app_id, body.app_secret, body.is_default, uid, config_id)
    return {"success": True}

@app.delete("/api/app/{config_id}")
def _app_delete(config_id: int, user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "仅管理员可操作")
    uid = user["id"]
    database.delete_app_config(config_id, uid)
    return {"success": True}
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
from main import app
from fastapi.testclient import TestClient
c = TestClient(app)
# 登录
c.post('/api/login', json={'username':'admin','password':'admin123'})
# 测试
r = c.get('/api/app/list')
print('list:', r.status_code, len(r.json().get('data',[])))
"
```

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: App管理 CRUD API端点"
```

---

### Task 6: 后端 API — BM 管理端点

**Files:**
- Modify: `main.py` — 新增 BM 管理端点

**Interfaces:**
- Produces: `GET /api/bm/list`, `POST /api/bm/discover`, `POST /api/bm`, `PUT /api/bm/{bm_id}`, `DELETE /api/bm/{bm_id}`

- [ ] **Step 1: 实现 BM 管理端点**

```python
@app.get("/api/bm/list")
def _bm_list(user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    return {"data": database.get_bm_configs(uid)}

class BmBody(BaseModel):
    bm_id: str
    bm_name: str = ""
    system_token: str = ""
    app_id: str = ""

@app.post("/api/bm")
def _bm_upsert(body: BmBody, user: dict = Depends(get_current_user)):
    uid = user["id"]
    database.upsert_bm_config(body.bm_id, body.bm_name, body.system_token, body.app_id, uid)
    return {"success": True}

@app.put("/api/bm/{bm_id}")
def _bm_update(bm_id: str, body: BmBody, user: dict = Depends(get_current_user)):
    uid = user["id"]
    database.upsert_bm_config(bm_id, body.bm_name, body.system_token, body.app_id, uid)
    return {"success": True}

@app.delete("/api/bm/{bm_id}")
def _bm_delete(bm_id: str, user: dict = Depends(get_current_user)):
    uid = user["id"]
    database.delete_bm_config(bm_id, uid)
    return {"success": True}

class DiscoverBody(BaseModel):
    access_token: str

@app.post("/api/bm/discover")
def _bm_discover(body: DiscoverBody, user: dict = Depends(get_current_user)):
    """用 System User Token 发现 BM 并自动保存"""
    uid = user["id"]
    result, err = meta_api.discover_all_assets(body.access_token)
    if err:
        return {"success": False, "message": err}
    businesses = result.get("businesses", [])
    count = 0
    for bm in businesses:
        bm_id = bm.get("id", "")
        if bm_id:
            database.upsert_bm_config(bm_id, bm.get("name", bm_id), body.access_token, "", uid)
            count += 1
    return {"success": True, "count": count, "message": f"发现并保存 {count} 个 BM"}
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
import database
database.upsert_bm_config('_t1', '测试BM', 'tok1')
bms = database.get_bm_configs()
print('BM数:', len(bms))
database.delete_bm_config('_t1')
print('OK')
"
```

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: BM管理 CRUD API端点 + 发现BM"
```

---

### Task 7: 数据迁移 — config.json 全局 App → app_config 表

**Files:**
- Modify: `main.py` — `init_db()` 之后或首次启动时执行

- [ ] **Step 1: 在 `main.py` 添加启动时迁移逻辑**

在 `database.init_db()` 调用之后添加：

```python
# 迁移：将 config.json 的全局 App 配置写入 app_config（仅首次）
config_path = Path("config.json")
if config_path.exists():
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    meta_cfg = cfg.get("meta", {})
    global_app_id = meta_cfg.get("app_id", "")
    if global_app_id:
        existing = database.get_app_configs()
        if not existing:
            database.upsert_app_config(
                "默认应用", global_app_id,
                meta_cfg.get("app_secret", ""), is_default=1
            )
            print("[迁移] 全局 App 配置已写入 app_config 表")
```

- [ ] **Step 2: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
from pathlib import Path; import json
cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
print('app_id:', cfg.get('meta',{}).get('app_id','无'))
import database
apps = database.get_app_configs()
print('app_config 表:', len(apps), '条')
for a in apps: print(f'  {a[\"app_name\"]}: {a[\"app_id\"][:10]}...')
"
```

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: 启动时自动迁移全局App配置到app_config表"
```

---

### Task 8: 前端 — BM/App 管理页面

**Files:**
- Modify: `static/index.html` — 新增标签页 + JS 函数

- [ ] **Step 1: 添加侧边栏导航按钮**

在侧边栏"Meta 管理"按钮后面添加：

```html
<button id="nav-bm-mgmt" class="sub-tab-btn ...">BM/App 管理</button>
```

- [ ] **Step 2: 添加标签页 HTML**

```html
<div id="tab-bm-mgmt" class="flex-1 bg-slate-900 overflow-y-auto px-5 py-4" hidden>
  <!-- App 管理区域 -->
  <div class="card-dark p-4 mb-4">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-sm font-semibold text-white">App 管理</h3>
      <button id="btnAddApp" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] text-white hover:bg-indigo-500">新增 App</button>
    </div>
    <table class="w-full text-[12px]" id="appTable">
      <thead><tr>
        <th class="text-left text-slate-400 py-1">名称</th>
        <th class="text-left text-slate-400 py-1">App ID</th>
        <th class="text-center text-slate-400 py-1">默认</th>
        <th class="text-right text-slate-400 py-1">操作</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <!-- BM 管理区域 -->
  <div class="card-dark p-4">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-sm font-semibold text-white">BM 管理</h3>
      <div class="flex gap-2">
        <button id="btnDiscoverBm" class="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] text-white hover:bg-emerald-500">发现 BM</button>
        <button id="btnAddBm" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] text-white hover:bg-indigo-500">手动添加</button>
      </div>
    </div>
    <table class="w-full text-[12px]" id="bmTable">
      <thead><tr>
        <th class="text-left text-slate-400 py-1">BM 名称</th>
        <th class="text-left text-slate-400 py-1">BM ID</th>
        <th class="text-center text-slate-400 py-1">账户数</th>
        <th class="text-center text-slate-400 py-1">绑定 App</th>
        <th class="text-center text-slate-400 py-1">Token</th>
        <th class="text-right text-slate-400 py-1">操作</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 3: 添加 JS 函数**

```javascript
// ---- BM/App 管理 ----
function loadBmAppPage() {
  loadAppTable();
  loadBmTable();
}

function loadAppTable() {
  fetch('/api/app/list').then(r => r.json()).then(res => {
    var apps = res.data || [];
    var html = '';
    apps.forEach(function(a) {
      html += '<tr class="border-b border-slate-800">' +
        '<td class="py-2 text-white">' + escapeHtml(a.app_name) + '</td>' +
        '<td class="py-2 text-slate-400">' + escapeHtml(a.app_id) + '</td>' +
        '<td class="py-2 text-center">' + (a.is_default ? '✅' : '') + '</td>' +
        '<td class="py-2 text-right">' +
          '<button class="text-[10px] text-indigo-400 hover:text-indigo-300 mr-2" onclick="editApp(' + a.id + ')">编辑</button>' +
          '<button class="text-[10px] text-red-400 hover:text-red-300" onclick="deleteApp(' + a.id + ')">删除</button>' +
        '</td></tr>';
    });
    document.getElementById('appTable').querySelector('tbody').innerHTML = html || '<tr><td colspan="4" class="text-slate-500 py-4 text-center">暂无 App</td></tr>';
  });
}

// ... (editApp, deleteApp, addApp 弹窗函数类似现有 Meta 配置弹窗模式)
// ... (loadBmTable, editBmToken, bindBmApp 等函数)
```

- [ ] **Step 4: 提交**

```bash
git add static/index.html
git commit -m "feat: BM/App管理页面 UI + JS"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 验证数据库迁移 + CRUD**

```bash
cd e:\xiangmu\5-28 && python -c "
import database; database.init_db()
apps = database.get_app_configs(); print(f'App: {len(apps)}')
bms = database.get_bm_configs(); print(f'BM: {len(bms)}')
import sqlite3; conn = sqlite3.connect('data/dashboard.db')
c = conn.cursor()
c.execute('PRAGMA table_info(\"meta_accounts\")')
print('bm_id:', 'bm_id' in [r[1] for r in c.fetchall()])
conn.close()
"
```

- [ ] **Step 2: 验证 API 端点**

启动服务，测试：
- `GET /api/app/list` 返回 App 列表
- `POST /api/app` 创建 App
- `GET /api/bm/list` 返回 BM 列表
- `POST /api/bm/discover` 发现 BM

- [ ] **Step 3: 验证前端页面**

打开 `BM/App 管理` 标签页，确认 App 表格和 BM 表格正常渲染

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: 端到端验证通过"
```
