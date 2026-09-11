# -*- coding: utf-8 -*-
"""验证三个定时同步任务按站点遍历（Task 7）。

覆盖：`_auto_sync_all_users` / `_auto_full_novel_sync` / `_auto_chapter_sync`
对 `scraper.get_sites()` 里每个站点各跑一次、透传正确的 site；
`_auto_sync_all_users` 的节流游标是 (sync_all, 用户, 站点) 维度；
单个站点抛异常不中断其余站点、且失败站点不会被标记为已同步；
`run_full_sync` 返回 `{"success": False}`（失败不抛异常）时同样不写游标。

全部打桩，不联网。导入 main 会启动 BackgroundScheduler，但任务首次触发在 120 秒后，
本脚本不 sleep、几秒内退出，故不会真的跑到任务（也不主动触发任何网络请求）。
"""
import sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def _install_stubs():
    """打桩 scraper / database 的下游函数，返回 (calls, state, restore)。"""
    import database, scraper

    orig = (
        scraper.run_full_sync, scraper.sync_novel_books, scraper.sync_missing_chapters,
        database.list_active_users_with_credentials,
        database.get_last_sync_date, database.set_last_sync_date,
    )
    calls = {"run_full_sync": [], "sync_novel_books": [], "sync_missing_chapters": [],
             "set_last_sync_date": []}
    state = {"last": None, "raise_on": None, "success": True}  # last = get_last_sync_date 的返回值

    def _run_full_sync(uid, site=None):
        calls["run_full_sync"].append((uid, site))
        if state["raise_on"] == site:
            raise RuntimeError(f"site {site} 故意失败")
        return {"success": state["success"]}

    def _sync_novel_books(uid, full_sync=False, site=None):
        calls["sync_novel_books"].append((uid, site, full_sync))
        if state["raise_on"] == site:
            raise RuntimeError(f"site {site} 故意失败")
        return 3, ""

    def _sync_missing_chapters(uid, site=None):
        calls["sync_missing_chapters"].append((uid, site))
        if state["raise_on"] == site:
            raise RuntimeError(f"site {site} 故意失败")
        return 2, ""

    def _set_last_sync_date(sync_type, date_str, user_id=None, site=None):
        calls["set_last_sync_date"].append((sync_type, user_id, site))

    scraper.run_full_sync = _run_full_sync
    scraper.sync_novel_books = _sync_novel_books
    scraper.sync_missing_chapters = _sync_missing_chapters
    database.list_active_users_with_credentials = lambda: [{"id": 7, "username": "u7"}]
    database.get_last_sync_date = lambda sync_type, user_id=None, site=None: state["last"]
    database.set_last_sync_date = _set_last_sync_date

    def restore():
        (scraper.run_full_sync, scraper.sync_novel_books, scraper.sync_missing_chapters,
         database.list_active_users_with_credentials,
         database.get_last_sync_date, database.set_last_sync_date) = orig

    return calls, state, restore


