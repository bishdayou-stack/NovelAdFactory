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
