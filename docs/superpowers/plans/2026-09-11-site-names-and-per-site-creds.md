# 站点改名 + 每站凭据 + 管理页每站登录状态 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个用户能自定义两个书城的显示名；让两个书城可以各用一套登录凭据（未单独设置时回落到共用那套）；管理页能看到每个站点各自的登录状态。

**Architecture:** 显示名存每用户的 `user_config`（已有 KV 表）；每站凭据存新表 `user_site_credentials`，解析顺序为「该站专属 → 通用（`users.pingykj_*`）」；管理页状态由内存会话（键为 `(user_id, site)`）逐站计算。

**Tech Stack:** Python 3 + FastAPI + SQLite（无 ORM，`database.py` contextmanager 连接）+ 原生 JS 单文件前端（`static/index.html`）。

**Spec:** 无独立 spec —— 设计在对话中确认（含两个决策：**站点名每用户各自改**、**支持每站各自凭据**）。

## Global Constraints

- 站点 `key`（`'a'`/`'b'`）是**数据分区标识，不可变**；只改**显示名**。
- 显示名解析顺序：**用户改的名 → `config.json` 的 `meta.pingykj_sites[].name` → 站点 key**。
- 凭据解析顺序：**`user_site_credentials(user_id, site)` → `users.pingykj_username/password_encrypted` → 无**。
  **现有用户不迁移**（新表纯新增，未配置的站点行为完全不变）。
- 密码沿用既有 `database.encrypt_pingykj_password` / `decrypt_pingykj_password`，**不得新增明文存储**。
- 管理页登录状态取自**内存会话是否有效**（与现状同源）；重启后显示"未登录"，下次同步自动恢复。
- 本仓库**没有单元测试框架**，验证用可运行的 assert 脚本（放 `scripts/`），沿用既有约定。
- 本功能**不改** `config.json` 里的 `base_url` / `content_url`，也不改任何已有数据的 `site` 值。

---

### Task 1: 每站凭据的数据层

**Files:**
- Modify: `database.py`（`init_db` 的基础 schema `executescript` 与"确保表存在"段；文件末尾附近的用户凭据函数区）
- Create: `scripts/verify_site_creds.py`

**Interfaces:**
- Consumes: 既有 `encrypt_pingykj_password` / `decrypt_pingykj_password` / `get_user_pingykj_credentials`（`database.py`）
- Produces:
  - `database.set_site_credentials(user_id: int, site: str, username: str, password: str) -> None`
  - `database.get_site_credentials(user_id: int, site: str) -> Optional[Dict[str, str]]`（`{"username","password"}`，解密后）
  - `database.delete_site_credentials(user_id: int, site: str) -> None`
  - `database.get_effective_pingykj_credentials(user_id: int, site: str = None) -> Optional[Dict[str, str]]`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_site_creds.py`：

```python
# -*- coding: utf-8 -*-
"""验证每站凭据：专属优先、回落通用、删除后回落、站点隔离。"""
import shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))
import database