def _check_auto_sync_all_users(main, calls, state, sites):
    # 1) 从没同步过 → 每个站点各跑一次，并按 (sync_all, 用户, 站点) 写游标
    calls["run_full_sync"].clear()
    calls["set_last_sync_date"].clear()
    state["last"], state["raise_on"] = None, None
    main._auto_sync_all_users()
    assert [s for _, s in calls["run_full_sync"]] == sites, calls["run_full_sync"]
    assert calls["set_last_sync_date"] == [("sync_all", 7, s) for s in sites], calls

    # 2) 节流未到：最近刚同步过 → 整轮跳过、不写游标
    calls["run_full_sync"].clear()
    calls["set_last_sync_date"].clear()
    state["last"] = datetime.now().isoformat()
    main._auto_sync_all_users()
    assert calls["run_full_sync"] == [], calls["run_full_sync"]
    assert calls["set_last_sync_date"] == [], calls["set_last_sync_date"]

    # 3) 节流已过期（久远以前）→ 重新同步所有站点
    calls["run_full_sync"].clear()
    state["last"] = datetime.min.isoformat()
    main._auto_sync_all_users()
    assert [s for _, s in calls["run_full_sync"]] == sites, calls["run_full_sync"]

    # 4) 单站点抛异常：其余站点仍全部执行；失败站点不写游标
    calls["run_full_sync"].clear()
    calls["set_last_sync_date"].clear()
    state["last"], state["raise_on"] = None, sites[0]
    main._auto_sync_all_users()
    state["raise_on"] = None
    assert [s for _, s in calls["run_full_sync"]] == sites, calls["run_full_sync"]
    marked = [t[2] for t in calls["set_last_sync_date"]]
    assert sites[0] not in marked, f"失败站点不该被标记已同步: {marked}"
    assert marked == sites[1:], marked

    # 5) run_full_sync 返回 {"success": False}（失败是返回 dict、不抛异常）→ 不写节流游标，下轮重试
    calls["run_full_sync"].clear()
    calls["set_last_sync_date"].clear()
    state["last"], state["raise_on"], state["success"] = None, None, False
    main._auto_sync_all_users()
    assert [s for _, s in calls["run_full_sync"]] == sites, calls["run_full_sync"]
    assert calls["set_last_sync_date"] == [], \
        f"同步返回 success=False 时不得写节流游标（写了就会被当成已同步）: {calls['set_last_sync_date']}"

    # 6) success=True → 同一路径必须写游标（证明 5) 的断言不是恒真）
    calls["set_last_sync_date"].clear()
    state["success"] = True
    main._auto_sync_all_users()
    assert calls["set_last_sync_date"] == [("sync_all", 7, s) for s in sites], \
        calls["set_last_sync_date"]

    print("OK: _auto_sync_all_users —— 按站点遍历 / (sync_all,用户,站点) 节流 / "
          "单站点失败隔离 / success=False 不写游标")


def _check_full_novel_sync(main, calls, state, sites):
    calls["sync_novel_books"].clear()
    state["raise_on"] = None
    main._auto_full_novel_sync()
    assert [(s, f) for _, s, f in calls["sync_novel_books"]] == [(s, True) for s in sites], \
        calls["sync_novel_books"]

    # 单站点异常不影响其余站点
    calls["sync_novel_books"].clear()
    state["raise_on"] = sites[0]
    main._auto_full_novel_sync()
    state["raise_on"] = None
    assert [s for _, s, _ in calls["sync_novel_books"]] == sites, calls["sync_novel_books"]
    print("OK: _auto_full_novel_sync —— 按站点遍历（full_sync=True）/ 单站点失败隔离")


def _check_chapter_sync(main, calls, state, sites):
    calls["sync_missing_chapters"].clear()
    state["raise_on"] = None
    main._auto_chapter_sync()
    assert [s for _, s in calls["sync_missing_chapters"]] == sites, calls["sync_missing_chapters"]

    calls["sync_missing_chapters"].clear()
    state["raise_on"] = sites[0]
    main._auto_chapter_sync()
    state["raise_on"] = None
    assert [s for _, s in calls["sync_missing_chapters"]] == sites, calls["sync_missing_chapters"]
    print("OK: _auto_chapter_sync —— 按站点遍历 / 单站点失败隔离")


def main():
    import main as main_mod
    import scraper

    sites = [s["key"] for s in scraper.get_sites()]
    assert len(sites) >= 2, f"至少要有两个站点才能验证遍历，实际: {sites}"

    calls, state, restore = _install_stubs()
    try:
        _check_auto_sync_all_users(main_mod, calls, state, sites)
        _check_full_novel_sync(main_mod, calls, state, sites)
        _check_chapter_sync(main_mod, calls, state, sites)
    finally:
        restore()
    print(f"ALL OK: 定时任务按站点遍历（站点={sites}）")


if __name__ == "__main__":
    main()
