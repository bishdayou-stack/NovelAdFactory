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

    print("OK: 会话按 (user, site) 分开，URL 与落库均按站点取")

if __name__ == "__main__":
    main()
