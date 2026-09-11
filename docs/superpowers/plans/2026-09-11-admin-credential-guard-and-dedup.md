# 管理员凭证护栏 + 重复数据清理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ① 让管理员账号**不参与书城同步、也不能保存书城凭据**；② 普通用户之间绑同一个书城账号时**给出警告**；③ 清理掉已经产生的重复数据。

**Architecture:** 重复的根因是 `ad_daily_stats` 的唯一键含 `user_id` —— 同一个书城账号被两个本地用户同步就会存两份，而管理员看板不带 user 过滤 → 消耗/收入翻倍。修法是**从源头堵住**（管理员不参与 + 重复账号警告）**+ 清掉已产生的副本**。

**Tech Stack:** Python 3 + FastAPI + SQLite（`database.py`）+ 单文件前端 `static/index.html`。

**Spec:** 无独立 spec —— 设计在对话中确认：用户明确「管理员账号不需要登录凭证」；已裁决「只删重复部分（保留 admin 独有的 8 行）」「普通用户之间重复 → 警告但允许」。

## Global Constraints

- **管理员（`role='admin'`）不参与书城同步**：同步枚举必须排除。
- **管理员不能保存书城凭据**：通用与每站凭据都拒绝（400）。
- **普通用户之间绑同一书城账号 → 警告但允许保存**（不能拒绝）。
- 数据清理**只删重复行**：admin 名下、且**其他用户也有同 `(date, ad_account)`** 的 pingykj 行（实测 950 行）；
   **保留 admin 独有的**（实测 8 行）；**不得**碰其他用户的数据。
- **清理前必须先备份** `data/dashboard.db`；清理必须可 dry-run（先打印将要删的行数与样例）。
- **不动** `orders` / `raw_ad_stats` / `raw_orders`（唯一键不含 `user_id`，无重复）、不动 Meta 行（实测 0 重复）。
- 本仓库**没有单元测试框架**，验证用可运行的 assert 脚本（放 `scripts/`）。

---

### Task 1: 管理员凭证护栏 + 重复账号警告

**Files:**
- Modify: `database.py`（`list_active_users_with_credentials`、新增重复账号查询）
- Modify: `main.py`（`PUT /api/auth/pingykj-credentials`）
- Modify: `static/index.html`（保存凭据后展示警告）
- Create: `scripts/verify_cred_guards.py`

**Interfaces:**
- Consumes: `database.get_effective_pingykj_credentials`、`scraper.get_sites`
- Produces:
  - `database.find_users_using_pingykj_account(username: str, exclude_user_id: int) -> List[Dict]`
    （返回其他用户的 `[{"id","username","role","site"}]`，`site` 为空表示通用凭据）
  - `PUT /api/auth/pingykj-credentials` 的响应新增可选字段 `warning`（字符串）

- [ ] **Step 1: 写失败的验证脚本**

`scripts/verify_cred_guards.py`：

