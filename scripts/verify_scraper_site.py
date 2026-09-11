# -*- coding: utf-8 -*-
"""验证 scraper 会话按 (user_id, site) 分开、URL 按站点取。"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))
import scraper

def main():
    scraper._user_sessions.clear()
    s_a = scraper.ScraperSession("u", "p", "p", site="a")
    s_b = scraper.ScraperSession("u", "p", "p", site="b")
    assert s_a.base_url == "https://hw.manage.pingykj.com", s_a.base_url
    assert s_b.base_url == "https://manage.relishnovel.com", s_b.base_url
    scraper._user_sessions[(1, "a")] = s_a
    scraper._user_sessions[(1, "b")] = s_b
    assert len(scraper._user_sessions) == 2, "同名用户的两站点会话必须分开"

    # 章节内容：B 站必须打到 B 站内容域，且落库带 site='b'（不联网，桩掉 curl 与写库）
    calls = []
    orig_curl, orig_upsert = scraper._curl_get, scraper.database.upsert_novel_chapters
    scraper._curl_get = lambda url, **kw: (calls.append(url) or "", 200, None)
    scraper.database.upsert_novel_chapters = lambda rows, site=None: calls.append(site) or 0
    try:
        scraper.sync_novel_chapters("n1", site="b")
    finally:
        scraper._curl_get, scraper.database.upsert_novel_chapters = orig_curl, orig_upsert
    assert calls[0].startswith("https://manage.api.relishnovel.com"), calls[0]
    assert calls[1] == "b", calls[1]

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

    # 且 _get_or_create_session 自动登录时确实取的是生效凭据（桩掉网络登录）
    scraper._user_sessions.clear()
    orig_login = scraper.ScraperSession.login
    scraper.ScraperSession.login = lambda self: (True, "")
    try:
        sess, err = scraper._get_or_create_session(uid, "b")
    finally:
        scraper.ScraperSession.login = orig_login
    assert sess is not None, err
    assert sess.pingykj_username == "b_user", sess.pingykj_username
    scraper._user_sessions.clear()

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("OK: 站点凭据解析——B 站专属、A 站回落通用")

    print("OK: 会话按 (user, site) 分开，URL 与落库均按站点取")

if __name__ == "__main__":
    main()