def main():
    tmp = Path(tempfile.mkdtemp(prefix="site_creds_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()

    # 造两个用户（沿用库默认管理员 id=1，另建 id=2 便于隔离验证）
    uid = database.create_user("u1", "p1", "user", pingykj_username="shared_user",
                               pingykj_password="shared_pw")
    uid2 = database.create_user("u2", "p1", "user", pingykj_username="other",
                                pingykj_password="other_pw")

    # 未配置每站凭据 → 回落到通用（users 表那套）
    eff_a = database.get_effective_pingykj_credentials(uid, "a")
    assert eff_a == {"username": "shared_user", "password": "shared_pw"}, eff_a

    # 给 B 站单独配一套 → B 站用专属，A 站仍回落通用
    database.set_site_credentials(uid, "b", "b_user", "b_pw")
    eff_b = database.get_effective_pingykj_credentials(uid, "b")
    assert eff_b == {"username": "b_user", "password": "b_pw"}, eff_b
    assert database.get_effective_pingykj_credentials(uid, "a")["username"] == "shared_user"
    assert database.get_site_credentials(uid, "a") is None, "A 站不该有专属凭据"

    # 站点隔离：另一个用户不受影响
    assert database.get_effective_pingykj_credentials(uid2, "b")["username"] == "other"

    # 重复设置同一站 → 覆盖而非新增
    database.set_site_credentials(uid, "b", "b_user2", "b_pw2")
    assert database.get_effective_pingykj_credentials(uid, "b")["username"] == "b_user2"

    # 删除该站专属 → 回落通用
    database.delete_site_credentials(uid, "b")
    assert database.get_site_credentials(uid, "b") is None
    assert database.get_effective_pingykj_credentials(uid, "b")["username"] == "shared_user"

    # 密码是加密存储的（不得明文落库）
    import sqlite3
    c = sqlite3.connect(str(tmp))
    database.set_site_credentials(uid, "b", "b_user", "b_pw")
    raw = c.execute("SELECT password_encrypted FROM user_site_credentials "
                    "WHERE user_id=? AND site='b'", (uid,)).fetchone()[0]
    assert raw and "b_pw" not in raw, f"密码疑似明文落库: {raw}"
    c.close()

    # site 为空 → 回落通用
    assert database.get_effective_pingykj_credentials(uid, "")["username"] == "shared_user"

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 每站凭据——专属优先 / 回落通用 / 删除后回落 / 站点隔离 / 加密存储")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_site_creds.py`
Expected: FAIL —— `AttributeError: module 'database' has no attribute 'set_site_credentials'`

- [ ] **Step 3: 建表**

在 `database.py` 的 `init_db()` 基础 schema（`conn.executescript("""...""")`，与 `users` / `user_config` 同一段）里加：

```sql
CREATE TABLE IF NOT EXISTS user_site_credentials (
    user_id INTEGER NOT NULL,
    site TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    password_encrypted TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, site),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

同时在"确保 user_config 表存在（幂等）"那段（`database.py` 约 `:589`）后面，照样加一段幂等的
`CREATE TABLE IF NOT EXISTS user_site_credentials (...)`（同上 DDL），保证老库启动即建表。

- [ ] **Step 4: 实现四个函数**

加在 `get_user_pingykj_credentials` 之后：

```python
def set_site_credentials(user_id: int, site: str, username: str, password: str) -> None:
    """为某用户某站点设置专属书城凭据（密码加密存储）。"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO user_site_credentials (user_id, site, username, password_encrypted, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, site) DO UPDATE SET
                username = excluded.username,
                password_encrypted = excluded.password_encrypted,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, site, username, encrypt_pingykj_password(password) if password else ""))


def get_site_credentials(user_id: int, site: str) -> Optional[Dict[str, str]]:
    """取某用户某站点的专属凭据（解密后）；未配置返回 None。"""
    if not site:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT username, password_encrypted FROM user_site_credentials "
            "WHERE user_id = ? AND site = ?", (user_id, site)
        ).fetchone()
    if not row or not row["username"]:
        return None
    return {"username": row["username"],
            "password": decrypt_pingykj_password(row["password_encrypted"])}


def delete_site_credentials(user_id: int, site: str) -> None:
    """删除某用户某站点的专属凭据（之后该站回落到通用凭据）。"""
    if not site:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM user_site_credentials WHERE user_id = ? AND site = ?",
                     (user_id, site))


def get_effective_pingykj_credentials(user_id: int, site: str = None) -> Optional[Dict[str, str]]:
    """该用户在某站点实际使用的书城凭据：站点专属优先，回落到通用（users 表那套）。"""
    return get_site_credentials(user_id, site or "") or get_user_pingykj_credentials(user_id)
```

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_site_creds.py`
Expected: `OK: 每站凭据——专属优先 / 回落通用 / 删除后回落 / 站点隔离 / 加密存储`

- [ ] **Step 6: 确认老库启动能建表**

Run: `python -c "import database; database.init_db(); print('ok')"`
Expected: `ok`（真实库 `data/dashboard.db` 自动多出 `user_site_credentials` 空表，不动任何已有数据）

- [ ] **Step 7: Commit**

```bash
git add database.py scripts/verify_site_creds.py
git commit -m "feat(site): 每站书城凭据——专属优先、回落通用，密文存储"
```

---

### Task 2: 同步会话按站点取凭据

**Files:**
- Modify: `scraper.py`（`_get_or_create_session`）
- Modify: `scripts/verify_scraper_site.py`（补断言）

**Interfaces:**
- Consumes: `database.get_effective_pingykj_credentials(user_id, site)`（Task 1）
- Produces: `_get_or_create_session(user_id, site)` 在自动登录时使用**该站点实际生效的凭据**

- [ ] **Step 1: 补断言（先失败）**

在 `scripts/verify_scraper_site.py` 的 `main()` 末尾（`print` 之前）加：

```python
    # 自动登录应使用「该站点生效的凭据」而不是通用凭据
    import database, tempfile, shutil
    from pathlib import Path as _P
    tmp = _P(tempfile.mkdtemp(prefix="scraper_creds_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()
    uid = database.create_user("u1", "p1", "user",
                               pingykj_username="shared", pingykj_password="shared_pw")
    database.set_site_credentials(uid, "b", "b_user", "b_pw")
    assert database.get_effective_pingykj_credentials(uid, "b")["username"] == "b_user"
    assert database.get_effective_pingykj_credentials(uid, "a")["username"] == "shared"
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 站点凭据解析——B 站专属、A 站回落通用")
```

- [ ] **Step 2: 确认现状**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_scraper_site.py`
Expected: 该断言在 Task 1 完成后**即可通过**（它验的是 Task 1 的解析函数）。
本步真正要改的是 Step 3 —— 让 `_get_or_create_session` 用它。

- [ ] **Step 3: 改 `_get_or_create_session`**

`scraper.py` 中把：

```python
    creds = database.get_user_pingykj_credentials(user_id)
```
改为：

```python
    creds = database.get_effective_pingykj_credentials(user_id, site)
```

（`site` 在该函数开头已归一为 `(site or DEFAULT_SITE).strip() or DEFAULT_SITE`，直接用。）

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_scraper_site.py`
Expected: `OK: 会话按 (user, site) 分开，URL 按站点取` + `OK: 站点凭据解析——B 站专属、A 站回落通用`

- [ ] **Step 5: 确认无其他调用点漏改**

Run: `grep -n "get_user_pingykj_credentials" scraper.py main.py database.py`
Expected: 只剩 `database.get_effective_pingykj_credentials` 内部对它的回落调用，以及 `main.py` 里显式取"通用凭据"展示的地方
（若有 in-place 使用，逐个判断是否该改成 `get_effective_pingykj_credentials`，并在报告中说明）。

- [ ] **Step 6: Commit**

```bash
git add scraper.py scripts/verify_scraper_site.py
git commit -m "feat(site): 自动登录改用该站点生效凭据（专属优先、回落通用）"
```

---

### Task 3: 后端接口（站点列表/改名、每站凭据保存、管理页每站状态）

**Files:**
- Modify: `main.py`（新增 2 个路由；改 3 个既有路由；改 `/api/users`）
- Create: `scripts/verify_sites_api.py`

**Interfaces:**
- Consumes: Task 1 的凭据函数；`database.get_user_config` / `set_user_config`；`scraper.get_sites` / `DEFAULT_SITE` / `_user_sessions`
- Produces:
  - `GET /api/sites` → `{"sites": [{"key","name","base_url","content_url"}, ...]}`
  - `PUT /api/sites/names`（body `{"names": {"a": "...", "b": "..."}}`）→ `{"status":"ok"}`
  - `PUT /api/auth/pingykj-credentials` 增加 `site`（空 = 通用）
  - `POST /api/users/{id}/reconnect-pingykj`、`GET /api/users/{id}/pingykj-captcha` 增加 `site`
  - `GET /api/users` 每项增加 `sites: {"a": {"online","configured","username"}, "b": {...}}`，保留 `pingykj_online`

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_sites_api.py`：

```python
# -*- coding: utf-8 -*-
"""验证站点接口：改名落到当前用户、站点列表、每站凭据、管理页每站状态。"""
import sys, tempfile, shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import database
    tmp = Path(tempfile.mkdtemp(prefix="sites_api_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()

    import main
    from fastapi.testclient import TestClient
    from main import get_current_user, get_current_admin
    c = TestClient(main.app)

    admin = database.get_user_by_username("admin")
    c.app.dependency_overrides[get_current_user] = lambda: admin
    c.app.dependency_overrides[get_current_admin] = lambda: admin

    # 站点列表：两个站点，name 默认来自 config/DEFAULT_SITES
    r = c.get("/api/sites")
    assert r.status_code == 200, r.text
    sites = r.json()["sites"]
    assert [s["key"] for s in sites][:2] == ["a", "b"], sites
    default_a = sites[0]["name"]

    # 改名 → 当前用户看到新名字
    r = c.put("/api/sites/names", json={"names": {"a": "番茄", "b": "Reels"}})
    assert r.status_code == 200, r.text
    sites = c.get("/api/sites").json()["sites"]
    assert sites[0]["name"] == "番茄", sites
    assert sites[1]["name"] == "Reels", sites

    # 改名是每用户独立的：另一个用户看到的还是默认名
    u2 = database.create_user("u2", "p2", "user")
    c.app.dependency_overrides[get_current_user] = lambda: u2
    sites2 = c.get("/api/sites").json()["sites"]
    assert sites2[0]["name"] == default_a, f"改名不该影响其他用户: {sites2[0]['name']}"

    # 每站凭据：给 B 站单独保存 → 只落 B 站，A 站仍回落通用
    database.update_user(u2["id"], pingykj_username="shared", pingykj_password="shared_pw")
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "b_user", "pingykj_password": "b_pw", "site": "b"})
    assert r.status_code == 200, r.text
    assert database.get_effective_pingykj_credentials(u2["id"], "b")["username"] == "b_user"
    assert database.get_effective_pingykj_credentials(u2["id"], "a")["username"] == "shared"

    # 管理页每站状态
    c.app.dependency_overrides[get_current_user] = lambda: admin
    c.app.dependency_overrides[get_current_admin] = lambda: admin
    users = c.get("/api/users").json()
    me = [u for u in users if u["id"] == u2["id"]][0]
    assert "sites" in me, me
    assert set(me["sites"].keys()) >= {"a", "b"}, me["sites"]
    assert me["sites"]["b"]["configured"] is True, me["sites"]
    assert me["sites"]["a"]["configured"] is True, me["sites"]
    assert "online" in me["sites"]["a"], me["sites"]
    # 未配凭据的用户 → configured False
    u3 = database.create_user("u3", "p3", "user")
    users = c.get("/api/users").json()
    me3 = [u for u in users if u["id"] == u3["id"]][0]
    assert me3["sites"]["a"]["configured"] is False, me3["sites"]

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 站点接口——列表/改名(每用户独立)/每站凭据/管理页每站状态")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_sites_api.py`
Expected: FAIL —— `404` 或 `KeyError: 'sites'`（路由不存在）

- [ ] **Step 3: 加 `GET /api/sites` 与 `PUT /api/sites/names`**

```python
def _site_display_name(user_id: int, site: dict) -> str:
    """显示名解析：用户改的名 → config 的 name → key。"""
    try:
        names = json.loads(database.get_user_config(user_id).get("site_names") or "{}")
    except Exception:
        names = {}
    return (names.get(site["key"]) or "").strip() or site.get("name") or site["key"]


@app.get("/api/sites")
def api_sites(user: dict = Depends(get_current_user)):
    """返回站点列表；name 已按当前用户的改名解析。"""
    uid = _opt_user_id(user) or 1
    return {"sites": [{**s, "name": _site_display_name(uid, s)} for s in scraper.get_sites()]}


class SiteNamesBody(BaseModel):
    names: dict = {}


@app.put("/api/sites/names")
def api_sites_names(body: SiteNamesBody, user: dict = Depends(get_current_user)):
    """保存当前用户的站点显示名（key 为站点 key）。只接受已存在的站点 key。"""
    valid = {s["key"] for s in scraper.get_sites()}
    names = {k: str(v).strip() for k, v in (body.names or {}).items() if k in valid}
    database.set_user_config(user["id"], "site_names", json.dumps(names, ensure_ascii=False))
    return {"status": "ok", "names": names}
```

- [ ] **Step 4: 凭据路由支持站点**

`PingykjCredsBody` 增加 `site: str = ""`；`api_update_pingykj_creds` 改为：

```python
@app.put("/api/auth/pingykj-credentials")
def api_update_pingykj_creds(body: PingykjCredsBody, user: dict = Depends(get_current_user)):
    """当前用户自助保存书城登录凭据。site 为空 = 通用凭据；非空 = 该站点专属凭据。"""
    site = _norm_site(body.site) or ""
    if site:
        database.set_site_credentials(user["id"], site, body.pingykj_username, body.pingykj_password)
        return {"status": "ok", "message": f"已保存 {site} 站凭据", "site": site}
    database.update_user(user["id"],
                         pingykj_username=body.pingykj_username,
                         pingykj_password=body.pingykj_password)
    return {"status": "ok", "message": "通用书城凭据已保存", "site": ""}
```

`reconnect-pingykj` 与 `pingykj-captcha` 各加 `site: str = Query(default=None)`，并：
- `reconnect-pingykj`：`scraper.clear_user_session(user_id, site or None)`（None = 清该用户所有站点，保持既有语义）；
  取凭据改用 `database.get_effective_pingykj_credentials(user_id, site)`；`login_via_api_for_user(..., site=...)`。
- `pingykj-captcha`：`scraper.fetch_captcha_with_creds(username, password, site=...)`
  （凭据用 `get_effective_pingykj_credentials`）。

- [ ] **Step 5: `/api/users` 返回每站状态**

把 `api_list_users` 里的在线判断替换为：

```python
    for u in users:
        uid = u["id"]
        per_site = {}
        for s in scraper.get_sites():
            key = s["key"]
            site_creds = database.get_effective_pingykj_credentials(uid, key)
            session = scraper._user_sessions.get((uid, key))
            per_site[key] = {
                "online": bool(session and session.check_valid()),
                "configured": bool(site_creds and site_creds.get("username")),
                "username": (site_creds or {}).get("username", ""),
                "name": _site_display_name(uid, s),
            }
        u["sites"] = per_site
        u["pingykj_online"] = any(v["online"] for v in per_site.values())
```

（`u.get("pingykj_username")` 的既有判断已不再需要，但 `pingykj_online` 字段**保留**以免前端旧代码报错。）

- [ ] **Step 6: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_sites_api.py`
Expected: `OK: 站点接口——列表/改名(每用户独立)/每站凭据/管理页每站状态`

- [ ] **Step 7: 回归**

Run: 依次跑 `scripts/verify_routes_site.py`、`verify_analytics_site.py`、`verify_site_io.py`、`verify_site_migration.py`、`verify_site_e2e.py`、`verify_scraper_site.py`、`verify_scheduler_sites.py`、`verify_sites_config.py`
Expected: 全部 PASS；`python -c "import main"` 成功。

- [ ] **Step 8: Commit**

```bash
git add main.py scripts/verify_sites_api.py
git commit -m "feat(site): 站点列表/每用户改名 + 每站凭据保存 + 管理页每站登录状态"
```

---

### Task 4: 前端（下拉名从接口读 + 改名入口 + 凭据弹窗选站点 + 管理页每站徽标）

**Files:**
- Modify: `static/index.html`

**Interfaces:**
- Consumes: Task 3 的 `GET /api/sites` / `PUT /api/sites/names` / 带 site 的凭据接口 / `/api/users` 的 `sites` 字段
- Produces: `window._siteNames`（key → 显示名）

- [ ] **Step 1: 下拉文案改为从接口读**

在站点下拉初始化处（`index.html` 中 `#siteSelect` 相关脚本）改为：

```javascript
    // 站点下拉：文案来自 /api/sites（每用户可改名），key 不可变
    window._siteNames = {};
    function loadSiteOptions() {
      return fetch('/api/sites').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d || !d.sites) return;
        var sel = document.getElementById('siteSelect');
        var cur = sel.value;
        sel.innerHTML = '<option value="">合计</option>' + d.sites.map(function (s) {
          window._siteNames[s.key] = s.name;
          return '<option value="' + s.key + '">' + escapeHtml(s.name) + '</option>';
        }).join('');
        sel.value = cur;
      }).catch(function () {});
    }
```

并在页面初始化（`loadApiConfig().then(...)` 那一串）里调用一次 `loadSiteOptions()`。

- [ ] **Step 2: 加"重命名"入口**

在 `#siteSelect` 旁边加一个小按钮，点击后用 `prompt` 逐个改名并保存：

```javascript
    function renameSites() {
      var names = {};
      var keys = Object.keys(window._siteNames);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        var v = prompt('「' + k + '」站的显示名：', window._siteNames[k]);
        if (v === null) return;                     // 取消则整体放弃
        names[k] = v.trim() || window._siteNames[k];
      }
      fetch('/api/sites/names', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ names: names })
      }).then(function (r) { return r.ok ? r.json() : null; }).then(function () {
        return loadSiteOptions();
      });
    }
```

HTML 侧在 `#siteSelect` 后插入：

```html
        <button id="btnRenameSites" type="button" title="重命名站点"
                class="px-2 py-1 text-[11px] rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 cursor-pointer">重命名</button>
```

并绑定：`document.getElementById('btnRenameSites').addEventListener('click', renameSites);`

- [ ] **Step 3: 凭据弹窗加站点选择**

在弹窗「书城账号」上方插入：

```html
                <div>
                  <label class="block text-xs font-medium text-slate-500 mb-1">作用于</label>
                  <select id="pingykjCredsSite" class="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm input-focus">
                    <option value="">通用（两个站点共用）</option>
                  </select>
                </div>
```

打开弹窗时填充选项（用 `window._siteNames`），并让**获取验证码 / 登录验证 / 保存**都带上该站点：

- 取验证码 URL：`'/api/scraper/captcha?username=...&password=...' + siteQS()`，其中
  `function siteQS(){ var s = document.getElementById('pingykjCredsSite').value; return s ? ('&site=' + encodeURIComponent(s)) : ''; }`
- 登录验证 `fetch('/api/scraper/login', ...)` 的 body 加 `site`，
  或改为 `'/api/scraper/login' + (site ? ('?site=' + encodeURIComponent(site)) : '')`（该路由 `site` 是 query 参数）。
- 保存 `fetch('/api/auth/pingykj-credentials', ...)` 的 body 加 `site`。

**注意**：`/api/scraper/login` 与 `/api/auth/pingykj-credentials` 都在 `applySiteParam` 白名单**之外**
（`applySiteParam` 只管 `/api/dashboard/*`、`/api/novels/*`、`/api/fetch-novel`），所以这里必须**显式**带 site，不会被拦截器自动加。

- [ ] **Step 4: 管理页每站徽标**

把用户列表里那段"在线/重新登录"的单徽标，改为按 `u.sites` 逐站渲染：

```javascript
function siteBadges(u) {
  if (!u.sites) return u.pingykj_online ? '<span class="...">● 在线</span>' : '';
  return Object.keys(u.sites).map(function (k) {
    var s = u.sites[k], label = htmlEscape(s.name || k);
    if (!s.configured) return '<span class="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-500/20 text-slate-400 text-[10px] font-medium mr-1" title="未配置该站凭据">' + label + ' 未配凭据</span>';
    if (s.online) return '<span class="inline-flex items-center px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-400 text-[10px] font-medium mr-1">● ' + label + ' 在线</span>';
    return '<span class="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-400 text-[10px] font-medium mr-1">' + label + ' 未登录</span>';
  }).join('');
}
```

并把 `reconnectPingykj(id, username, btn)` 的调用/定义都加上 `site` 参数（透传到
`POST /api/users/{id}/reconnect-pingykj?site=...`），未登录的那个站点徽标带一个"重新登录"小按钮。

- [ ] **Step 5: 手工验证**

启动 `python -m uvicorn main:app --port 8000`，浏览器检查：
1. 站点下拉显示的是你改过的名字；改名后立即生效；**换个用户登录看到的是各自的名字**
2. 凭据弹窗选「B站」→ 获取验证码/登录验证/保存都带 `?site=b`（Network 里确认）
3. 管理页用户列表每行显示 **A/B 两个徽标**（在线 / 未登录 / 未配凭据）
验证后关掉服务。

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat(site): 前端站点名可改(每用户)、凭据弹窗按站点、管理页每站登录徽标"
```

---

## Self-Review

**设计覆盖检查**

| 设计要点 | 对应任务 |
|---|---|
| 站点显示名每用户可改（存 `user_config`，解析顺序 用户→config→key） | Task 3（`_site_display_name` + `GET /api/sites` + `PUT /api/sites/names`）+ Task 4 Step 1/2 |
| 每站凭据（新表、专属优先回落通用、密文存储） | Task 1 + Task 3 Step 3/4 |
| 自动登录用该站生效凭据 | Task 2 |
| 管理页每站登录状态（`sites` 字段，保留 `pingykj_online`） | Task 3 Step 5 + Task 4 Step 4 |
| 补上 B 站验证码登录的界面入口 | Task 3 Step 4（`reconnect-pingykj`/`pingykj-captcha` 加 site）+ Task 4 Step 3 |
| 不迁移现有数据、key 不可变 | Task 1（纯新增表）、Global Constraints |
| 不动 `config.json` 的 base_url/content_url | 全任务未涉及 |

**占位符扫描**：无 TBD/TODO；每个代码步骤均给出可执行代码或精确改动点。

**类型一致性**：`get_effective_pingykj_credentials(user_id, site) -> Optional[{"username","password"}]` 在
Task 1 定义、Task 2 与 Task 3 一致使用；`sites` 字段结构（`online`/`configured`/`username`/`name`）在
Task 3 Step 5 产出、Task 4 Step 4 消费，字段名一致。

**已知需在执行时确认的点**：
- Task 4 的 `#siteSelect` 初始化代码位置、`reconnectPingykj` 的现有签名，需按 `static/index.html` 实际内容落位。
- Task 2 Step 5 的 grep 可能出现 `main.py` 里展示用的 `get_user_pingykj_credentials` 调用，需逐个判断是否应改。
