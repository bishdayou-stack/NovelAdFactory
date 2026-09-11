# -*- coding: utf-8 -*-
"""验证站点接口：改名落到当前用户、站点列表、每站凭据、管理页每站状态。

另含两条编排方裁决的断言：
  裁决 15：PUT /api/auth/pingykj-credentials 传非法 site → 400，且通用凭据不被改动（写语义）；
  转交项：只配了 B 站专属凭据、没配通用凭据的用户，带 site=b 的 reconnect/captcha
          不应被「未配置书城凭据」拦下。
登录/取验证码一律打桩，不联网。
"""
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
    import scraper
    from fastapi.testclient import TestClient
    from main import get_current_user, get_current_admin

    # 不联网：打桩登录/取验证码，记录实际收到的 site 与凭据
    calls = {}

    def _fake_login(user_id, username, password, site=None, captcha="", check_key=""):
        calls["login"] = {"user_id": user_id, "username": username,
                          "password": password, "site": site}
        return True, "stub-ok"

    def _fake_captcha(username, password, site=None):
        calls["captcha"] = {"username": username, "password": password, "site": site}
        return "data:image/png;base64,x", "ck-1", None

    scraper.login_via_api_for_user = _fake_login
    scraper.fetch_captcha_with_creds = _fake_captcha

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
    # 非法站点 key 被忽略
    r = c.put("/api/sites/names", json={"names": {"a": "番茄", "zzz": "野站"}})
    assert r.status_code == 200, r.text
    assert "zzz" not in r.json()["names"], r.json()

    # 改名是每用户独立的：另一个用户看到的还是默认名
    u2 = database.get_user(database.create_user("u2", "p2", "user"))
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

    # 裁决 15：非法 site 是写操作 → 400，且通用凭据分毫未动（不能被静默归一成空串覆盖通用凭据）
    before = database.get_user_pingykj_credentials(u2["id"])
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "bogus", "pingykj_password": "bogus_pw", "site": "x"})
    assert r.status_code == 400, f"非法 site 应 400，实际 {r.status_code} {r.text}"
    after = database.get_user_pingykj_credentials(u2["id"])
    assert after == before, f"非法站点不该改动通用凭据: {before} -> {after}"
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
    assert "pingykj_online" in me, me
    # 未配凭据的用户 → configured False
    u3 = database.get_user(database.create_user("u3", "p3", "user"))
    users = c.get("/api/users").json()
    me3 = [u for u in users if u["id"] == u3["id"]][0]
    assert me3["sites"]["a"]["configured"] is False, me3["sites"]

    # 转交项：只配了 B 站专属凭据、没有通用凭据的用户 —— 前置校验必须按「该站生效凭据」判定
    u4 = database.get_user(database.create_user("u4", "p4", "user"))
    assert not u4.get("pingykj_username"), u4
    database.set_site_credentials(u4["id"], "b", "b_only", "b_only_pw")

    calls.clear()
    r = c.post(f"/api/users/{u4['id']}/reconnect-pingykj", params={"site": "b"})
    assert r.status_code == 200, f"只配 B 站凭据的用户带 site=b 不该被拦: {r.status_code} {r.text}"
    assert calls["login"]["username"] == "b_only", calls
    assert calls["login"]["password"] == "b_only_pw", calls
    assert calls["login"]["site"] == "b", calls

    calls.clear()
    r = c.get(f"/api/users/{u4['id']}/pingykj-captcha", params={"site": "b"})
    assert r.status_code == 200, f"captcha 同上: {r.status_code} {r.text}"
    assert calls["captcha"]["username"] == "b_only", calls

    # 反向对照：不带 site（= 通用凭据）时，u4 确实没配 → 仍应 400，说明校验没被整体删掉
    r = c.post(f"/api/users/{u4['id']}/reconnect-pingykj")
    assert r.status_code == 400, f"没配通用凭据的用户不带 site 应 400: {r.status_code} {r.text}"

    # 裁决 17：这两条也是写操作（建/换会话 + 向书城发起请求），非法 site 必须 400，不能静默按默认站登录。
    # 用 u2（通用凭据有效）做样本：若只按读语义归一，非法 site 会回落到通用凭据一路 200 走完登录——
    # 断言必须能区分「拦下」与「拿通用凭据照跑」。
    calls.clear()
    r = c.post(f"/api/users/{u2['id']}/reconnect-pingykj", params={"site": "x"})
    assert r.status_code == 400, f"reconnect 非法 site 应 400: {r.status_code} {r.text}"
    r = c.get(f"/api/users/{u2['id']}/pingykj-captcha", params={"site": "x"})
    assert r.status_code == 400, f"captcha 非法 site 应 400: {r.status_code} {r.text}"
    assert not calls, f"非法 site 不该发起任何登录/取码请求: {calls}"
    # 合法站点仍照常
    r = c.post(f"/api/users/{u2['id']}/reconnect-pingykj", params={"site": "b"})
    assert r.status_code == 200 and calls["login"]["username"] == "b_user", (r.text, calls)

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 站点接口——列表/改名(每用户独立)/每站凭据(非法 site 400)/管理页每站状态/仅站点凭据可登录")


if __name__ == "__main__":
    main()
