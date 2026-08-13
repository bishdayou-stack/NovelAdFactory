from typing import Optional, List, Dict, Any
import database


def _keyword_clause() -> str:
    """返回在 extra_data JSON 中搜索 campaignName / adName 的 SQL 片段"""
    return """ AND (
        json_extract(extra_data, '$.campaignName') LIKE '%' || ? || '%'
        OR json_extract(extra_data, '$.adName') LIKE '%' || ? || '%'
        OR json_extract(extra_data, '$.adsetName') LIKE '%' || ? || '%'
    )"""


def _add_keyword(where: List[str], params: List, keyword: str) -> None:
    if keyword:
        where.append(f"""(
            json_extract(extra_data, '$.campaignName') LIKE '%' || ? || '%'
            OR json_extract(extra_data, '$.adName') LIKE '%' || ? || '%'
            OR json_extract(extra_data, '$.adsetName') LIKE '%' || ? || '%'
        )""")
        params.extend([keyword, keyword, keyword])


def _add_user_filter(where: List[str], params: List, user_id: int, prefix: str = "",
                     exclude_paused_meta: bool = False):
    """添加 user_id 过滤条件；可选排除已停用的 Meta 账户（历史数据也不统计）"""
    if user_id is not None:
        col = f"{prefix}user_id" if prefix else "user_id"
        where.append(f"{col} = ?")
        params.append(user_id)
        if exclude_paused_meta:
            where.append("""ad_account NOT IN (
                SELECT act_id FROM meta_accounts WHERE user_id = ? AND status != 'active'
            )""")
            params.append(user_id)
    # user_id=None 表示管理员看全部，不加过滤


# ====== KPI 汇总 ======

def get_summary(start_date: str = None, end_date: str = None, account: str = None,
                keyword: str = None, user_id: int = None) -> Dict[str, Any]:
    with database.get_conn() as conn:
        where = ["(source IS NULL OR source != 'meta')"]
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        if account:
            where.append("ad_account = ?")
            params.append(account)
        _add_keyword(where, params, keyword)
        _add_user_filter(where, params, user_id)

        sql = f"""
            SELECT
                COALESCE(SUM(total_spend), 0) AS total_spend,
                COALESCE(SUM(total_revenue), 0) AS total_revenue,
                COUNT(DISTINCT date) AS active_days,
                COUNT(DISTINCT ad_account) AS account_count,
                COALESCE(SUM(ad_count), 0) AS total_ads
            FROM ad_daily_stats
            WHERE {' AND '.join(where)}
        """
        row = conn.execute(sql, params).fetchone()

        order_where = ["status = '成功'"]
        order_params = []
        if start_date:
            order_where.append("date(order_date) >= ?")
            order_params.append(start_date)
        if end_date:
            order_where.append("date(order_date) <= ?")
            order_params.append(end_date)
        if account:
            order_where.append("ad_account = ?")
            order_params.append(account)
        if keyword:
            order_where.append("""(
                json_extract(extra_data, '$.campaignName') LIKE '%' || ? || '%'
                OR json_extract(extra_data, '$.adName') LIKE '%' || ? || '%'
            )""")
            order_params.extend([keyword, keyword])
        _add_user_filter(order_where, order_params, user_id)

        order_row = conn.execute(
            f"SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total_amount FROM orders WHERE {' AND '.join(order_where)}",
            order_params
        ).fetchone()

        total_spend = row["total_spend"] or 0
        total_revenue = order_row["total_amount"] or 0
        order_count = order_row["cnt"] or 0

        roi = round(total_revenue / total_spend, 2) if total_spend > 0 else 0
        cpa = round(total_spend / order_count, 2) if order_count > 0 else 0

        return {
            "total_spend": round(total_spend, 2),
            "total_revenue": round(total_revenue, 2),
            "roi": roi,
            "order_count": order_count,
            "cpa": cpa,
            "active_days": row["active_days"] or 0,
            "account_count": row["account_count"] or 0,
            "total_ads": row["total_ads"] or 0,
        }


# ====== 日报明细 ======

