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

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 管理员护栏与重复账号警告均生效")


if __name__ == "__main__":
    main()
