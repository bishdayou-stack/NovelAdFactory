"""Reorder 转化金额 to right after 转化 (purchases) in all tables and KPIs"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. KPI cards: move revenue after purchases
old_kpis = """        { id: 'purchases', label: '转化', value: summary.purchases || 0, prev: compareSummary.purchases || 0, format: '', digits: 0 },
        { id: 'roi', label: 'ROI', value: summary.roi || 0, prev: compareSummary.roi || 0, format: '', digits: 2, suffix: 'x', positive: true },
        { id: 'cpa', label: 'CPA', value: summary.cpa || 0, prev: compareSummary.cpa || 0, format: '$', digits: 2, inverse: true },
        { id: 'cpm', label: 'CPM', value: summary.cpm || 0, prev: compareSummary.cpm || 0, format: '$', digits: 2 },
        { id: 'ctr', label: 'CTR', value: summary.ctr || 0, prev: compareSummary.ctr || 0, format: '', digits: 2, suffix: '%' },
        { id: 'revenue', label: '转化金额', value: summary.total_revenue || 0, prev: compareSummary.total_revenue || 0, format: '$', digits: 2 },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
new_kpis = """        { id: 'purchases', label: '转化', value: summary.purchases || 0, prev: compareSummary.purchases || 0, format: '', digits: 0 },
        { id: 'revenue', label: '转化金额', value: summary.total_revenue || 0, prev: compareSummary.total_revenue || 0, format: '$', digits: 2 },
        { id: 'roi', label: 'ROI', value: summary.roi || 0, prev: compareSummary.roi || 0, format: '', digits: 2, suffix: 'x', positive: true },
        { id: 'cpa', label: 'CPA', value: summary.cpa || 0, prev: compareSummary.cpa || 0, format: '$', digits: 2, inverse: true },
        { id: 'cpm', label: 'CPM', value: summary.cpm || 0, prev: compareSummary.cpm || 0, format: '$', digits: 2 },
        { id: 'ctr', label: 'CTR', value: summary.ctr || 0, prev: compareSummary.ctr || 0, format: '', digits: 2, suffix: '%' },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
content = content.replace(old_kpis, new_kpis)
print('KPIs: revenue -> after purchases')

# 2. Daily Detail: revenue after purchases
old_daily = """          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (d.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((d.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((d.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((d.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((d.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((d.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">$' + ((d.purchase_value || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +"""
new_daily = """          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (d.purchases || 0) + '</td>' +
          '<td class="text-right">$' + ((d.purchase_value || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((d.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((d.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((d.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((d.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((d.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +"""
content = content.replace(old_daily, new_daily)

old_daily_h = """<th class="text-right" style="width:8%">转化</th><th class="text-right" style="width:8%">ROI</th><th class="text-right" style="width:8%">CPA</th><th class="text-right" style="width:8%">CPM</th><th class="text-right" style="width:8%">CTR</th><th class="text-right" style="width:12%">转化金额</th><th class="text-right" style="width:8%">展示</th>"""
new_daily_h = """<th class="text-right" style="width:8%">转化</th><th class="text-right" style="width:10%">转化金额</th><th class="text-right" style="width:8%">ROI</th><th class="text-right" style="width:8%">CPA</th><th class="text-right" style="width:8%">CPM</th><th class="text-right" style="width:8%">CTR</th><th class="text-right" style="width:8%">展示</th>"""
content = content.replace(old_daily_h, new_daily_h)
print('Daily: revenue -> after purchases')

# 3. Account Ranking: revenue after purchases
old_rank = """          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (a.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((a.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((a.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((a.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((a.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((a.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">$' + ((a.revenue || 0).toLocaleString()) + '</td>' +"""
new_rank = """          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (a.purchases || 0) + '</td>' +
          '<td class="text-right">$' + ((a.revenue || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((a.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((a.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((a.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((a.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((a.ctr || 0).toFixed(2)) + '%</td>' +"""
content = content.replace(old_rank, new_rank)

old_rank_h = """<th class="text-right" style="width:10%">转化</th><th class="text-right" style="width:10%">ROI</th><th class="text-right" style="width:13%">CPA</th><th class="text-right" style="width:13%">CPM</th><th class="text-right" style="width:13%">CTR</th><th class="text-right" style="width:14%">转化金额</th></tr>"""
new_rank_h = """<th class="text-right" style="width:8%">转化</th><th class="text-right" style="width:12%">转化金额</th><th class="text-right" style="width:8%">ROI</th><th class="text-right" style="width:12%">CPA</th><th class="text-right" style="width:12%">CPM</th><th class="text-right" style="width:12%">CTR</th></tr>"""
content = content.replace(old_rank_h, new_rank_h)
print('Account Ranking: revenue -> after purchases')

# 4. BM Summary: no purchases column, leave revenue at end
print('BM Summary: no purchases column, leave at end')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nDone')