def get_daily_stats(start_date: str = None, end_date: str = None, account: str = None,
                    keyword: str = None, order_by: str = "date",
                    page: int = 1, page_size: int = 20, user_id: int = None) -> dict:
    """返回 {"data": [...], "total": N, "page": 1, "page_size": 20}"""
    with database.get_conn() as conn:
        where = ["(source IS NULL OR source != 'meta')"]
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        if account:
            where.append("ad_account = ?")
            params.append(account)
        _add_keyword(where, params, keyword)
        _add_user_filter(where, params, user_id)

        allowed_order = {"date", "ad_account", "total_spend", "total_revenue"}
        if order_by not in allowed_order:
            order_by = "date"

        where_clause = ' AND '.join(where)

        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM ad_daily_stats WHERE {where_clause}", params
        ).fetchone()
        total = total_row["cnt"]

        offset = (page - 1) * page_size
        sql = f"""
            SELECT a.date, a.ad_account, a.total_spend, a.total_revenue,
                   CASE WHEN a.total_spend > 0 THEN ROUND(a.total_revenue / a.total_spend, 2) ELSE 0 END AS roi,
                   a.ad_count, a.impressions, a.clicks, a.user_id,
                   CASE WHEN u.display_name IS NOT NULL AND u.display_name != '' THEN u.display_name ELSE COALESCE(u.username, '') END AS user_name
            FROM ad_daily_stats a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {where_clause}
            ORDER BY {order_by} DESC, a.ad_account
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()

        aliases = database.get_account_aliases(user_id)
        results = []
        for r in rows:
            d = dict(r)
            acct = d.get("ad_account", "")
            d["account_display"] = aliases.get(acct, acct)
            results.append(d)
        return {"data": results, "total": total, "page": page, "page_size": page_size}


# ====== 趋势数据 ======

def get_trend(days: int = 30, account: str = None, keyword: str = None,
              user_id: int = None) -> List[Dict[str, Any]]:
    with database.get_conn() as conn:
        where = ["(source IS NULL OR source != 'meta')", "date >= date('now', ?)"]
        params = [f"-{days} days"]
        if account:
            where.append("ad_account = ?")
            params.append(account)
        _add_keyword(where, params, keyword)
        _add_user_filter(where, params, user_id)

        sql = f"""
            SELECT date, SUM(total_spend) AS spend, SUM(total_revenue) AS revenue,
                   CASE WHEN SUM(total_spend) > 0 THEN ROUND(SUM(total_revenue) / SUM(total_spend), 2) ELSE 0 END AS roi
            FROM ad_daily_stats
            WHERE {' AND '.join(where)}
            GROUP BY date
            ORDER BY date
        """
        rows = conn.execute(sql, params).fetchall()

    data = [dict(r) for r in rows]
    for i, item in enumerate(data):
        window = data[max(0, i - 6): i + 1]
        n = len(window)
        item["spend_ma7"] = round(sum(w["spend"] for w in window) / n, 2) if n > 0 else 0
        item["revenue_ma7"] = round(sum(w["revenue"] for w in window) / n, 2) if n > 0 else 0
    return data


# ====== 账户列表 ======

def get_accounts(user_id: int = None) -> list:
    """返回账户列表，含别名"""
    return database.get_account_display_list(user_id)


def _account_display(account_id: str, user_id: int = None) -> str:
    """将账户 ID 转为可读名称（别名 或 ID + 计划名）"""
    aliases = database.get_account_aliases(user_id)
    if account_id in aliases:
        return aliases[account_id]
    return account_id


# ====== 账户排名 ======

def get_account_ranking(start_date: str = None, end_date: str = None,
                         keyword: str = None, page: int = 1, page_size: int = 20,
                         user_id: int = None) -> dict:
    with database.get_conn() as conn:
        where = ["(source IS NULL OR source != 'meta')"]
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        _add_keyword(where, params, keyword)
        _add_user_filter(where, params, user_id)
        where_clause = ' AND '.join(where)

        total = conn.execute(
            f"SELECT COUNT(DISTINCT ad_account) AS cnt FROM ad_daily_stats WHERE {where_clause}", params
        ).fetchone()["cnt"]

        offset = (page - 1) * page_size
        sql = f"""
            SELECT a.ad_account, SUM(a.total_spend) AS spend, SUM(a.total_revenue) AS revenue,
                   CASE WHEN SUM(a.total_spend) > 0 THEN ROUND(SUM(a.total_revenue) / SUM(a.total_spend), 2) ELSE 0 END AS roi,
                   SUM(a.ad_count) AS total_ads, a.user_id,
                   CASE WHEN u.display_name IS NOT NULL AND u.display_name != '' THEN u.display_name ELSE COALESCE(u.username, '') END AS user_name
            FROM ad_daily_stats a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {where_clause}
            GROUP BY a.ad_account, a.user_id
            ORDER BY spend DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()
        aliases = database.get_account_aliases(user_id)
        results = []
        for r in rows:
            d = dict(r)
            acct = d.get("ad_account", "")
            d["account_display"] = aliases.get(acct, acct)
            results.append(d)
        return {"data": results, "total": total, "page": page, "page_size": page_size}


# ====== 异常检测 ======

def detect_anomalies(days: int = 30, threshold_sigma: float = 2.0,
                     user_id: int = None) -> List[Dict[str, Any]]:
    with database.get_conn() as conn:
        where = ["(source IS NULL OR source != 'meta')", "date >= date('now', ?)"]
        params = [f"-{days} days"]
        _add_user_filter(where, params, user_id, exclude_paused_meta=True)
        rows = conn.execute(f"""
            SELECT date, SUM(total_spend) AS spend
            FROM ad_daily_stats
            WHERE {' AND '.join(where)}
            GROUP BY date ORDER BY date
        """, params).fetchall()

    if len(rows) < 5:
        return []

    spends = [r["spend"] for r in rows]
    mean = sum(spends) / len(spends)
    variance = sum((s - mean) ** 2 for s in spends) / len(spends)
    std = variance ** 0.5
    threshold = mean + threshold_sigma * std

    anomalies = []
    for r in rows:
        if r["spend"] > threshold:
            anomalies.append({
                "date": r["date"],
                "spend": r["spend"],
                "mean": round(mean, 2),
                "threshold": round(threshold, 2),
                "deviation_pct": round((r["spend"] - mean) / mean * 100, 1) if mean > 0 else 0,
            })
    return anomalies


