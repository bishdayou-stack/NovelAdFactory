# -*- coding: utf-8 -*-
"""验证：凡是「今天」都按北京日历算，不取服务器本地时间。

背景：服务器时区是 UTC（宝塔机器默认如此），而 `time.strftime("%Y-%m-%d")` 给的是本地时间。
北京时间 0-8 点这段时间，两者差一天，于是：
  书城同步请求 statDate_end=<昨天> → 当天的行永远拉不到
  看板默认筛选是「今天」→ 一行都没匹配上 → KPI 全 0
  北京 8:00（UTC 0:00）日期翻页后才恢复
Meta 那条路径当初就是这么坏的（提交 3e74644），书城这条漏改了。

做法：把 `database.datetime.utcnow()` 冻结在一个刻意远离真实日期的时刻
（真实日期 +3 天），再用 `time.strftime` 取真实本地日期做对照。
「用了 bj_now()」和「用了服务器本地时间」会给出不同答案，断言因此有区分度。
"""
import datetime as _dt
import inspect
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

# 冻结时刻：北京日期 = 真实本地日期 +3 天（保证与真实日期不同，断言才有区分度）
REAL_TODAY = time.strftime("%Y-%m-%d")
BJ_TODAY_DT = _dt.datetime.strptime(REAL_TODAY, "%Y-%m-%d") + _dt.timedelta(days=3)
BJ_TODAY = BJ_TODAY_DT.strftime("%Y-%m-%d")
assert BJ_TODAY != REAL_TODAY, "冻结日期必须与真实日期不同，否则测不出错"


class _FrozenDateTime(_dt.datetime):
    """utcnow() 冻结；+8 小时正好落在 BJ_TODAY"""
    @classmethod
    def utcnow(cls):
        return BJ_TODAY_DT - _dt.timedelta(hours=8)


class FakeSession:
    _token = "fake-token"
    base_url = "http://fake"
    content_url = "http://fake"

    def __init__(self):
        self.calls = []

    def _fetch_with_token(self, path, params, page_size=500, date_start=None, date_end=None, **kw):
        self.calls.append({"date_start": date_start, "date_end": date_end})
        return [], "stop-after-capture"


def main():
    import database
    tmp = Path(tempfile.mkdtemp(prefix="bj_dates_")) / "dashboard.db"
    database.DB_PATH = tmp
    database.init_db()
    database.datetime = _FrozenDateTime

    # 1) 时钟与助手函数
    assert test_time_is_frozen(), "冻结失败：time.strftime 应仍是真实日期"
    assert database.bj_now().strftime("%Y-%m-%d") == BJ_TODAY, \
        f"bj_now 应给北京日期 {BJ_TODAY}: {database.bj_now()}"

    import scraper
    assert scraper._bj_now().strftime("%Y-%m-%d") == BJ_TODAY, \
        "scraper._bj_now 应与 database.bj_now 同一实现（不是自己算一份）"

    # 2) 三个同步入口：请求的日期终点必须是北京今天
    sessions = {}

    def fake_get_session(user_id, site):
        key = (user_id, site)
        sessions.setdefault(key, FakeSession())
        return sessions[key], None

    scraper._get_or_create_session = fake_get_session
    database.get_last_sync_date = lambda *a, **kw: REAL_TODAY  # 让增量分支生效

    captured_urls = []  # 书籍列表走 _curl_get，日期拼在 URL 上

    def fake_curl_get(url, headers=None, cookies=None, timeout=30):
        captured_urls.append(url)
        return {}, 200, None

    scraper._curl_get = fake_curl_get

    for name, fn, via_url in (
        ("sync_ads", lambda: scraper.sync_ads(1, "a"), False),
        ("sync_orders", lambda: scraper.sync_orders(1, "a"), False),
        ("sync_novel_books", lambda: scraper.sync_novel_books(1, False, "a"), True),
    ):
        sessions.clear()
        captured_urls.clear()
        fn()
        if via_url:
            assert [u for u in captured_urls if f"updateTime_end={BJ_TODAY}" in u], \
                f"{name}: 书籍请求的 updateTime_end 不是北京今天({BJ_TODAY})，实际 {captured_urls}"
        else:
            ends = [c["date_end"] for s in sessions.values() for c in s.calls]
            assert ends, f"{name}: 没有发出带日期的请求"
            bad = [e for e in ends if BJ_TODAY not in str(e)]
            assert not bad, f"{name}: 请求日期终点不是北京今天({BJ_TODAY})，实际 {bad}"

    # 3) 看板区间按北京日历（临时库里放北京今天、前天、以及一周前的行）
    dates = [(BJ_TODAY_DT - _dt.timedelta(days=n)).strftime("%Y-%m-%d") for n in (0, 1, 6)]
    with database.get_conn() as conn:
        for d in dates:
            conn.execute(
                "INSERT INTO ad_daily_stats (date, ad_account, total_spend, source, user_id, site) "
                "VALUES (?, ?, 10, 'pingykj', 1, 'a')", (d, "acc_" + d))
    import analytics
    got = sorted(r["date"] for r in analytics.get_trend(days=1, user_id=1, site="a"))
    assert got == sorted(dates[:2]), f"get_trend(days=1) 应只含北京今天和昨天 {dates[:2]}，实际 {got}"

    # SQL 里的 date('now') 用的是 UTC 时钟，冻不住也测不出，只能用源码兜住
    for fn in (analytics.get_trend, analytics.detect_anomalies, analytics.meta_trend):
        assert "date('now'" not in inspect.getsource(fn), \
            f"{fn.__name__} 还在用 SQLite 的 date('now')（UTC 时钟），北京 0-8 点会少算今天"

    # 4) 小说消耗快照的日期
    n = database.save_novel_spend_snapshots([{"novel_id": "n1", "book_ad_spend": 5}], site="a")
    with database.get_conn() as conn:
        snap = [r["snap_date"] for r in conn.execute("SELECT snap_date FROM novel_spend_snapshots")]
    assert n == 1 and snap == [BJ_TODAY], f"快照日期应为北京今天 {BJ_TODAY}，实际 {snap}"

    shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"OK: 同步/看板/快照的「今天」都按北京日历（冻结在 {BJ_TODAY}，真实日期 {REAL_TODAY}）")


def test_time_is_frozen() -> bool:
    """确认 time.strftime 拿到的仍是真实本地日期（没被我们误伤）"""
    return time.strftime("%Y-%m-%d") == REAL_TODAY


if __name__ == "__main__":
    main()
