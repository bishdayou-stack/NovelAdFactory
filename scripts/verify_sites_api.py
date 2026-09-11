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

    # --- Important 1：非 id=1 的管理员改名必须生效（读侧不能把 uid 写死成 1）---
    admin2 = database.get_user(database.create_user("admin2", "p2", "admin"))
    assert admin2["id"] != 1, admin2
    c.app.dependency_overrides[get_current_user] = lambda: admin2
    c.app.dependency_overrides[get_current_admin] = lambda: admin2
    seen_before = c.get("/api/sites").json()["sites"][0]["name"]
    r = c.put("/api/sites/names", json={"names": {"a": "副站"}})
    assert r.status_code == 200, r.text
    got_a2 = c.get("/api/sites").json()["sites"][0]["name"]
    assert got_a2 == "副站", \
        f"副管理员改名没生效（读侧按 uid=1 读？）：改名前 {seen_before!r}，PUT 后仍是 {got_a2!r}"
    me_a2 = [u for u in c.get("/api/users").json() if u["id"] == admin2["id"]][0]
    assert me_a2["sites"]["a"]["name"] == "副站", me_a2["sites"]
    c.app.dependency_overrides[get_current_user] = lambda: admin
    c.app.dependency_overrides[get_current_admin] = lambda: admin
    assert c.get("/api/sites").json()["sites"][0]["name"] == "番茄", "副管理员的改名污染了主管理员"

    # --- Minor：部分提交只更新本次给的 key，不清掉其它站的改名 ---
    r = c.put("/api/sites/names", json={"names": {"b": "B站新名"}})
    assert r.status_code == 200, r.text
    got = [s["name"] for s in c.get("/api/sites").json()["sites"]]
    assert got[:2] == ["番茄", "B站新名"], f"部分提交清掉了没提交的站: {got}"

    # --- Minor：值为 null 不改名（不能变成字面量 "None"）---
    r = c.put("/api/sites/names", json={"names": {"a": None}})
    assert r.status_code == 200, r.text
    assert c.get("/api/sites").json()["sites"][0]["name"] == "番茄", \
        f"null 值不该改名，更不该写成 'None': {c.get('/api/sites').json()['sites'][0]}"

    # --- Minor：站点名要有长度上限 ---
    r = c.put("/api/sites/names", json={"names": {"a": "x" * 5000}})
    assert r.status_code == 400, f"超长站点名应 400，实际 {r.status_code}"
    assert c.get("/api/sites").json()["sites"][0]["name"] == "番茄", "超长名不该入库"

    # --- Important 2：站点凭据「有用户名/空密码」不许造行，历史坏行也不得遮蔽通用凭据 ---
    u5 = database.get_user(database.create_user("u5", "p5", "user",
                                                pingykj_username="gen", pingykj_password="gen_pw"))
    c.app.dependency_overrides[get_current_user] = lambda: u5
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "onlyname", "pingykj_password": "", "site": "b"})
    assert r.status_code == 400, f"站点凭据缺密码应 400，实际 {r.status_code} {r.text}"
    assert database.get_site_credentials(u5["id"], "b") is None, "不该造出坏行"
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "", "pingykj_password": "pw", "site": "b"})
    assert r.status_code == 400, f"站点凭据缺用户名应 400，实际 {r.status_code} {r.text}"
    # 通用分支保持现状（允许只存用户名），别被这条裁决波及
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "gen", "pingykj_password": ""})
    assert r.status_code == 200, f"通用分支不该被波及: {r.status_code} {r.text}"
    database.update_user(u5["id"], pingykj_username="gen", pingykj_password="gen_pw")

    # 历史脏行（用户名有、密码空）→ 视为未配置 → 回落通用，不再遮蔽
    database.set_site_credentials(u5["id"], "b", "onlyname", "")
    eff = database.get_effective_pingykj_credentials(u5["id"], "b")
    assert eff and eff["username"] == "gen", f"坏行遮蔽了通用凭据: {eff}"
    c.app.dependency_overrides[get_current_user] = lambda: admin
    c.app.dependency_overrides[get_current_admin] = lambda: admin
    me5 = [u for u in c.get("/api/users").json() if u["id"] == u5["id"]][0]
    assert me5["sites"]["b"]["username"] == "gen", me5["sites"]
    assert me5["sites"]["b"]["configured"] is True, me5["sites"]
    # configured 与 reconnect 前置条件必须一致：不再「显示已配置、点下去报未配置」
    calls.clear()
    r = c.post(f"/api/users/{u5['id']}/reconnect-pingykj", params={"site": "b"})
    assert r.status_code == 200 and calls["login"]["username"] == "gen", (r.text, calls)

    # 既无通用凭据、只有坏行的用户 → configured 必须 false
    u6 = database.get_user(database.create_user("u6", "p6", "user"))
    database.set_site_credentials(u6["id"], "b", "onlyname", "")
    assert database.get_effective_pingykj_credentials(u6["id"], "b") is None
    me6 = [u for u in c.get("/api/users").json() if u["id"] == u6["id"]][0]
    assert me6["sites"]["b"]["configured"] is False, me6["sites"]
    r = c.post(f"/api/users/{u6['id']}/reconnect-pingykj", params={"site": "b"})
    assert r.status_code == 400, r.text

    # --- Minor：保活探测抛异常不该让 /api/users 整个 500 ---
    class _Boom:
        def check_valid(self):
            raise RuntimeError("boom")

    key = (u2["id"], "a")
    saved = scraper._user_sessions.get(key)
    scraper._user_sessions[key] = _Boom()
    try:
        r = c.get("/api/users")
        assert r.status_code == 200, f"探测异常不该让管理页 500: {r.status_code} {r.text[:200]}"
        me_b = [u for u in r.json() if u["id"] == u2["id"]][0]
        assert me_b["sites"]["a"]["online"] is False, me_b["sites"]
    finally:
        if saved is None:
            scraper._user_sessions.pop(key, None)
        else:
            scraper._user_sessions[key] = saved

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 站点接口——列表/改名(每用户独立)/每站凭据(非法 site 400)/管理页每站状态/仅站点凭据可登录")


if __name__ == "__main__":
    main()
