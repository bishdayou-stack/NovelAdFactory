# -*- coding: utf-8 -*-
"""验证看板/小说/scraper 路由接受 site 参数并真正透传（TestClient + 打桩，不连真实书城）。

依赖：本脚本需要 httpx（FastAPI TestClient 的依赖），未列入 requirements.txt（那是运行时依赖清单），
本地跑之前先 `pip install httpx`。

第一部分只证明「加了 site 不报错」（FastAPI 忽略未知 query 参数，故不报错≠过滤生效）；
第二部分用打桩捕获下游函数收到的 site 关键字，证明透传与「空 = 遍历所有站点」确实成立。
真正的数据过滤验证由 Task 9 的端到端承担。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def _check_http_accepts_site(main):
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    if r.status_code == 200:
        h = {"Authorization": f"Bearer {r.json()['token']}"}
    else:
        # 本地库管理员口令可能已被修改：直接注入已认证用户，本脚本只验证路由签名与透传
        print(f"[提示] admin/admin123 登录失败（{r.text[:80]}），改用依赖注入模拟已登录管理员")
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 1, "role": "admin"}
        h = {}
    for path in ["/api/dashboard/summary", "/api/dashboard/daily-stats",
                 "/api/dashboard/trend", "/api/dashboard/accounts",
                 "/api/dashboard/account-ranking", "/api/dashboard/orders",
                 "/api/dashboard/anomalies", "/api/dashboard/user-ranking",
                 "/api/dashboard/novel-stats", "/api/dashboard/account-aliases",
                 "/api/novels/list"]:
        for site in ["", "a", "b"]:
            rr = c.get(path, params={"site": site} if site else {}, headers=h)
            assert rr.status_code == 200, f"{path}?site={site} -> {rr.status_code} {rr.text[:200]}"
    print("OK: 看板与小说路由均接受 site 参数")
    return c, h


def _check_invalid_site(main, c, h):
    """非法 site（如 x）必须被拦下：写类 400，读类归一为合计（不报错）。"""
    write_paths = ["/api/scraper/sync", "/api/scraper/reset-sync", "/api/scraper/logout",
                   "/api/novels/sync-books", "/api/novels/sync-content"]
    for path in write_paths:
        rr = c.post(path, params={"site": "x"}, json={}, headers=h)
        assert rr.status_code == 400, f"POST {path}?site=x -> {rr.status_code} {rr.text[:200]}"
    # 登录体必填，先把体给全，确保打到的是站点校验而不是 422
    rr = c.post("/api/scraper/login", params={"site": "x"},
                json={"username": "u", "password": "p"}, headers=h)
    assert rr.status_code == 400, f"POST /api/scraper/login?site=x -> {rr.status_code} {rr.text[:200]}"
    for path in ["/api/scraper/captcha", "/api/scraper/session-status"]:
        rr = c.get(path, params={"site": "x"}, headers=h)
        assert rr.status_code == 400, f"GET {path}?site=x -> {rr.status_code} {rr.text[:200]}"
    rr = c.post("/api/dashboard/account-aliases",
                params={"site": "x", "account_id": "a1", "alias": "n"}, headers=h)
    assert rr.status_code == 400, f"写别名 site=x -> {rr.status_code} {rr.text[:200]}"
    rr = c.delete("/api/dashboard/accounts/a1", params={"site": "x"}, headers=h)
    assert rr.status_code == 400, f"删账户 site=x -> {rr.status_code} {rr.text[:200]}"

    read_paths = ["/api/dashboard/summary", "/api/dashboard/daily-stats", "/api/dashboard/trend",
                  "/api/dashboard/accounts", "/api/dashboard/orders",
                  "/api/dashboard/account-ranking", "/api/dashboard/anomalies",
                  "/api/dashboard/user-ranking", "/api/dashboard/novel-stats",
                  "/api/dashboard/account-aliases", "/api/novels/list",
                  "/api/novels/NOPE", "/api/novels/NOPE/chapters"]
    for path in read_paths:
        rr = c.get(path, params={"site": "x"}, headers=h)
        assert rr.status_code < 400 or rr.status_code == 404, \
            f"GET {path}?site=x -> {rr.status_code} {rr.text[:200]}  （读类非法站点应归一为合计而非报错）"
    print("OK: 非法 site —— 写类 400 / 读类归一为合计")


def _check_site_passthrough(main):
    """打桩下游函数，断言 site 透传与空值语义。"""
    import analytics, database, scraper
    seen = []

    def _rec(name):
        def fn(*a, **kw):
            seen.append((name, kw.get("site", "<未传>")))
            return {}
        return fn

    # 1) 看板路由：site='b' 原样透传；site='' 归一为 None（合计）
    stubbed = ["get_summary", "get_orders", "get_novel_stats", "detect_anomalies"]
    orig_analytics = {n: getattr(analytics, n) for n in stubbed}
    for fname in stubbed:
        setattr(analytics, fname, _rec(fname))
    user = {"id": 1, "role": "admin"}
    seen.clear()
    main.api_dashboard_summary(site="b", user=user)
    main.api_dashboard_summary(site="", user=user)
    main.api_dashboard_orders(site="b", user=user)
    main.api_dashboard_novel_stats(site="b", user=user)
    main.api_dashboard_anomalies(site="b", user=user)
    assert seen.count(("get_summary", "b")) == 1, seen
    assert ("get_summary", None) in seen, seen          # 空串 → 合计
    assert ("get_orders", "b") in seen and ("get_novel_stats", "b") in seen, seen
    assert ("detect_anomalies", "b") in seen, seen
    seen.clear()
    main.api_dashboard_summary(site="x", user=user)     # 非法站点 → 读类归一为合计，不是 400
    assert seen == [("get_summary", None)], seen

    # 2) 小说列表：site 透传；空 → None
    orig_books = database.get_novel_books
    database.get_novel_books = lambda *a, **kw: seen.append(("get_novel_books", kw.get("site"))) or {}
    main.api_novel_books_list(site="b", user=user)
    main.api_novel_books_list(site="", user=user)
    database.get_novel_books = orig_books
    assert ("get_novel_books", "b") in seen and ("get_novel_books", None) in seen, seen

    # 3) /api/fetch-novel 打到 B 站内容接口（Ruling 4）
    captured = []
    orig_curl = main._curl_get_text
    main._curl_get_text = lambda url, timeout_sec=60: (
        captured.append(url) or ("<html><body><p>正文</p></body></html>", 200, None))
    main._fetch_novel_content("N1", site="b")
    main._fetch_novel_content("N1")                      # 空 → 默认 A 站
    main._curl_get_text = orig_curl
    assert captured[0].startswith(scraper.site_content_url("b") + "/"), captured[0]
    assert captured[1].startswith(scraper.site_content_url("a") + "/"), captured[1]

    # 4) /api/novels/sync-content：未传 site 回落 DEFAULT_SITE（Ruling 8b）
    orig_sync_content = scraper.sync_all_novel_content
    scraper.sync_all_novel_content = lambda *a, **kw: seen.append(
        ("sync_all_novel_content", kw.get("site"))) or {}
    main.api_novel_sync_content(site="b", user=user)
    main.api_novel_sync_content(site="", user=user)
    scraper.sync_all_novel_content = orig_sync_content
    assert ("sync_all_novel_content", "b") in seen, seen
    assert ("sync_all_novel_content", scraper.DEFAULT_SITE) in seen, seen

    # 5) 同步/重置同步：site 空 = 遍历所有站点；指定站点 = 只跑该站
    class _SyncExec:                                      # 同步执行，避免依赖后台线程
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    all_sites = [s["key"] for s in scraper.get_sites()]
    orig_exec, orig_run, orig_reset = main._EXECUTOR, scraper.run_full_sync, scraper.reset_sync_state
    orig_list = database.list_active_users_with_credentials
    main._EXECUTOR = _SyncExec()
    scraper.run_full_sync = lambda uid, site=None: seen.append(("run_full_sync", site)) or {}
    scraper.reset_sync_state = lambda uid, site=None: seen.append(("reset_sync_state", site))
    database.list_active_users_with_credentials = lambda: [{"id": 1, "username": "admin"}]
    try:
        main._sync_tasks.clear()
        main.api_scraper_sync(site=None, user={"id": 1, "role": "user"})
        got = [s for n, s in seen if n == "run_full_sync"]
        assert got == all_sites, (all_sites, got)

        seen.clear()
        main._sync_tasks.clear()
        main.api_scraper_sync(site="b", user={"id": 1, "role": "user"})
        assert [s for n, s in seen if n == "run_full_sync"] == ["b"], seen

        seen.clear()
        main._sync_tasks.clear()
        main.api_reset_sync_state(site=None, user={"id": 1, "role": "user"})
        assert [s for n, s in seen if n == "reset_sync_state"] == all_sites, seen
        assert [s for n, s in seen if n == "run_full_sync"] == all_sites, seen
    finally:
        main._EXECUTOR, scraper.run_full_sync, scraper.reset_sync_state = orig_exec, orig_run, orig_reset
        database.list_active_users_with_credentials = orig_list
        main._sync_tasks.clear()

    # 6) 用户列表在线徽标：会话键为 (user_id, site)，任一站点在线即在线（Ruling 8a）
    class _FakeSession:
        def check_valid(self):
            return True
    orig_list_users = database.list_users
    database.list_users = lambda: [{"id": 1, "username": "admin", "pingykj_username": "u"}]
    try:
        scraper._user_sessions.clear()
        assert main.api_list_users(user=user)[0]["pingykj_online"] is False
        scraper._user_sessions[(1, "b")] = _FakeSession()
        assert main.api_list_users(user=user)[0]["pingykj_online"] is True, "非默认站点会话也应算在线"
    finally:
        scraper._user_sessions.clear()
        database.list_users = orig_list_users

    # 7) 书籍详情：site 透传，空归一为 None（Ruling 10）
    orig_book = database.get_novel_book
    database.get_novel_book = lambda novel_id, site=None: seen.append(
        ("get_novel_book", site)) or {"novel_id": novel_id}
    main.api_novel_book_detail("N1", site="b", user=user)
    main.api_novel_book_detail("N1", site="", user=user)
    database.get_novel_book = orig_book
    assert ("get_novel_book", "b") in seen, seen
    assert ("get_novel_book", None) in seen, seen

    # 8) 登出：传 site 只清该站点；空 = 传 None（清该用户所有站点，Ruling 11）
    orig_clear = scraper.clear_user_session
    scraper.clear_user_session = lambda uid, site=None: seen.append(("clear_user_session", site))
    main.api_scraper_logout(site="b", user=user)
    main.api_scraper_logout(site="", user=user)
    scraper.clear_user_session = orig_clear
    assert ("clear_user_session", "b") in seen, seen
    assert ("clear_user_session", None) in seen, seen
    assert ("clear_user_session", scraper.DEFAULT_SITE) not in seen, seen

    for n, fn in orig_analytics.items():                 # 还原打桩，避免影响后续 HTTP 检查
        setattr(analytics, n, fn)
    print("OK: site 透传（合计=None / 指定站点 / 空=遍历所有站点）均成立")


def main():
    import main
    c, h = _check_http_accepts_site(main)
    _check_site_passthrough(main)
    _check_invalid_site(main, c, h)


if __name__ == "__main__":
    main()
