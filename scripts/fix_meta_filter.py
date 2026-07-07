"""Precisely add exclude_paused_meta to Meta analytics functions only"""
with open('analytics.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update _add_user_filter definition
old_def = '''def _add_user_filter(where: List[str], params: List, user_id: int, prefix: str = ""):
    """添加 user_id 过滤条件"""
    if user_id is not None:
        col = f"{prefix}user_id" if prefix else "user_id"
        where.append(f"{col} = ?")
        params.append(user_id)
    # user_id=None 表示管理员看全部，不加过滤'''

new_def = '''def _add_user_filter(where: List[str], params: List, user_id: int, prefix: str = "",
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
    # user_id=None 表示管理员看全部，不加过滤'''

content = content.replace(old_def, new_def)
print('Updated _add_user_filter definition')

# 2. meta_summary: line ~487
content = content.replace(
    '        _add_user_filter(where, params, user_id)\n        row = conn.execute(f"""\n            SELECT COALESCE(SUM(total_spend),0) AS spend',
    '        _add_user_filter(where, params, user_id, exclude_paused_meta=True)\n        row = conn.execute(f"""\n            SELECT COALESCE(SUM(total_spend),0) AS spend'
)
print('Updated meta_summary')

# 3. meta_daily_stats: line ~518
content = content.replace(
    '        _add_user_filter(where, params, user_id, prefix="a.")\n        wc',
    '        _add_user_filter(where, params, user_id, prefix="a.", exclude_paused_meta=True)\n        wc'
)
print('Updated meta_daily_stats')

# 4. meta_trend: line ~542
content = content.replace(
    '        _add_user_filter(where, params, user_id)\n        rows = conn.execute(f"""\n            SELECT date, SUM(total_spend) AS spend',
    '        _add_user_filter(where, params, user_id, exclude_paused_meta=True)\n        rows = conn.execute(f"""\n            SELECT date, SUM(total_spend) AS spend'
)
print('Updated meta_trend')

# 5. meta_account_ranking: line ~563
content = content.replace(
    '        _add_user_filter(where, params, user_id, prefix="a.")\n        rows = conn.execute(f"""\n            SELECT a.ad_account, m.act_name',
    '        _add_user_filter(where, params, user_id, prefix="a.", exclude_paused_meta=True)\n        rows = conn.execute(f"""\n            SELECT a.ad_account, m.act_name'
)
print('Updated meta_account_ranking')

with open('analytics.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nAll done')
