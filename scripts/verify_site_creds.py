# -*- coding: utf-8 -*-
"""验证每站凭据：专属优先、回落通用、删除后回落、站点隔离、批量同步枚举。"""
import base64
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

    # 密码是加密存储的（不得明文落库，且装了 cryptography 时必须是真密文而非可逆的 base64 混淆）
    import sqlite3
    c = sqlite3.connect(str(tmp))
    database.set_site_credentials(uid, "b", "b_user", "b_pw")
    raw = c.execute("SELECT password_encrypted FROM user_site_credentials "
                    "WHERE user_id=? AND site='b'", (uid,)).fetchone()[0]
    assert raw and "b_pw" not in raw, f"密码疑似明文落库: {raw}"
    assert database._get_fernet() is not None, \
        "未安装 cryptography：密码只是 base64 混淆（可逆），requirements.txt 已要求该依赖"
    try:
        leaked = base64.b64decode(raw.encode()).decode()
    except Exception:
        leaked = ""                      # Fernet 密文 base64 解出来是二进制，解不出明文
    assert leaked != "b_pw", f"密文可用 base64 还原成明文，不是真加密: {raw}"
    c.close()

    # site 为空 → 回落通用
    assert database.get_effective_pingykj_credentials(uid, "")["username"] == "shared_user"

    # 批量同步枚举：只配站点凭据（通用为空）的用户必须出现在 list_active_users_with_credentials()
    # —— 否则每 120s 自动同步 / 每日全量 / 管理员「同步全部」都会静默漏掉他
    uid3 = database.create_user("siteonly", "p1", "user")
    database.set_site_credentials(uid3, "b", "b_only", "b_only_pw")
    assert database.get_effective_pingykj_credentials(uid3, "b")["username"] == "b_only"
    active_ids = {u["id"] for u in database.list_active_users_with_credentials()}
    assert uid3 in active_ids, f"只配站点凭据的用户被批量同步漏掉（枚举到 {active_ids}）"

    # 反向对照：无通用凭据、站点凭据是坏行（有用户名无密码）的用户不该被枚举到
    uid4 = database.create_user("nosite", "p1", "user")
    database.set_site_credentials(uid4, "b", "onlyname", "")
    assert database.get_effective_pingykj_credentials(uid4, "b") is None
    assert uid4 not in {u["id"] for u in database.list_active_users_with_credentials()}, \
        "坏行用户不该算「有凭据」"

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 每站凭据——专属优先 / 回落通用 / 删除后回落 / 站点隔离 / 真加密 / 批量同步枚举")


if __name__ == "__main__":
    main()