```python
# -*- coding: utf-8 -*-
"""验证：管理员不参与同步、管理员不能存凭据、重复账号给出警告。"""
import shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import database
    tmp = Path(tempfile.mkdtemp(prefix="cred_guard_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()

    admin = database.get_user_by_username("admin")
    u2 = database.create_user("u2", "p2", "user", pingykj_username="shared_acc",
                              pingykj_password="shared_pw")

    # 1) 管理员不参与同步枚举
    names = [u["username"] for u in database.list_active_users_with_credentials()]
    assert "admin" not in names, f"管理员不该被枚举: {names}"
    assert "u2" in names, f"普通用户应在枚举里: {names}"

    # 管理员即使有每站凭据也不枚举
    database.set_site_credentials(admin["id"], "a", "shared_acc", "shared_pw")
    names = [u["username"] for u in database.list_active_users_with_credentials()]
    assert "admin" not in names, f"配了每站凭据的管理员仍不该被枚举: {names}"

    # 2) 重复账号查询：能找出绑了同一账号的其他人
    hits = database.find_users_using_pingykj_account("shared_acc", exclude_user_id=u2["id"])
    assert any(h["id"] == admin["id"] for h in hits), f"应查到 admin 也绑了 shared_acc: {hits}"
    assert database.find_users_using_pingykj_account("nobody", exclude_user_id=u2["id"]) == []

    # 3) 接口层
    import main
    from fastapi.testclient import TestClient
    from main import get_current_user
    c = TestClient(main.app)
    c.app.dependency_overrides[get_current_user] = lambda: admin

    # 管理员存通用凭据 → 400
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "x", "pingykj_password": "y", "site": ""})
    assert r.status_code == 400, f"管理员不该能存通用凭据: {r.status_code} {r.text}"
    # 管理员存每站凭据 → 400
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "x", "pingykj_password": "y", "site": "b"})
    assert r.status_code == 400, f"管理员不该能存每站凭据: {r.status_code} {r.text}"

    # 普通用户存已被 admin 占用的账号 → 200 且带 warning
    c.app.dependency_overrides[get_current_user] = lambda: u2
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "shared_acc", "pingykj_password": "shared_pw", "site": "b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("warning"), f"重复账号应返回 warning: {body}"
    assert "admin" in body["warning"], f"warning 应指出被谁占用: {body['warning']}"

    # 无重复时不带 warning
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "brand_new_acc", "pingykj_password": "pw", "site": ""})
    assert r.status_code == 200 and not r.json().get("warning"), r.text

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 管理员护栏与重复账号警告均生效")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_cred_guards.py`
Expected: FAIL（管理员被枚举 / 或接口 200 而非 400）

- [ ] **Step 3: 同步枚举排除管理员**

`database.py` 的 `list_active_users_with_credentials()`：在既有 `WHERE is_active = 1 AND (...)` 上再加
`AND role != 'admin'`（保持原有 `(通用凭据 OR 任一站点凭据)` 的判定不变）。

- [ ] **Step 4: 重复账号查询**

`database.py` 新增：

```python
def find_users_using_pingykj_account(username: str, exclude_user_id: int) -> List[Dict[str, Any]]:
    """找出除 exclude_user_id 外，还绑定了该 pingykj 账号的用户（含站点）。
    同时查通用凭据与每站凭据；site 为空表示通用凭据。"""
    if not (username or "").strip():
        return []
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, username, role, '' AS site FROM users
              WHERE pingykj_username = ? AND id != ?
            UNION
            SELECT u.id, u.username, u.role, c.site FROM user_site_credentials c
              JOIN users u ON u.id = c.user_id
              WHERE c.username = ? AND c.user_id != ? AND c.password_encrypted != ''
        """, (username, exclude_user_id, username, exclude_user_id)).fetchall()
    return [dict(r) for r in rows]
```

> 注意：`user_site_credentials` 表可能不存在于极老的库上——本函数只在保存凭据时调用，而那时表一定存在
> （`init_db` 会建）；若你想更保守，可用 `try/except` 包住并返回 `[]`。

- [ ] **Step 5: 接口层：拒绝管理员 + 返回警告**

`main.py` 的 `api_update_pingykj_creds` 改为：

```python
@app.put("/api/auth/pingykj-credentials")
def api_update_pingykj_creds(body: PingykjCredsBody, user: dict = Depends(get_current_user)):
    """当前用户自助保存书城登录凭据。site 为空 = 通用凭据；非空 = 该站点专属凭据。
    管理员账号不参与书城同步，因此不允许保存书城凭据。"""
    if user.get("role") == "admin":
        raise HTTPException(status_code=400, detail="管理员账号无需书城凭据（管理员看全部用户数据）")
    site = _norm_site(body.site, write=True) or ""

    # 同一书城账号被其他本地用户绑定时给出警告（会导致统计重复），但仍允许保存
    others = database.find_users_using_pingykj_account(body.pingykj_username, user["id"])
    warning = ""
    if others:
        who = "、".join(f"{o['username']}({o['site'] or '通用'})" for o in others)
        warning = f"该账号已被 {who} 绑定，两个账号同步会重复统计数据"

    if site:
        ...（保持既有逻辑：空密码 400 + set_site_credentials）
    else:
        ...（保持既有逻辑：update_user）
    return {"status": "ok", "message": ..., "site": site, "warning": warning}
```

