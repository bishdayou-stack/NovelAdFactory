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
    # create_user 返回 user_id（int），取回完整行做样本
    u2 = database.get_user(database.create_user("u2", "p2", "user", pingykj_username="shared_acc",
                                                pingykj_password="shared_pw"))

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

    # 4) 管理员用户管理路由同样不许给 admin 角色写凭据（Ruling 19）：
    #    否则管理员账号会在界面上显示成「书城凭据 ✓」，与「管理员不参与同步」自相矛盾
    from main import get_current_admin
    c.app.dependency_overrides[get_current_admin] = lambda: admin

    # 创建 role=admin 且带凭据 → 400（不是静默丢弃：静默丢弃会让管理员以为设上了）
    r = c.post("/api/users", json={"username": "admin_creds", "password": "p", "role": "admin",
                                   "pingykj_username": "acc", "pingykj_password": "pw"})
    assert r.status_code == 400, f"不该能创建带凭据的管理员: {r.status_code} {r.text}"
    assert database.get_user_by_username("admin_creds") is None, "被拒时不该把用户建出来"
    # 普通用户带凭据照旧可建（别把整条路堵死）
    r = c.post("/api/users", json={"username": "ok_user", "password": "p", "role": "user",
                                   "pingykj_username": "acc2", "pingykj_password": "pw2"})
    assert r.status_code == 200, f"普通用户带凭据不该被拦: {r.status_code} {r.text}"

    # 已有管理员：改成非空凭据 → 400，且不落库
    r = c.put(f"/api/users/{admin['id']}", json={"pingykj_username": "acc"})
    assert r.status_code == 400, f"不该能给管理员写入凭据: {r.status_code} {r.text}"
    assert not database.get_user(admin["id"])["pingykj_username"], "被拒后不该落库"
    # None = 不改，不算写凭据（前端表单常整体回传）→ 仍成功
    r = c.put(f"/api/users/{admin['id']}",
              json={"pingykj_username": None, "pingykj_password": None, "display_name": "总管"})
    assert r.status_code == 200, f"None 表示不改，不该被拦: {r.status_code} {r.text}"
    assert database.get_user(admin["id"])["display_name"] == "总管", "其余字段应照常更新"
    # 提升为 admin 的同时塞凭据 → 也拦（生效角色是 admin，不能只看改前的 role）
    uid_p = database.create_user("promote_me", "p", "user")
    r = c.put(f"/api/users/{uid_p}", json={"role": "admin", "pingykj_username": "acc3"})
    assert r.status_code == 400, f"提升为管理员时塞凭据也该拦: {r.status_code} {r.text}"
    assert database.get_user(uid_p)["role"] == "user", "被拒时不该只改一半"

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 管理员护栏与重复账号警告均生效")


if __name__ == "__main__":
    main()
