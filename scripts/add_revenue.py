"""Add conversion value (转化金额) to KPI cards + all three tables"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. KPI grid: 8 → 9 columns
content = content.replace('grid-cols-8 gap-1.5 mb-4" id="metaKpiRow"', 'grid-cols-9 gap-1.5 mb-4" id="metaKpiRow"')
print('KPI grid: 9 cols')

# 2. KPI cards: add revenue after ctr
old_kpis = """        { id: 'ctr', label: 'CTR', value: summary.ctr || 0, prev: compareSummary.ctr || 0, format: '', digits: 2, suffix: '%' },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
new_kpis = """        { id: 'ctr', label: 'CTR', value: summary.ctr || 0, prev: compareSummary.ctr || 0, format: '', digits: 2, suffix: '%' },
        { id: 'revenue', label: '转化金额', value: summary.total_revenue || 0, prev: compareSummary.total_revenue || 0, format: '$', digits: 2 },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
content = content.replace(old_kpis, new_kpis)
print('KPI cards: +转化金额')

# 3. BM Summary: add revenue column
old_bm = """          '<td class="text-right">$' + ((bm.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((bm.ctr || 0).toFixed(2)) + '%</td>' +
          '</tr>';"""
new_bm = """          '<td class="text-right">$' + ((bm.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((bm.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">$' + ((bm.revenue || 0).toLocaleString()) + '</td>' +
          '</tr>';"""
content = content.replace(old_bm, new_bm)

old_bm_header = """<th class="text-right" style="width:12%">CTR</th></tr></thead><tbody>';"""
new_bm_header = """<th class="text-right" style="width:12%">CTR</th><th class="text-right" style="width:14%">转化金额</th></tr></thead><tbody>';"""
content = content.replace(old_bm_header, new_bm_header)

old_bm_colspan = """if (!bmData.length) html += '<tr><td colspan="7" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
new_bm_colspan = """if (!bmData.length) html += '<tr><td colspan="8" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_bm_colspan, new_bm_colspan)
print('BM Summary: +转化金额')

# 4. Account Ranking: add revenue column
old_rank = """          '<td class="text-right">' + ((a.ctr || 0).toFixed(2)) + '%</td>' +
          '</tr>';"""
new_rank = """          '<td class="text-right">' + ((a.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">$' + ((a.revenue || 0).toLocaleString()) + '</td>' +
          '</tr>';"""
content = content.replace(old_rank, new_rank)

old_rank_header = """<th class="text-right" style="width:13%">CTR</th></tr></thead><tbody>';"""
new_rank_header = """<th class="text-right" style="width:13%">CTR</th><th class="text-right" style="width:14%">转化金额</th></tr></thead><tbody>';"""
content = content.replace(old_rank_header, new_rank_header)

old_rank_colspan = """if (!items.length) html += '<tr><td colspan="8" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
new_rank_colspan = """if (!items.length) html += '<tr><td colspan="9" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_rank_colspan, new_rank_colspan)
print('Account Ranking: +转化金额')

# 5. Daily Detail: add revenue column
old_daily = """          '<td class="text-right">' + ((d.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +"""
new_daily = """          '<td class="text-right">' + ((d.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">$' + ((d.purchase_value || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +"""
content = content.replace(old_daily, new_daily)

old_daily_header = """<th class="text-right" style="width:8%">CTR</th><th class="text-right" style="width:10%">展示</th>"""
new_daily_header = """<th class="text-right" style="width:8%">CTR</th><th class="text-right" style="width:12%">转化金额</th><th class="text-right" style="width:8%">展示</th>"""
content = content.replace(old_daily_header, new_daily_header)

old_daily_colspan = """if (!items.length) html += '<tr><td colspan="10" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
new_daily_colspan = """if (!items.length) html += '<tr><td colspan="11" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_daily_colspan, new_daily_colspan)
print('Daily Detail: +转化金额')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nDone')