**保留**既有的「站点分支拒绝空密码」与「非法 site 400」行为。

- [ ] **Step 6: 前端展示警告**

`static/index.html` 保存凭据的 `.then` 里，若 `d.warning` 存在，用既有的消息区
（`pingykjCredsMsg`）以琥珀色显示该警告（**不要**用 `alert`，避免打断）；成功文案照旧。

- [ ] **Step 7: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 python scripts/verify_cred_guards.py`
Expected: `OK: 管理员护栏与重复账号警告均生效`

- [ ] **Step 8: 回归**

依次跑 `scripts/verify_sites_api.py`、`verify_site_creds.py`、`verify_scraper_site.py`、
`verify_scheduler_sites.py`、`verify_routes_site.py`、`verify_site_io.py`、`verify_site_migration.py`、
`verify_site_e2e.py`、`verify_analytics_site.py`、`verify_sites_config.py`、`verify_dual_*`（若存在），
以及 `python -c "import main"`。
Expected: 全部通过。**注意**：`verify_sites_api.py` 里可能用 admin 做样本存凭据——若因本次改动变红，
**那是预期**，请把该用例改用普通用户，并在报告中说明。

- [ ] **Step 9: Commit**

```bash
git add database.py main.py static/index.html scripts/verify_cred_guards.py
git commit -m "feat(cred): 管理员不参与书城同步且不能存凭据；同一书城账号被他人绑定时警告"
```

---

### Task 2: 清理已重复的数据

**Files:**
- Create: `scripts/dedup_admin_stats.py`（**必须支持 `--dry-run`**）

**Interfaces:**
- Consumes: 无
- Produces: 命令行脚本，`--apply` 才真正删除

- [ ] **Step 1: 写脚本（默认 dry-run）**

`scripts/dedup_admin_stats.py`：

```python
# -*- coding: utf-8 -*-
"""清理因「同一书城账号被 admin 与其他用户同时同步」产生的重复行。

规则：删除 user_id=1(admin) 名下、source!='meta'、且**其他用户也有同 (date, ad_account)** 的行。
      admin 独有的行保留。默认 dry-run，加 --apply 才真删。

用法：
    python scripts/dedup_admin_stats.py            # 只看将删多少
    python scripts/dedup_admin_stats.py --apply    # 真删（拒绝 apply 前未 dry-run 的规则不需要，直接执行）
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

ADMIN_UID = 1

DUP_WHERE = """
    user_id = ? AND source != 'meta' AND EXISTS (
        SELECT 1 FROM ad_daily_stats b
        WHERE b.date = ad_daily_stats.date
          AND b.ad_account = ad_daily_stats.ad_account
          AND b.user_id != ?
          AND b.source != 'meta')
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只预览）")
    args = ap.parse_args()

    import database
    db_path = database._get_db_path()

    with database.get_conn() as conn:
        total_admin = conn.execute(
            "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
            (ADMIN_UID,)).fetchone()[0]
        dup = conn.execute(
            f"SELECT COUNT(*) FROM ad_daily_stats WHERE {DUP_WHERE}",
            (ADMIN_UID, ADMIN_UID)).fetchone()[0]
        keep = total_admin - dup
        sample = conn.execute(
            f"SELECT date, ad_account, total_spend FROM ad_daily_stats WHERE {DUP_WHERE} LIMIT 5",
            (ADMIN_UID, ADMIN_UID)).fetchall()

        print(f"admin(uid={ADMIN_UID}) pingykj 总行数: {total_admin}")
        print(f"将删除（其他用户也有同日期+同账户）: {dup}")
        print(f"将保留（admin 独有）: {keep}")
        print("样例:")
        for r in sample:
            print("  ", dict(r))

        if not args.apply:
            print("\n（dry-run，未做任何修改。加 --apply 才真删）")
            return

        # 备份
        bak = db_path.with_name(db_path.name + f".bak-dedup-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy(db_path, bak)
        print(f"\n已备份到: {bak}")

        cur = conn.execute(f"DELETE FROM ad_daily_stats WHERE {DUP_WHERE}", (ADMIN_UID, ADMIN_UID))
        print(f"已删除 {cur.rowcount} 行")

    # 删后核对
    with database.get_conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
            (ADMIN_UID,)).fetchone()[0]
        dup_left = conn.execute(
            f"SELECT COUNT(*) FROM ad_daily_stats WHERE {DUP_WHERE}",
            (ADMIN_UID, ADMIN_UID)).fetchone()[0]
    print(f"删后 admin 行数: {left}（应等于保留数 {keep}）")
    print(f"删后残留重复: {dup_left}（应为 0）")
    assert left == keep and dup_left == 0, "清理结果不符合预期，请用备份恢复后排查"


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: dry-run 并核对数字**

