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

    # 管理员配了**通用**凭据也不枚举（护栏只加在站点分支时这条会红——覆盖盲区）
    database.update_user(admin["id"], pingykj_username="shared_acc", pingykj_password="shared_pw")
    names = [u["username"] for u in database.list_active_users_with_credentials()]
    assert "admin" not in names, f"配了通用凭据的管理员仍不该被枚举: {names}"

    # 2) 重复账号查询：能找出绑了同一账号的其他人
    hits = database.find_users_using_pingykj_account("shared_acc", exclude_user_id=u2["id"])
    assert any(h["id"] == admin["id"] for h in hits), f"应查到 admin 也绑了 shared_acc: {hits}"
    assert database.find_users_using_pingykj_account("nobody", exclude_user_id=u2["id"]) == []
    # 大小写不敏感：书城侧账号校验通常 _ci，Shared_Acc 与 shared_acc 是同一个账号，
    # 漏判 → 不报警告 → 重复统计照旧发生而唯一的提示机制沉默。宁可误报不可漏报。
    assert database.find_users_using_pingykj_account("Shared_Acc", exclude_user_id=u2["id"]), \
        "大小写不同的同一账号必须能查到（否则唯一提示机制静默漏报）"

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

    # 已有管理员：改成非空凭据 → 400，且不落库（注意上面已给 admin 配过凭据，故比「未被改动」）
    before_pu = database.get_user(admin["id"])["pingykj_username"]
    r = c.put(f"/api/users/{admin['id']}", json={"pingykj_username": "acc"})
    assert r.status_code == 400, f"不该能给管理员写入凭据: {r.status_code} {r.text}"
    assert database.get_user(admin["id"])["pingykj_username"] == before_pu, "被拒后不该落库"
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

    # 5) C1：手动同步不能绕过护栏 —— 否则 admin 用自己的 id 再写一份，看板翻倍立刻复现
    # 5a) 取生效凭据这一处收口：管理员一律视为无凭据（覆盖 sync / reconnect / captcha 全部路径）
    database.set_site_credentials(admin["id"], "a", "shared_acc", "shared_pw")
    assert database.get_effective_pingykj_credentials(admin["id"], "a") is None, \
        "管理员的生效凭据必须为 None（否则手动同步会用 admin 的 id 再写一份）"
    assert database.get_effective_pingykj_credentials(admin["id"], "") is None, \
        "管理员的通用凭据同样必须为 None"

    # 5b) 明确报错，别让管理员点了没反应或只看到含糊错误
    c.app.dependency_overrides[get_current_user] = lambda: admin
    # 重新取一次：上面往库里写了 admin 的凭据列，注入的 dict 必须反映库里状态，否则断言是空的
    admin = database.get_user(admin["id"])
    assert admin["pingykj_username"], "前置：admin 库里确实有通用凭据列，断言才有意义"
    me = c.get("/api/auth/me").json()
    assert me["has_pingykj_creds"] is False, f"管理员不该显示「书城凭据 ✓」: {me}"
    assert all(not v["configured"] for v in me["sites"].values()), me["sites"]
    r = c.post("/api/novels/sync-books")
    assert r.status_code == 400, f"管理员手动同步书籍应 400: {r.status_code} {r.text}"
    r = c.post("/api/novels/sync-books-full")
    assert r.status_code == 400, f"管理员全量同步应 400: {r.status_code} {r.text}"
    # 普通用户不受影响（护栏不能把正常用户挡在门外）：打桩同步，不联网
    import scraper
    _orig_sync = scraper.sync_novel_books
    scraper.sync_novel_books = lambda uid, **kw: (3, "")
    try:
        c.app.dependency_overrides[get_current_user] = lambda: u2
        r = c.post("/api/novels/sync-books")
        assert r.status_code == 200 and r.json().get("count") == 3, (r.status_code, r.text)
    finally:
        scraper.sync_novel_books = _orig_sync

    # 5c) 还剩一条造出「管理员可用 session」的路：/api/scraper/login 用显式账密登录并把 session
    #     缓存在 (admin_id, site)，之后 sync_ads 复用该缓存、绕过凭据查询 → 照样翻倍。一并堵上。
    _orig_login = scraper.login_via_api_for_user
    scraper.login_via_api_for_user = lambda *a, **k: (True, "stub")
    try:
        c.app.dependency_overrides[get_current_user] = lambda: admin
        r = c.post("/api/scraper/login",
                   json={"username": "shared_acc", "password": "shared_pw"})
        assert r.status_code == 400, f"管理员不该能登录书城建会话: {r.status_code} {r.text}"
    finally:
        scraper.login_via_api_for_user = _orig_login

    # 6) 同一用户既绑通用又绑每站 → 警告文本里该用户名只出现一次
    u3 = database.get_user(database.create_user("u3", "p3", "user", pingykj_username="dup_acc",
                                                pingykj_password="dup_pw"))
    database.set_site_credentials(u3["id"], "b", "dup_acc", "dup_pw")
    c.app.dependency_overrides[get_current_user] = lambda: u2
    r = c.put("/api/auth/pingykj-credentials",
              json={"pingykj_username": "dup_acc", "pingykj_password": "pw", "site": "a"})
    assert r.status_code == 200, r.text
    w = r.json().get("warning") or ""
    assert w.count("u3") == 1, f"同一用户绑了通用+每站，用户名不该重复出现: {w!r}"
    assert w, "重复账号仍要给出警告"

    # 7) 一次性清理脚本 scripts/dedup_admin_stats.py 的护栏（用户很可能还要在生产库再跑一次）
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "dedup_admin_stats", ROOT / "scripts" / "dedup_admin_stats.py")
    dedup = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(dedup)

    _tmpd = Path(tempfile.mkdtemp(prefix="dedup_fix_"))
    _n = [0]

    def _fresh_db():
        _n[0] += 1
        database.DB_PATH = _tmpd / f"db{_n[0]}.db"
        database.init_db()
        return database.DB_PATH

    def _run_dedup(*argv):
        _argv = sys.argv
        sys.argv = ["dedup_admin_stats", *argv]
        try:
            dedup.main()
        finally:
            sys.argv = _argv

    # 7a) I1: user_id=1 不是管理员 → 必须中止。否则 --apply 会删掉这个普通用户名下的行
    #     （违反「不得碰其他用户数据」），而删后自检必然通过（keep 与 dup 同源，永远自洽）。
    _fresh_db()
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET role = 'user' WHERE id = 1")
    try:
        _run_dedup()
        raise AssertionError("user_id=1 非管理员时脚本必须中止，而不是照删")
    except SystemExit as e:
        assert "不是管理员" in str(e), f"中止原因要说清是角色不对: {e}"

    # 7b) ledger#6: 重复判定口径收紧到与唯一键一致（含 site）。跨站（同日期同账户不同 site）
    #     不是重复，不得误删；同站点的才删。
    _fresh_db()
    _other = database.create_user("other_user", "p", "user")
    with database.get_conn() as conn:
        for site in ("a", "b"):  # admin(id=1) 两条：a 与 b
            conn.execute("INSERT INTO ad_daily_stats(date, ad_account, source, user_id, site) "
                         "VALUES ('2026-01-01','ACC','pingykj',1,?)", (site,))
        conn.execute("INSERT INTO ad_daily_stats(date, ad_account, source, user_id, site) "
                     "VALUES ('2026-01-01','ACC','pingykj',?,'a')", (_other,))  # 只有 site=a 撞车
    _run_dedup("--apply")
    with database.get_conn() as conn:
        _left = [r["site"] for r in conn.execute(
            "SELECT site FROM ad_daily_stats WHERE user_id = 1 ORDER BY site").fetchall()]
    assert _left == ["b"], f"只该删同站点(site=a)那条，跨站的 site=b 必须保留: {_left}"

    # 7c) P1: 显式清空 admin 的**通用**凭据列（此前只清了每站凭据）。
    #     留着 → 用户管理页保存 admin 任何字段都 400（前端回填非空 → 护栏拦）。
    _fresh_db()
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET pingykj_username = 'legacy_acc', "
                     "pingykj_password_encrypted = 'legacy_enc' WHERE role = 'admin'")
    _run_dedup("--apply")
    _a = database.get_user(1)
    assert _a["pingykj_username"] == "" and _a["pingykj_password_encrypted"] == "", \
        f"admin 通用凭据列必须被清空: {_a['pingykj_username']!r}/{_a['pingykj_password_encrypted']!r}"

    # 8) P2: 把普通用户提升为 admin 时，剥掉他身上已带的凭据（通用 + 每站）。
    #    护栏只拦「本次写入非空凭据」，拦不住「被提升时身上已有」——留下就是取不到的死密钥，
    #    且用户管理页再保存该 admin 必 400。
    _uid_x = database.create_user("promote_creds", "p", "user",
                                  pingykj_username="legacy_x", pingykj_password="pw_x")
    database.set_site_credentials(_uid_x, "b", "legacy_site_x", "pw_site")
    assert database.get_effective_pingykj_credentials(_uid_x, "b"), "前置：提升前确有凭据"
    c.app.dependency_overrides[get_current_admin] = lambda: admin
    r = c.put(f"/api/users/{_uid_x}", json={"role": "admin"})
    assert r.status_code == 200, (r.status_code, r.text)
    _xu = database.get_user(_uid_x)
    assert _xu["role"] == "admin", _xu["role"]
    assert _xu["pingykj_username"] == "" and _xu["pingykj_password_encrypted"] == "", \
        f"提升为管理员后通用凭据应被剥掉: {_xu['pingykj_username']!r}"
    assert database.get_site_credentials(_uid_x, "b") is None, "提升为管理员后每站凭据也应被剥掉"
    # 已是 admin 的存量行不受这条影响（别把每次改资料都变成重写凭据列）
    database.update_user(admin["id"], pingykj_username="keep_me")
    r = c.put(f"/api/users/{admin['id']}", json={"display_name": "仍可改"})
    assert r.status_code == 200 and database.get_user(admin["id"])["pingykj_username"] == "keep_me", \
        "已是 admin 的行不该被顺手清掉凭据列（那是 dedup 脚本 P1 的事）"

    # 9) P3: 单管理员部署（只有默认 admin）下「同步」不能再静默无事发生还回成功文案
    _fresh_db()
    c.app.dependency_overrides[get_current_user] = lambda: database.get_user(1)
    r = c.post("/api/scraper/sync")
    assert r.status_code == 200, (r.status_code, r.text)
    _b = r.json()
    assert _b.get("status") == "noop" and "没有可同步的用户" in _b.get("message", ""), _b

    c.app.dependency_overrides.clear()
    shutil.rmtree(_tmpd, ignore_errors=True)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 管理员护栏与重复账号警告均生效")


if __name__ == "__main__":
    main()