# ====== 订单查询 ======

def get_orders(start_date: str = None, end_date: str = None, keyword: str = None,
               page: int = 1, page_size: int = 15, user_id: int = None) -> dict:
    with database.get_conn() as conn:
        where = ["status = '成功'"]
        params = []
        if start_date:
            where.append("date(order_date) >= ?")
            params.append(start_date)
        if end_date:
            where.append("date(order_date) <= ?")
            params.append(end_date)
        if keyword:
            where.append("""(
                json_extract(extra_data, '$.campaignName') LIKE '%' || ? || '%'
                OR json_extract(extra_data, '$.adName') LIKE '%' || ? || '%'
            )""")
            params.extend([keyword, keyword])
        _add_user_filter(where, params, user_id)

        where_clause = ' AND '.join(where)
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM orders WHERE {where_clause}", params
        ).fetchone()["cnt"]

        offset = (page - 1) * page_size
        sql = f"""
            SELECT o.order_id, o.order_date, o.amount, o.status, o.ad_account, o.synced_at,
                   json_extract(o.extra_data, '$.campaignLinkId_dictText') AS promotion_link_name,
                   json_extract(o.extra_data, '$.adId') AS ad_id,
                   json_extract(o.customer_info, '$.novelName') AS novel_name,
                   json_extract(o.customer_info, '$.novelId') AS novel_id,
                   o.user_id, CASE WHEN u.display_name IS NOT NULL AND u.display_name != '' THEN u.display_name ELSE COALESCE(u.username, '') END AS user_name
            FROM orders o
            LEFT JOIN users u ON o.user_id = u.id
            WHERE {where_clause}
            ORDER BY o.order_date DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ====== 小说订单汇总 ======

def get_novel_stats(start_date: str = None, end_date: str = None,
                    keyword: str = None, user_id: int = None,
                    sort_by: str = "order_count",
                    page: int = 1, page_size: int = 20) -> dict:
    """按小说汇总订单，默认按订单量降序，支持翻页"""
    import json as _json
    with database.get_conn() as conn:
        where = ["status = '成功'"]
        params = []
        if start_date:
            where.append("date(order_date) >= ?")
            params.append(start_date)
        if end_date:
            where.append("date(order_date) <= ?")
            params.append(end_date)
        _add_user_filter(where, params, user_id)

        sql = f"""
            SELECT customer_info, amount
            FROM orders
            WHERE {' AND '.join(where)}
        """
        rows = conn.execute(sql, params).fetchall()

        groups: Dict[str, Dict] = {}
        for r in rows:
            ci = r["customer_info"]
            if not ci:
                continue
            try:
                inner = _json.loads(ci)
                if isinstance(inner, str):
                    inner = _json.loads(inner)
            except (_json.JSONDecodeError, TypeError):
                continue

            nid = str(inner.get("novelId", "") or "")
            name = str(inner.get("novelName", "") or "")
            if not nid and not name:
                continue

            if keyword:
                kw = keyword.lower()
                if kw not in name.lower() and kw not in nid.lower():
                    continue

            key = nid or name
            if key not in groups:
                groups[key] = {"novel_id": nid, "novel_name": name, "order_count": 0, "total_amount": 0.0}
            groups[key]["order_count"] += 1
            groups[key]["total_amount"] += r["amount"] or 0

        result = list(groups.values())

        # 补充 book_ad_spend 和 conversion_cost
        # - 总排行（无时间过滤）：转化成本 = 累计消耗 / 总订单数
        # - 7日排行（有 start_date）：转化成本 = 近7天消耗 / 近7天订单数
        #   近7天消耗 = 当前累计消耗 - 7天前的快照值
        novel_ids = [r["novel_id"] for r in result if r["novel_id"]]
        if novel_ids:
            placeholders = ",".join("?" for _ in novel_ids)
            spend_rows = conn.execute(
                f"SELECT novel_id, book_ad_spend FROM novel_books WHERE novel_id IN ({placeholders})",
                novel_ids
            ).fetchall()
            spend_map = {r["novel_id"]: (r["book_ad_spend"] or 0) for r in spend_rows}

            # 查询每本书的订单总数（不限时间范围，用于总排行）
            _uid_filter = user_id
            total_where = ["status = '成功'"]
            total_params = []
            if _uid_filter:
                total_where.append("user_id = ?")
                total_params.append(_uid_filter)
            total_rows = conn.execute(
                f"""SELECT json_extract(customer_info, '$.novelId') AS nid, COUNT(*) AS total_cnt
                    FROM orders WHERE {' AND '.join(total_where)}
                    GROUP BY json_extract(customer_info, '$.novelId')""",
                total_params
            ).fetchall()
            total_order_map = {r["nid"]: r["total_cnt"] for r in total_rows if r["nid"]}

            # 近7天消耗：从快照表取 start_date 前一天的累计值，用当前值减它
            has_date_range = bool(start_date)
            for r in result:
                nid = r["novel_id"]
                book_ad_spend = spend_map.get(nid, 0)
                total_orders = total_order_map.get(nid, r["order_count"])
                r["book_ad_spend"] = book_ad_spend

                if has_date_range and start_date:
                    # 取 start_date 前一天或更早的快照值
                    from datetime import datetime as _dt, timedelta as _td
                    snap_before = (_dt.strptime(start_date, "%Y-%m-%d") - _td(days=1)).strftime("%Y-%m-%d")
                    prev_spend = database.get_novel_spend_snapshot(nid, snap_before)
                    if prev_spend is not None and prev_spend > 0:
                        recent_spend = max(0, book_ad_spend - prev_spend)
                        r["recent_spend"] = round(recent_spend, 2)
                        r["conversion_cost"] = round(recent_spend / r["order_count"], 2) if recent_spend > 0 and r["order_count"] > 0 else None
                    else:
                        # 无快照数据，回退到累计消耗 / 总订单数
                        r["recent_spend"] = None
                        denominator = total_orders or r["order_count"]
                        r["conversion_cost"] = round(book_ad_spend / denominator, 2) if book_ad_spend > 0 and denominator > 0 else None
                else:
                    # 总排行：使用累计消耗 / 总订单数
                    r["recent_spend"] = None
                    denominator = total_orders or r["order_count"]
                    r["conversion_cost"] = round(book_ad_spend / denominator, 2) if book_ad_spend > 0 and denominator > 0 else None
        else:
            for r in result:
                r["book_ad_spend"] = 0
                r["recent_spend"] = None
                r["conversion_cost"] = None

        if sort_by == "conversion_cost":
            result.sort(key=lambda x: (x["conversion_cost"] is None, x["conversion_cost"] or 0))
        elif sort_by == "total_amount":
            result.sort(key=lambda x: x["total_amount"], reverse=True)
        else:
            result.sort(key=lambda x: x["order_count"], reverse=True)

        total = len(result)
        offset = (page - 1) * page_size
        paged = result[offset:offset + page_size]
        return {"data": paged, "total": total, "page": page, "page_size": page_size}


# ====== Meta 数据看板（source='meta'，含 Meta 特有指标） ======

def _meta_where(where, params, start_date, end_date, account, keyword):
    where.append("source = 'meta'")
    if start_date:
        where.append("date >= ?"); params.append(start_date)
    if end_date:
        where.append("date <= ?"); params.append(end_date)
    if account:
        accts = [a for a in account.split(",") if a]
        if len(accts) > 1:
            where.append("ad_account IN (" + ",".join(["?"] * len(accts)) + ")")
            params.extend(accts)
        else:
            where.append("ad_account = ?"); params.append(accts[0] if accts else account)
    if keyword:
        where.append("(ad_account LIKE ? OR meta_account_id LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])


def meta_summary(start_date=None, end_date=None, account=None, keyword=None,
                 user_id=None):
    with database.get_conn() as conn:
        where, params = [], []
        _meta_where(where, params, start_date, end_date, account, keyword)
        _add_user_filter(where, params, user_id, exclude_paused_meta=True)
        row = conn.execute(f"""
            SELECT COALESCE(SUM(total_spend),0) AS spend, COALESCE(SUM(total_revenue),0) AS revenue,
                   COUNT(DISTINCT date) AS days, COUNT(DISTINCT ad_account) AS accounts,
                   COALESCE(SUM(ad_count),0) AS ads, COALESCE(SUM(impressions),0) AS impressions,
                   COALESCE(SUM(clicks),0) AS clicks, COALESCE(SUM(inline_link_clicks),0) AS link_clicks,
                   COALESCE(SUM(purchases),0) AS purchases, COALESCE(SUM(add_to_cart),0) AS add_to_cart,
                   COALESCE(SUM(subscribe_count),0) AS subscribe_count,
                   COALESCE(SUM(purchase_value),0) AS purchase_value
            FROM ad_daily_stats WHERE {' AND '.join(where)}
        """, params).fetchone()
        spend = row["spend"] or 0
        revenue = row["revenue"] or 0
        purchases = row["purchases"] or 0
        roi = round(revenue / spend, 2) if spend > 0 else 0
        cpa = round(spend / purchases, 2) if purchases > 0 else 0
        cpm = round(spend / row["impressions"] * 1000, 2) if row["impressions"] else 0
        ctr = round(row["clicks"] / row["impressions"] * 100, 2) if row["impressions"] else 0
        return {"total_spend": round(spend,2), "total_revenue": round(revenue,2),
                "roi": roi, "cpa": cpa, "cpm": cpm, "ctr": ctr,
                "impressions": row["impressions"] or 0, "clicks": row["clicks"] or 0,
                "link_clicks": row["link_clicks"] or 0, "purchases": purchases,
                "add_to_cart": row["add_to_cart"] or 0, "subscribe_count": row["subscribe_count"] or 0,
                "purchase_value": round(row["purchase_value"] or 0, 2),
                "active_days": row["days"] or 0, "account_count": row["accounts"] or 0,
                "total_ads": row["ads"] or 0}


def meta_daily_stats(start_date=None, end_date=None, account=None, keyword=None,
                     page=1, page_size=20, user_id=None):
    with database.get_conn() as conn:
        where, params = [], []
        _meta_where(where, params, start_date, end_date, account, keyword)
        _add_user_filter(where, params, user_id, prefix="a.", exclude_paused_meta=True)
        wc = ' AND '.join(where)
        total = conn.execute(f"SELECT COUNT(DISTINCT a.date||a.ad_account) AS cnt FROM ad_daily_stats a WHERE {wc}", params).fetchone()["cnt"]
        rows = conn.execute(f"""
            SELECT a.date, a.ad_account, m.act_name,
                   COALESCE(u.display_name, u.username, '') AS user_name,
                   SUM(a.total_spend) AS total_spend, SUM(a.total_revenue) AS total_revenue,
                   SUM(a.impressions) AS impressions, SUM(a.clicks) AS clicks,
                   SUM(a.inline_link_clicks) AS link_clicks, SUM(a.purchases) AS purchases,
                   SUM(a.purchase_value) AS purchase_value, SUM(a.ad_count) AS ad_count,
                   SUM(a.add_to_cart) AS add_to_cart, SUM(a.subscribe_count) AS subscribe_count,
                   CASE WHEN SUM(a.total_spend)>0 THEN ROUND(SUM(a.total_revenue)/SUM(a.total_spend),2) ELSE 0 END AS roi,
                   CASE WHEN SUM(a.purchases)>0 THEN ROUND(SUM(a.total_spend)/SUM(a.purchases),2) ELSE 0 END AS cpa,
                   CASE WHEN SUM(a.impressions)>0 THEN ROUND(SUM(a.total_spend)/SUM(a.impressions)*1000,2) ELSE 0 END AS cpm,
                   CASE WHEN SUM(a.impressions)>0 THEN ROUND(SUM(a.clicks)*100.0/SUM(a.impressions),2) ELSE 0 END AS ctr
            FROM ad_daily_stats a
            LEFT JOIN meta_accounts m ON a.ad_account = m.act_id
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {wc}
            GROUP BY a.date, a.ad_account ORDER BY a.date DESC, a.ad_account LIMIT ? OFFSET ?
        """, params + [page_size, (page-1)*page_size]).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def meta_trend(days=30, account=None, user_id=None):
    with database.get_conn() as conn:
        where, params = ["source='meta'", "date >= date('now', ?)"], [f"-{days} days"]
        if account:
            where.append("ad_account = ?"); params.append(account)
        _add_user_filter(where, params, user_id, exclude_paused_meta=True)
        rows = conn.execute(f"""
            SELECT date, SUM(total_spend) AS spend, SUM(total_revenue) AS revenue,
                   SUM(purchases) AS purchases, SUM(impressions) AS impressions,
                   SUM(clicks) AS clicks
            FROM ad_daily_stats WHERE {' AND '.join(where)}
            GROUP BY date ORDER BY date
        """, params).fetchall()
        data = [dict(r) for r in rows]
        for i, item in enumerate(data):
            window = data[max(0, i-6): i+1]
            n = len(window)
            item["spend_ma7"] = round(sum(w["spend"] for w in window)/n, 2) if n>0 else 0
        return data


def meta_account_ranking(start_date=None, end_date=None, page=1, page_size=20,
                         user_id=None, account=None):
    with database.get_conn() as conn:
        where, params = [], []
        _meta_where(where, params, start_date, end_date, account, None)
        _add_user_filter(where, params, user_id, prefix="a.", exclude_paused_meta=True)
        total_row = conn.execute(f"""
            SELECT COUNT(DISTINCT a.ad_account) AS cnt
            FROM ad_daily_stats a LEFT JOIN meta_accounts m ON a.ad_account = m.act_id
            WHERE {' AND '.join(where)}
        """, params).fetchone()
        total = total_row["cnt"] if total_row else 0

        rows = conn.execute(f"""
            SELECT a.ad_account, m.act_name,
                   COALESCE(u.display_name, u.username, '') AS user_name,
                   SUM(a.total_spend) AS spend,
                   SUM(a.total_revenue) AS revenue, SUM(a.purchases) AS purchases,
                   SUM(a.add_to_cart) AS add_to_cart, SUM(a.subscribe_count) AS subscribe_count,
                   SUM(a.impressions) AS impressions, SUM(a.clicks) AS clicks,
                   CASE WHEN SUM(a.total_spend)>0 THEN ROUND(SUM(a.total_revenue)/SUM(a.total_spend),2) ELSE 0 END AS roi,
                   CASE WHEN SUM(a.purchases)>0 THEN ROUND(SUM(a.total_spend)/SUM(a.purchases),2) ELSE 0 END AS cpa,
                   CASE WHEN SUM(a.impressions)>0 THEN ROUND(SUM(a.total_spend)/SUM(a.impressions)*1000,2) ELSE 0 END AS cpm,
                   CASE WHEN SUM(a.impressions)>0 THEN ROUND(SUM(a.clicks)*100.0/SUM(a.impressions),2) ELSE 0 END AS ctr
            FROM ad_daily_stats a
            LEFT JOIN meta_accounts m ON a.ad_account = m.act_id
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {' AND '.join(where)}
            GROUP BY a.ad_account ORDER BY spend DESC LIMIT ? OFFSET ?
        """, params + [page_size, (page-1)*page_size]).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ====== 用户汇总排名 ======

def get_user_ranking(start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    """按用户汇总消耗/收入/ROI/订单/CPA，用于管理员数据看板
    消耗来源：书城 ad_daily_stats（source='pingykj'）
    收入/订单来源：书城 orders 表（status='成功'）的实际支付金额
    """
    with database.get_conn() as conn:
        where_ads = ["a.source = 'pingykj'"]
        where_orders = ["o.status = '成功'"]
        params_ads = []
        params_orders = []
        if start_date:
            where_ads.append("a.date >= ?")
            params_ads.append(start_date)
            where_orders.append("date(o.order_date) >= ?")
            params_orders.append(start_date)
        if end_date:
            where_ads.append("a.date <= ?")
            params_ads.append(end_date)
            where_orders.append("date(o.order_date) <= ?")
            params_orders.append(end_date)

        # 按用户汇总广告消耗（来自 ad_daily_stats pingykj）
        ads_sql = f"""
            SELECT a.user_id, u.username,
                   CASE WHEN u.display_name IS NOT NULL AND u.display_name != '' THEN u.display_name ELSE u.username END AS display_name,
                   SUM(a.total_spend) AS total_spend
            FROM ad_daily_stats a
            LEFT JOIN users u ON a.user_id = u.id
            WHERE {' AND '.join(where_ads)}
            GROUP BY a.user_id
        """
        ad_rows = conn.execute(ads_sql, params_ads).fetchall()

        # 按用户汇总成功订单的实际收入（来自 orders 表）
        order_sql = f"""
            SELECT o.user_id, COUNT(*) AS order_count, COALESCE(SUM(o.amount), 0) AS total_revenue
            FROM orders o
            WHERE {' AND '.join(where_orders)}
            GROUP BY o.user_id
        """
        order_rows = conn.execute(order_sql, params_orders).fetchall()
        order_map = {r["user_id"]: (r["order_count"], r["total_revenue"]) for r in order_rows}

        results = []
        for r in ad_rows:
            uid = r["user_id"]
            spend = r["total_spend"] or 0
            orders, order_revenue = order_map.get(uid, (0, 0))
            roi = round(order_revenue / spend, 2) if spend > 0 else 0
            cpa = round(spend / orders, 2) if orders > 0 else 0
            results.append({
                "user_id": uid,
                "user_name": r["display_name"] or r["username"],
                "total_spend": round(spend, 2),
                "total_revenue": round(order_revenue, 2),
                "roi": roi,
                "order_count": orders,
                "cpa": cpa,
            })

        results.sort(key=lambda x: x["total_spend"], reverse=True)
        return results


# ====== Meta 广告系列 / 广告组 明细 ======

def _row_metrics(spend, impressions, clicks, purchases, purchase_value):
    """由基础指标派生 roi/cpa/cpm/ctr"""
    return {
        "spend": round(spend, 2),
        "impressions": int(impressions),
        "clicks": int(clicks),
        "purchases": int(purchases),
        "purchase_value": round(purchase_value, 2),
        "roi": round(purchase_value / spend, 2) if spend > 0 else 0,
        "cpa": round(spend / purchases, 2) if purchases > 0 else 0,
        "cpm": round(spend / impressions * 1000, 2) if impressions > 0 else 0,
        "ctr": round(clicks / impressions * 100, 2) if impressions > 0 else 0,
    }


def meta_campaigns(account: str, start_date: str = None, end_date: str = None,
                   user_id: int = None) -> List[Dict[str, Any]]:
    """某账户下按「广告系列」聚合的表现（由广告组数据 GROUP BY 系列得出）"""
    with database.get_conn() as conn:
        accts = [a for a in (account or "").split(",") if a]
        if len(accts) > 1:
            where = ["ad_account IN (" + ",".join(["?"] * len(accts)) + ")"]
            params: List = list(accts)
        else:
            where = ["ad_account = ?"]
            params: List = [accts[0] if accts else account]
        if start_date:
            where.append("date >= ?"); params.append(start_date)
        if end_date:
            where.append("date <= ?"); params.append(end_date)
        _add_user_filter(where, params, user_id)
        sql = f"""
            SELECT agg.*, es.effective_status, es.status, es.created_time,
                   COALESCE(u.display_name, u.username, '') AS user_name
            FROM (
                SELECT campaign_id, MAX(campaign_name) AS campaign_name, MAX(ad_account) AS ad_account,
                    COALESCE(SUM(spend),0) AS spend,
                    COALESCE(SUM(impressions),0) AS impressions,
                    COALESCE(SUM(clicks),0) AS clicks,
                    COALESCE(SUM(purchases),0) AS purchases,
                    COALESCE(SUM(purchase_value),0) AS purchase_value,
                    COALESCE(SUM(add_to_cart),0) AS add_to_cart,
                    COALESCE(SUM(subscribe_count),0) AS subscribe_count
                FROM meta_adset_stats
                WHERE {' AND '.join(where)}
                GROUP BY campaign_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.campaign_id = es.entity_id
                AND es.level = 'campaign'
            LEFT JOIN meta_accounts ma ON agg.ad_account = ma.act_id
            LEFT JOIN users u ON ma.user_id = u.id
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY es.created_time DESC, spend DESC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["campaign_id"] = r["campaign_id"]
            m["campaign_name"] = r["campaign_name"] or r["campaign_id"] or "(未命名系列)"
            m["ad_account"] = r["ad_account"]
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
            m["add_to_cart"] = r["add_to_cart"] if "add_to_cart" in r.keys() else 0
            m["subscribe_count"] = r["subscribe_count"] if "subscribe_count" in r.keys() else 0
            m["user_name"] = r["user_name"] if "user_name" in r.keys() else ""
            out.append(m)
        return out


def meta_adsets(account: str, campaign_id: str = None, start_date: str = None,
                end_date: str = None, user_id: int = None) -> List[Dict[str, Any]]:
    """某账户（可指定系列）下按「广告组」聚合的表现"""
    with database.get_conn() as conn:
        where = ["ad_account = ?"]
        params: List = [account]
        if campaign_id:
            where.append("campaign_id = ?"); params.append(campaign_id)
        if start_date:
            where.append("date >= ?"); params.append(start_date)
        if end_date:
            where.append("date <= ?"); params.append(end_date)
        _add_user_filter(where, params, user_id)
        sql = f"""
            SELECT agg.*, es.effective_status, es.status,
                   COALESCE(u.display_name, u.username, '') AS user_name
            FROM (
                SELECT adset_id, MAX(adset_name) AS adset_name,
                    campaign_id, MAX(campaign_name) AS campaign_name,
                    MAX(ad_account) AS ad_account,
                    COALESCE(SUM(spend),0) AS spend,
                    COALESCE(SUM(impressions),0) AS impressions,
                    COALESCE(SUM(clicks),0) AS clicks,
                    COALESCE(SUM(purchases),0) AS purchases,
                    COALESCE(SUM(purchase_value),0) AS purchase_value,
                    COALESCE(SUM(add_to_cart),0) AS add_to_cart,
                    COALESCE(SUM(subscribe_count),0) AS subscribe_count
                FROM meta_adset_stats
                WHERE {' AND '.join(where)}
                GROUP BY adset_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.adset_id = es.entity_id
                AND es.level = 'adset'
            LEFT JOIN meta_accounts ma ON agg.ad_account = ma.act_id
            LEFT JOIN users u ON ma.user_id = u.id
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY spend DESC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["adset_id"] = r["adset_id"]
            m["adset_name"] = r["adset_name"] or r["adset_id"] or "(未命名广告组)"
            m["campaign_id"] = r["campaign_id"]
            m["campaign_name"] = r["campaign_name"] or ""
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
            m["add_to_cart"] = r["add_to_cart"] if "add_to_cart" in r.keys() else 0
            m["subscribe_count"] = r["subscribe_count"] if "subscribe_count" in r.keys() else 0
            m["user_name"] = r["user_name"] if "user_name" in r.keys() else ""
            out.append(m)
        return out


def meta_ads(account: str, adset_id: str = None, start_date: str = None,
             end_date: str = None, user_id: int = None) -> List[Dict[str, Any]]:
    """某账户（可指定广告组）下按「广告」聚合的表现，附带素材缩略图。"""
    with database.get_conn() as conn:
        where = ["s.ad_account = ?"]
        params: List = [account]
        if adset_id:
            where.append("s.adset_id = ?"); params.append(adset_id)
        if start_date:
            where.append("s.date >= ?"); params.append(start_date)
        if end_date:
            where.append("s.date <= ?"); params.append(end_date)
        if user_id is not None:
            where.append("s.user_id = ?"); params.append(user_id)
        sql = f"""
            SELECT agg.*, es.effective_status, es.status,
                   COALESCE(u.display_name, u.username, '') AS user_name
            FROM (
                SELECT s.ad_id, MAX(s.ad_name) AS ad_name,
                    s.adset_id, MAX(s.adset_name) AS adset_name,
                    s.campaign_id, MAX(s.campaign_name) AS campaign_name,
                    MAX(s.ad_account) AS ad_account,
                    COALESCE(SUM(s.spend),0) AS spend,
                    COALESCE(SUM(s.impressions),0) AS impressions,
                    COALESCE(SUM(s.clicks),0) AS clicks,
                    COALESCE(SUM(s.purchases),0) AS purchases,
                    COALESCE(SUM(s.purchase_value),0) AS purchase_value,
                    COALESCE(SUM(s.add_to_cart),0) AS add_to_cart,
                    COALESCE(SUM(s.subscribe_count),0) AS subscribe_count,
                    MAX(c.local_path) AS local_path,
                    MAX(c.thumbnail_url) AS thumbnail_url,
                    MAX(c.video_id) AS video_id
                FROM meta_ad_stats s
                LEFT JOIN meta_ad_creatives c ON c.ad_id = s.ad_id AND c.user_id = s.user_id
                WHERE {' AND '.join(where)}
                AND s.ad_id NOT LIKE '_missing_ad_%'
                GROUP BY s.ad_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.ad_id = es.entity_id
                AND es.level = 'ad'
            LEFT JOIN meta_accounts ma ON agg.ad_account = ma.act_id
            LEFT JOIN users u ON ma.user_id = u.id
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY spend DESC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["ad_id"] = r["ad_id"]
            m["ad_name"] = r["ad_name"] or r["ad_id"] or "(未命名广告)"
            m["adset_id"] = r["adset_id"]
            m["campaign_name"] = r["campaign_name"] or ""
            m["add_to_cart"] = r["add_to_cart"] if "add_to_cart" in r.keys() else 0
            m["subscribe_count"] = r["subscribe_count"] if "subscribe_count" in r.keys() else 0
            m["thumb"] = ("/static/" + r["local_path"]) if r["local_path"] else (r["thumbnail_url"] or "")
            m["video_id"] = r["video_id"] or ""
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
            m["user_name"] = r["user_name"] if "user_name" in r.keys() else ""
            out.append(m)
        return out


def meta_creative_gallery(account: str = None, start_date: str = None, end_date: str = None,
                          sort: str = "spend", page: int = 1, page_size: int = 40,
                          user_id: int = None) -> Dict[str, Any]:
    """素材画廊：按广告聚合，附缩略图，按 消耗/ROI/转化 排序，分页。"""
    sort_col = {"spend": "spend", "roi": "roi", "purchases": "purchases",
                "purchase_value": "purchase_value"}.get(sort, "spend")
    with database.get_conn() as conn:
        where = ["(s.spend > 0 OR s.purchases > 0)"]
        params: List = []
        if account:
            where.append("s.ad_account = ?"); params.append(account)
        if start_date:
            where.append("s.date >= ?"); params.append(start_date)
        if end_date:
            where.append("s.date <= ?"); params.append(end_date)
        if user_id is not None:
            where.append("s.user_id = ?"); params.append(user_id)
        base = f"""
            FROM meta_ad_stats s
            LEFT JOIN meta_ad_creatives c ON c.ad_id = s.ad_id AND c.user_id = s.user_id
            LEFT JOIN users u ON u.id = s.user_id
            LEFT JOIN meta_accounts ma ON ma.act_id = s.ad_account AND ma.user_id = s.user_id
            LEFT JOIN (
                SELECT ad_id, user_id, MAX(id) AS hit_id
                FROM hit_materials WHERE ad_id != '' GROUP BY ad_id, user_id
            ) hm ON hm.ad_id = s.ad_id AND hm.user_id = s.user_id
            WHERE {' AND '.join(where)}
            GROUP BY s.ad_id
        """
        total = conn.execute(f"SELECT COUNT(*) AS n FROM (SELECT s.ad_id {base})", params).fetchone()["n"]
        rows = conn.execute(f"""
            SELECT s.ad_id, MAX(s.ad_name) AS ad_name, s.ad_account,
                s.campaign_id, MAX(s.campaign_name) AS campaign_name,
                s.user_id AS user_id,
                MAX(COALESCE(NULLIF(u.display_name, ''), u.username)) AS user_name,
                MAX(ma.act_name) AS account_name,
                COALESCE(SUM(s.spend),0) AS spend,
                COALESCE(SUM(s.impressions),0) AS impressions,
                COALESCE(SUM(s.clicks),0) AS clicks,
                COALESCE(SUM(s.purchases),0) AS purchases,
                COALESCE(SUM(s.purchase_value),0) AS purchase_value,
                MAX(c.local_path) AS local_path, MAX(c.thumbnail_url) AS thumbnail_url,
                MAX(c.video_id) AS video_id,
                MAX(hm.hit_id) AS hit_id, s.user_id AS hit_owner
            {base}
            ORDER BY {sort_col} DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, (page - 1) * page_size]).fetchall()
        items = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["ad_id"] = r["ad_id"]
            m["ad_name"] = r["ad_name"] or r["ad_id"] or "(未命名广告)"
            m["campaign_name"] = r["campaign_name"] or ""
            m["user_id"] = r["user_id"]
            m["user_name"] = r["user_name"] or ""
            m["account_id"] = r["ad_account"]
            m["account_name"] = r["account_name"] or r["ad_account"] or ""
            m["thumb"] = ("/static/" + r["local_path"]) if r["local_path"] else (r["thumbnail_url"] or "")
            m["video_id"] = r["video_id"] or ""
            m["hit_id"] = r["hit_id"]
            m["hit_owner"] = r["hit_owner"]
            items.append(m)
        return {"data": items, "total": total, "page": page, "page_size": page_size}
