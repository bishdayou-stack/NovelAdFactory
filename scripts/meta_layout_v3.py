"""Meta page layout v3: button inline, 归属人, user summary, trend removed, layout swap"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# === 1. Fix tree item button to be inline (already is, but adjust flex) ===
# Current: <span class="flex-1 truncate">name</span><button...>
# The button already follows the name inline. If it wraps, it's because the name is too long.
# Add flex items-center to the tree-item to ensure vertical centering
content = content.replace(
    "html += '<div class=\"meta-tree-item meta-tree-account'",
    "html += '<div class=\"meta-tree-item meta-tree-account flex items-center'"
)
print('1. Fixed button alignment')

# === 2. Remove trend chart HTML ===
old_trend_html = '''<div class="grid grid-cols-5 gap-4 mb-4">
          <div class="col-span-3 card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">BM 汇总</h3><div id="metaBmTable" class="text-[11px]"></div></div>
          <div class="col-span-2 card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">趋势</h3><div class="h-48"><canvas id="metaChartTrend"></canvas></div></div>
        </div>'''
new_trend_html = '''<div class="grid grid-cols-2 gap-4 mb-4">
          <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">用户汇总</h3><div id="metaUserSummary" class="text-[11px]"></div></div>
          <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">BM 汇总</h3><div id="metaBmTable" class="text-[11px]"></div></div>
        </div>'''
content = content.replace(old_trend_html, new_trend_html)
print('2. Trend replaced with User Summary (left) + BM Summary (right)')

# === 3. Remove renderMetaChart function ===
chart_start = content.find('    // ---- 趋势图 ----')
chart_end = content.find('    // ---- BM 汇总 ----')
if chart_start >= 0 and chart_end >= 0:
    content = content[:chart_start] + content[chart_end:]
    print('3. Removed renderMetaChart')
else:
    print('3. WARNING: Could not find renderMetaChart')

# === 4. Remove Chart-related globals (cleanup _metaChart) ===
content = content.replace('    var _metaChart = null;\n', '')
print('4. Cleaned up _metaChart')

# === 5. Remove renderMetaChart call from refreshMetaDashboard ===
content = content.replace(
    "        renderMetaChart(trendRes.data || trendRes);\n",
    ''
)
# Also remove trendRes from destructuring
content = content.replace(
    '        var [summaryRes, trendRes, bmRes, dailyRes, rankRes] = await Promise.all([\n'
    '          fetch(\'/api/meta/summary\' + params).then(function(r) { return r.json(); }),\n'
    '          fetch(\'/api/meta/trend?start=\' + range.start + \'&end=\' + range.end).then(function(r) { return r.json(); }),\n',
    '        var [summaryRes, bmRes, dailyRes, rankRes, userRes] = await Promise.all([\n'
    '          fetch(\'/api/meta/summary\' + params).then(function(r) { return r.json(); }),\n'
)
# Add user summary fetch and remove trend fetch
content = content.replace(
    "          fetch('/api/meta/trend?start=' + range.start + '&end=' + range.end).then(function(r) { return r.json(); }),\n",
    ''
)
# Add user summary API call before bm-summary
content = content.replace(
    "          fetch('/api/meta/bm-summary' + params).then(function(r) { return r.json(); }),\n",
    "          fetch('/api/meta/bm-summary' + params).then(function(r) { return r.json(); }),\n"
    "          fetch('/api/meta/user-summary' + params).then(function(r) { return r.json(); }),\n"
)
# Add render for user summary
content = content.replace(
    "        renderBmTable(bmRes.bm_summary || []);\n",
    "        renderUserSummary(userRes.user_summary || []);\n"
    "        renderBmTable(bmRes.bm_summary || []);\n"
)
print('5. Updated refreshMetaDashboard')

# === 6. Add renderUserSummary function before BM Summary ===
user_summary_fn = '''
    // ---- 用户汇总 ----
    function renderUserSummary(data) {
      var html = '<table class="w-full"><thead><tr><th>用户</th><th class="text-right">账户</th><th class="text-right">消耗</th><th class="text-right">转化</th><th class="text-right">转化金额</th><th class="text-right">ROI</th><th class="text-right">CPA</th></tr></thead><tbody>';
      data.forEach(function(u) {
        html += '<tr>' +
          '<td class="text-indigo-400">' + escapeHtml(u.user_name || '未知') + '</td>' +
          '<td class="text-right">' + (u.account_count || 0) + '</td>' +
          '<td class="text-right">$' + ((u.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (u.purchases || 0) + '</td>' +
          '<td class="text-right">$' + ((u.revenue || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((u.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((u.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((u.cpa || 0).toFixed(2)) + '</td>' +
          '</tr>';
      });
      if (!data.length) html += '<tr><td colspan="7" class="text-center text-slate-500 py-4">暂无数据</td></tr>';
      html += '</tbody></table>';
      document.getElementById('metaUserSummary').innerHTML = html;
    }

'''
bm_summary_pos = content.find('    // ---- BM 汇总 ----')
content = content[:bm_summary_pos] + user_summary_fn + content[bm_summary_pos:]
print('6. Added renderUserSummary')

# === 7. Add 归属人 column to account ranking and daily detail ===
# Account ranking: add user_name after account name
old_rank_row = """          '<td class="text-left">' + escapeHtml(actName) + '</td>' +
          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +"""
new_rank_row = """          '<td class="text-left">' + escapeHtml(actName) + '</td>' +
          '<td class="text-left text-slate-500 text-xs">' + escapeHtml(a.user_name || '-') + '</td>' +
          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +"""
content = content.replace(old_rank_row, new_rank_row)

old_rank_header = """<th class="text-left" style="width:25%">账户</th><th class="text-right" style="width:13%">消耗</th>"""
new_rank_header = """<th class="text-left" style="width:18%">账户</th><th class="text-left" style="width:12%">归属人</th><th class="text-right" style="width:12%">消耗</th>"""
content = content.replace(old_rank_header, new_rank_header)

# Update colspan for empty state (8 → 9)
content = content.replace(
    'if (!items.length) html += \'<tr><td colspan="9" class="text-center text-slate-500 py-4">暂无数据</td></tr>\';',
    'if (!items.length) html += \'<tr><td colspan="10" class="text-center text-slate-500 py-4">暂无数据</td></tr>\';'
)
print('7. Added 归属人 to account ranking')

# Daily detail: add user_name after account
old_daily_row = """          '<td class="text-left font-mono">' + escapeHtml(d.act_name || d.ad_account || '-') + '</td>' +
          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +"""
new_daily_row = """          '<td class="text-left font-mono">' + escapeHtml(d.act_name || d.ad_account || '-') + '</td>' +
          '<td class="text-left text-slate-500 text-xs">' + escapeHtml(d.user_name || '-') + '</td>' +
          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +"""
content = content.replace(old_daily_row, new_daily_row)

old_daily_header = """<th class="text-left" style="width:18%">账户</th><th class="text-right" style="width:10%">消耗</th>"""
new_daily_header = """<th class="text-left" style="width:15%">账户</th><th class="text-left" style="width:10%">归属人</th><th class="text-right" style="width:9%">消耗</th>"""
content = content.replace(old_daily_header, new_daily_header)

# Update colspan (10 → 11)
content = content.replace(
    'if (!items.length) html += \'<tr><td colspan="11" class="text-center text-slate-500 py-4">暂无数据</td></tr>\';',
    'if (!items.length) html += \'<tr><td colspan="12" class="text-center text-slate-500 py-4">暂无数据</td></tr>\';'
)
print('8. Added 归属人 to daily detail')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nAll done')