Run: `PYTHONIOENCODING=utf-8 python scripts/dedup_admin_stats.py`
Expected: `将删除: 950` / `将保留: 8`（与本计划实测一致；若环境不同以实际为准，但**保留数必须远小于删除数**）

- [ ] **Step 3: 记录「删前」各用户的口径**

Run:
```bash
python -c "
import analytics
for u in [None, 2, 3, 4, 5]:
    r = analytics.get_summary(user_id=u) if u else analytics.get_summary()
    print('user_id=', u, 'spend=', r['total_spend'], 'orders=', r['order_count'])
"
```
把输出记进报告（这是**删后要对照**的基线：`user_id=2..5` 的数值必须**完全不变**，`None`（管理员口径）应当**下降**）。

- [ ] **Step 4: 真正执行**

Run: `PYTHONIOENCODING=utf-8 python scripts/dedup_admin_stats.py --apply`
Expected: 打印备份路径、`已删除 950 行`、`删后 admin 行数: 8`、`删后残留重复: 0`

- [ ] **Step 5: 核对删后口径**

重跑 Step 3 的命令，确认：
- `user_id=2/3/4/5` 的 `total_spend` 与 `order_count` **与删前逐位相同**（没动别人的数据）；
- `None`（管理员全局口径）的 `total_spend` **下降**（不再重复计数）。
把两组数字都写进报告。

- [ ] **Step 6: 清掉管理员误配的每站凭据**

```bash
python -c "
import database
for s in ('a','b'):
    database.delete_site_credentials(1, s)
print('剩余每站凭据:', database.get_site_credentials(1,'a'), database.get_site_credentials(1,'b'))
"
```
Expected: 两个都打印 `None`。

- [ ] **Step 7: Commit**

```bash
git add scripts/dedup_admin_stats.py
git commit -m "chore(data): 清理 admin 名下因重复绑定书城账号产生的重复统计行（含 dry-run 与备份）"
```

---

## Self-Review

**需求覆盖**

| 需求 | 对应任务 |
|---|---|
| 管理员账号不需要登录凭证（不参与同步） | Task 1 Step 3 + Step 5 |
| 管理员不能误存凭据（防再犯） | Task 1 Step 5（400） |
| 用户与管理员绑同一账户 → 统计重复（防再犯） | Task 1 Step 4 + Step 5（警告）+ Step 6（前端展示） |
| 清掉已重复的数据（只删重复、保留独有） | Task 2 Step 1/2/4 |
| 清掉管理员误配的每站凭据 | Task 2 Step 6 |
| 备份（破坏性操作前的硬要求） | Task 2 Step 1 的 `--apply` 分支 |

**占位符扫描**：无 TBD；Task 1 Step 5 中 `...（保持既有逻辑）` 是**指代已有代码块**，
执行者需打开 `main.py` 照原样保留，不是"待补"。

**类型一致性**：`find_users_using_pingykj_account(username, exclude_user_id) -> List[Dict]`
在 Step 4 定义、Step 5 使用，字段 `id/username/role/site` 一致。

**已知需在执行时确认的点**：
- Task 1 Step 8 的 `verify_sites_api.py` 可能用 admin 存凭据，改动后会变红——**属预期**，应改用普通用户样本。
- Task 2 的删除数（950/8）是本地库实测值，**生产库数字可能不同**，脚本按规则计算、不写死。
- **生产库执行清理前必须停服或确认无同步在跑**（本机 8000 端口有实例在跑），否则删除过程中可能有新行写入。
