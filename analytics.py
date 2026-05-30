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


# ====== KPI 汇总 ======

def get_summary(start_date: str = None, end_date: str = None, account: str = None,
                keyword: str = None) -> Dict[str, Any]:
    with database.get_conn() as conn:
        where = ["1=1"]
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
                    page: int = 1, page_size: int = 20) -> dict:
    """返回 {"data": [...], "total": N, "page": 1, "page_size": 20}"""
    with database.get_conn() as conn:
        where = ["1=1"]
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

        allowed_order = {"date", "ad_account", "total_spend", "total_revenue"}
        if order_by not in allowed_order:
            order_by = "date"

        where_clause = ' AND '.join(where)

        # 总数
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM ad_daily_stats WHERE {where_clause}", params
        ).fetchone()
        total = total_row["cnt"]

        # 分页数据
        offset = (page - 1) * page_size
        sql = f"""
            SELECT date, ad_account, total_spend, total_revenue,
                   CASE WHEN total_spend > 0 THEN ROUND(total_revenue / total_spend, 2) ELSE 0 END AS roi,
                   ad_count, impressions, clicks
            FROM ad_daily_stats
            WHERE {where_clause}
            ORDER BY {order_by} DESC, ad_account
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()

        aliases = database.get_account_aliases()
        results = []
        for r in rows:
            d = dict(r)
            acct = d.get("ad_account", "")
            d["account_display"] = aliases.get(acct, acct)
            results.append(d)
        return {"data": results, "total": total, "page": page, "page_size": page_size}


# ====== 趋势数据 ======

def get_trend(days: int = 30, account: str = None, keyword: str = None) -> List[Dict[str, Any]]:
    with database.get_conn() as conn:
        where = ["date >= date('now', ?)"]
        params = [f"-{days} days"]
        if account:
            where.append("ad_account = ?")
            params.append(account)
        _add_keyword(where, params, keyword)

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

def get_accounts() -> list:
    """返回账户列表，含别名"""
    return database.get_account_display_list()


def _account_display(account_id: str) -> str:
    """将账户 ID 转为可读名称（别名 或 ID + 计划名）"""
    aliases = database.get_account_aliases()
    if account_id in aliases:
        return aliases[account_id]
    return account_id


# ====== 账户排名 ======

def get_account_ranking(start_date: str = None, end_date: str = None,
                         keyword: str = None, page: int = 1, page_size: int = 20) -> dict:
    with database.get_conn() as conn:
        where = ["1=1"]
        params = []
        if start_date:
            where.append("date >= ?")
            params.append(start_date)
        if end_date:
            where.append("date <= ?")
            params.append(end_date)
        _add_keyword(where, params, keyword)
        where_clause = ' AND '.join(where)

        # 总数
        total = conn.execute(
            f"SELECT COUNT(DISTINCT ad_account) AS cnt FROM ad_daily_stats WHERE {where_clause}", params
        ).fetchone()["cnt"]

        # 分页
        offset = (page - 1) * page_size
        sql = f"""
            SELECT ad_account, SUM(total_spend) AS spend, SUM(total_revenue) AS revenue,
                   CASE WHEN SUM(total_spend) > 0 THEN ROUND(SUM(total_revenue) / SUM(total_spend), 2) ELSE 0 END AS roi,
                   SUM(ad_count) AS total_ads
            FROM ad_daily_stats
            WHERE {where_clause}
            GROUP BY ad_account
            ORDER BY spend DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()
        aliases = database.get_account_aliases()
        results = []
        for r in rows:
            d = dict(r)
            acct = d.get("ad_account", "")
            d["account_display"] = aliases.get(acct, acct)
            results.append(d)
        return {"data": results, "total": total, "page": page, "page_size": page_size}


# ====== 异常检测 ======

def detect_anomalies(days: int = 30, threshold_sigma: float = 2.0) -> List[Dict[str, Any]]:
    with database.get_conn() as conn:
        rows = conn.execute("""
            SELECT date, SUM(total_spend) AS spend
            FROM ad_daily_stats
            WHERE date >= date('now', ?)
            GROUP BY date ORDER BY date
        """, (f"-{days} days",)).fetchall()

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
               page: int = 1, page_size: int = 15) -> dict:
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

        where_clause = ' AND '.join(where)
        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM orders WHERE {where_clause}", params
        ).fetchone()["cnt"]

        offset = (page - 1) * page_size
        sql = f"""
            SELECT order_id, order_date, amount, status, ad_account, synced_at,
                   json_extract(extra_data, '$.campaignLinkId_dictText') AS promotion_link_name,
                   json_extract(extra_data, '$.adId') AS ad_id
            FROM orders
            WHERE {where_clause}
            ORDER BY order_date DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, params + [page_size, offset]).fetchall()
        return {"data": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


# ====== 小说订单汇总 ======

def get_novel_stats(start_date: str = None, end_date: str = None,
                    keyword: str = None) -> List[Dict[str, Any]]:
    """按小说（novelId + novelName）汇总订单，返回列表按金额降序"""
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

        sql = f"""
            SELECT customer_info, amount
            FROM orders
            WHERE {' AND '.join(where)}
        """
        rows = conn.execute(sql, params).fetchall()

        # customer_info 是双重 JSON 编码的字符串，需解析后分组
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

            # 关键词筛选
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
        result.sort(key=lambda x: x["total_amount"], reverse=True)
        return result
