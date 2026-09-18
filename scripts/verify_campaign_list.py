# -*- coding: utf-8 -*-
"""验证：广告系列列表要看得见「刚建出来、Meta 还没给数据」的系列。

原来的 bug：`analytics.meta_campaigns` 完全由 meta_adset_stats 聚合而来
（子查询 GROUP BY campaign_id），于是一个**从没有过统计数据的系列在列表里
根本不存在**。投放向导建出来的系列正好全是这种：Meta 那边一条数据都没有
（PAUSED 的更是一辈子不会有），名字只在本地 delivery_campaigns 里。
实测线上：向导建过的 33 个系列 100% 落在这个盲区里。

修法：把「本地建过、且一条统计都没有」的系列补进列表（全 0 指标），
名字/账户/归属人用本地的，状态徽章照旧 LEFT JOIN meta_entity_status。

覆盖：
  1. 没有统计的系列要出现在列表里（指标全 0、名字/账户/归属人来自本地）
  2. 有统计的系列照旧，数字不受影响
  3. **不重复**：本地有记录、同时也已有统计的系列只出现一次（用统计那份）
  4. 账户筛选对补进来的行也生效（别的账户的不能串进来）
  5. 用户筛选对补进来的行也生效，且 **LEFT JOIN 不能退化成 INNER JOIN**
     （把 user_id 写进 WHERE 会让没状态记录的系列整行消失 —— 正是要修的东西）
  6. 状态徽章：有 meta_entity_status 记录时带出来，没有时空着但不能丢行
  7. 静态：前端默认筛选是「投放中」，且下拉框的 selected 与 _campStatusFilter 一致
     （这两处必须一起改，只改一个就会出现「显示投放中、实际筛全部」）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

ACT_A, ACT_B = "act_aaa", "act_bbb"


def setup_db(tmp):
    import database
    database.DB_PATH = tmp / "dashboard.db"
    database.init_db()
    return database


def add_stat(conn, campaign_id, name, act, date, spend, user_id=1, adset_id=None):
    conn.execute(
        "INSERT INTO meta_adset_stats (date, ad_account, campaign_id, campaign_name, "
        "adset_id, adset_name, spend, impressions, clicks, purchases, purchase_value, user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (date, act, campaign_id, name, adset_id or campaign_id + "_as", "组", spend,
         1000, 10, 1, spend * 2, user_id))


def add_local_campaign(conn, fb_id, name, act, user_id=1):
    conn.execute(
        "INSERT INTO delivery_campaigns (name, ad_account_id, fb_campaign_id, user_id) "
        "VALUES (?,?,?,?)", (name, act, fb_id, user_id))


def main():
    import analytics
    tmp = Path(tempfile.mkdtemp(prefix="camp_list_"))
    database = setup_db(tmp)

    with database.get_conn() as conn:
        # 有数据的系列（正常展示）
        add_stat(conn, "c_has_data", "跑着的系列", ACT_A, "2026-09-15", 100.0)
        # 向导刚建的：本地有名字，Meta 一条统计都没有 → 以前看不见
        add_local_campaign(conn, "c_new", "9.16-2057723374815436802-1", ACT_A)
        # 本地有记录、但同时也已经有统计了 → 只能出现一次
        add_local_campaign(conn, "c_has_data", "重复的本地记录", ACT_A)
        # 别的账户的本地系列（账户筛选不该把它带进来）
        add_local_campaign(conn, "c_other_act", "别的账户的系列", ACT_B)
        # 别的用户的本地系列（用户筛选不该把它带进来）
        add_local_campaign(conn, "c_other_user", "别的用户的系列", ACT_A, user_id=9)
        # 带状态记录的本地系列
        add_local_campaign(conn, "c_new_active", "刚建好·开着", ACT_A)
        conn.execute("INSERT INTO meta_entity_status (level, entity_id, ad_account, "
                     "effective_status, status, user_id) VALUES ('campaign',?,?,?,?,1)",
                     ("c_new_active", ACT_A, "ACTIVE", "ACTIVE"))

    # ---- 1~3) 全量（不限用户，模拟管理员）----
    rows = analytics.meta_campaigns(account=ACT_A)
    by_id = {r["campaign_id"]: r for r in rows}
    ids = [r["campaign_id"] for r in rows]
    assert len(ids) == len(set(ids)), f"同一个系列出现了多次：{ids}"

    assert "c_new" in by_id, f"没统计的新系列必须出现，实际只有 {ids}"
    n = by_id["c_new"]
    assert n["campaign_name"] == "9.16-2057723374815436802-1", n["campaign_name"]
    assert n["ad_account"] == ACT_A and n["spend"] == 0 and n["roi"] == 0, n
    for k in ("impressions", "clicks", "purchases", "purchase_value", "cpm", "ctr", "cpa"):
        assert n[k] == 0, f"没有数据的系列 {k} 应该是 0：{n}"

    assert "c_has_data" in by_id and by_id["c_has_data"]["spend"] == 100.0, by_id.get("c_has_data")
    assert ids.count("c_has_data") == 1, "有统计的系列不能被补的那份重复一遍"
    assert by_id["c_has_data"]["campaign_name"] == "跑着的系列", \
        "有统计时应该用 Meta 那边的名字，不是本地那份"

    # 新建的排在最前（默认按消耗排序时它们会沉到 0 那堆里，很难找）
    assert rows[0]["campaign_id"] in ("c_new", "c_new_active"), \
        f"新系列应该排在前面，实际第一行是 {rows[0]['campaign_id']}"

    # ---- 4) 账户筛选 ----
    assert "c_other_act" not in by_id, "别的账户的本地系列不该出现"
    rows_b = analytics.meta_campaigns(account=ACT_B)
    assert [r["campaign_id"] for r in rows_b] == ["c_other_act"], \
        f"ACT_B 应该只看到自己那条：{[r['campaign_id'] for r in rows_b]}"
    rows_both = analytics.meta_campaigns(account=f"{ACT_A},{ACT_B}")
    assert {r["campaign_id"] for r in rows_both} >= {"c_new", "c_other_act"}, \
        "多账户时两边都该出现"

    # ---- 5) 用户筛选：既不能串号，也不能把没状态记录的行整行筛没 ----
    rows_u1 = analytics.meta_campaigns(account=ACT_A, user_id=1)
    ids_u1 = [r["campaign_id"] for r in rows_u1]
    assert "c_other_user" not in ids_u1, "不该看到别的用户的系列"
    assert "c_new" in ids_u1, \
        ("user_id 条件写进 WHERE 了 —— LEFT JOIN 退化成 INNER JOIN，"
         "没有状态记录的系列会整行消失")
    rows_u9 = analytics.meta_campaigns(account=ACT_A, user_id=9)
    assert "c_other_user" in [r["campaign_id"] for r in rows_u9], "自己的要看得见"

    # ---- 6) 状态徽章 ----
    assert by_id["c_new_active"]["effective_status"] == "ACTIVE", \
        f"有状态记录的要把状态带出来（前端「投放中」筛选靠它）：{by_id['c_new_active']}"
    assert by_id["c_new"]["effective_status"] == "", "没状态记录的留空，但不能丢行"

    # 用户对不上时只丢状态、不丢行（ON 里过滤的正确语义）
    rows_u9b = {r["campaign_id"]: r for r in analytics.meta_campaigns(account=ACT_A, user_id=9)}
    assert "c_new_active" not in rows_u9b or rows_u9b["c_new_active"]["effective_status"] == "", \
        "状态记录属于 user 1，user 9 看时不该串出别人的状态"

    # ---- 7) 前端默认筛选 ----
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '<option value="ACTIVE" selected>投放中</option>' in html, \
        "下拉框的默认项没改成「投放中」"
    assert "var _campStatusFilter = 'ACTIVE';" in html, \
        "默认筛选变量没跟着改 —— 会出现「框里显示投放中、实际筛的全部」"
    assert "campFilterEmpty" in html, "全被筛掉时的提示没了（默认筛投放中时最容易撞上）"

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 广告系列列表（没统计的新系列看得见且指标全 0 / 有统计的不受影响 / 不重复 / "
          "账户与用户筛选都生效且 LEFT JOIN 不退化 / 状态徽章带得出来 / "
          "前端默认筛「投放中」且两处一致 / 全筛掉时有提示）")


if __name__ == "__main__":
    main()
