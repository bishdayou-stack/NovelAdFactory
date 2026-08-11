"""Fix table alignment + add CTR column to all Meta tables and KPIs"""
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. KPI grid: 7 → 8 columns
content = content.replace('grid-cols-7 gap-2 mb-4" id="metaKpiRow"', 'grid-cols-8 gap-1.5 mb-4" id="metaKpiRow"')
print('Changed KPI grid to 8 cols')

# 2. KPI cards: add CTR before CPM
old_kpis = """        { id: 'cpm', label: 'CPM', value: summary.cpm || 0, prev: compareSummary.cpm || 0, format: '$', digits: 2 },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
new_kpis = """        { id: 'cpm', label: 'CPM', value: summary.cpm || 0, prev: compareSummary.cpm || 0, format: '$', digits: 2 },
        { id: 'ctr', label: 'CTR', value: summary.ctr || 0, prev: compareSummary.ctr || 0, format: '', digits: 2, suffix: '%' },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },"""
content = content.replace(old_kpis, new_kpis)
print('Added CTR to KPI cards')

# 3. BM Summary: add CTR column, fix table
old_bm = """var html = '<table class="w-full"><thead><tr><th>BM</th><th class="text-right">账户</th><th class="text-right">消耗</th><th class="text-right">ROI</th><th class="text-right">CPA</th><th class="text-right">CPM</th></tr></thead><tbody>';
      bmData.forEach(function(bm) {
        html += '<tr>' +
          '<td class="text-indigo-400">' + escapeHtml(bm.bm_name || '未归类') + '</td>' +
          '<td class="text-right">' + (bm.account_count || 0) + '</td>' +
          '<td class="text-right">$' + ((bm.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((bm.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((bm.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((bm.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((bm.cpm || 0).toFixed(2)) + '</td>' +
          '</tr>';
      });
      if (!bmData.length) html += '<tr><td colspan="6" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""

new_bm = """var html = '<table class="w-full" style="table-layout:fixed"><thead><tr><th class="text-left" style="width:25%">BM</th><th class="text-right" style="width:12%">账户</th><th class="text-right" style="width:15%">消耗</th><th class="text-right" style="width:12%">ROI</th><th class="text-right" style="width:12%">CPA</th><th class="text-right" style="width:12%">CPM</th><th class="text-right" style="width:12%">CTR</th></tr></thead><tbody>';
      bmData.forEach(function(bm) {
        html += '<tr>' +
          '<td class="text-left text-indigo-400">' + escapeHtml(bm.bm_name || '未归类') + '</td>' +
          '<td class="text-right">' + (bm.account_count || 0) + '</td>' +
          '<td class="text-right">$' + ((bm.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((bm.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((bm.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((bm.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((bm.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((bm.ctr || 0).toFixed(2)) + '%</td>' +
          '</tr>';
      });
      if (!bmData.length) html += '<tr><td colspan="7" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_bm, new_bm)
print('Fixed BM Summary + CTR')

# 4. Account Ranking: add CTR, fix table
old_rank = """var html = '<table class="w-full"><thead><tr><th>#</th><th>账户</th><th class="text-right">消耗</th><th class="text-right">转化</th><th class="text-right">ROI</th><th class="text-right">CPA</th><th class="text-right">CPM</th></tr></thead><tbody>';
      items.forEach(function(a, i) {
        var actName = a.act_name || a.ad_account || '-';
        html += '<tr>' +
          '<td class="text-slate-500">' + (i+1) + '</td>' +
          '<td>' + escapeHtml(actName) + '</td>' +
          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (a.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((a.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((a.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((a.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((a.cpm || 0).toFixed(2)) + '</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="7" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""

new_rank = """var html = '<table class="w-full" style="table-layout:fixed"><thead><tr><th class="text-left" style="width:3%">#</th><th class="text-left" style="width:25%">账户</th><th class="text-right" style="width:13%">消耗</th><th class="text-right" style="width:10%">转化</th><th class="text-right" style="width:10%">ROI</th><th class="text-right" style="width:13%">CPA</th><th class="text-right" style="width:13%">CPM</th><th class="text-right" style="width:13%">CTR</th></tr></thead><tbody>';
      items.forEach(function(a, i) {
        var actName = a.act_name || a.ad_account || '-';
        html += '<tr>' +
          '<td class="text-left text-slate-500">' + (i+1) + '</td>' +
          '<td class="text-left">' + escapeHtml(actName) + '</td>' +
          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (a.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((a.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((a.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((a.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((a.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((a.ctr || 0).toFixed(2)) + '%</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="8" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_rank, new_rank)
print('Fixed Account Ranking + CTR')

# 5. Daily Detail: add CTR, fix table
old_daily = """var html = '<table class="w-full"><thead><tr><th>日期</th><th>账户</th><th class="text-right">消耗</th><th class="text-right">转化</th><th class="text-right">ROI</th><th class="text-right">CPA</th><th class="text-right">CPM</th><th class="text-right">展示</th><th class="text-right">点击</th></tr></thead><tbody>';
      items.forEach(function(d) {
        html += '<tr>' +
          '<td>' + (d.date || '-') + '</td>' +
          '<td class="font-mono">' + escapeHtml(d.act_name || d.ad_account || '-') + '</td>' +
          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (d.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((d.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((d.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((d.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((d.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + ((d.clicks || 0).toLocaleString()) + '</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="9" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""

new_daily = """var html = '<table class="w-full" style="table-layout:fixed"><thead><tr><th class="text-left" style="width:10%">日期</th><th class="text-left" style="width:18%">账户</th><th class="text-right" style="width:10%">消耗</th><th class="text-right" style="width:8%">转化</th><th class="text-right" style="width:8%">ROI</th><th class="text-right" style="width:8%">CPA</th><th class="text-right" style="width:8%">CPM</th><th class="text-right" style="width:8%">CTR</th><th class="text-right" style="width:10%">展示</th><th class="text-right" style="width:12%">点击</th></tr></thead><tbody>';
      items.forEach(function(d) {
        html += '<tr>' +
          '<td class="text-left">' + (d.date || '-') + '</td>' +
          '<td class="text-left font-mono">' + escapeHtml(d.act_name || d.ad_account || '-') + '</td>' +
          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (d.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((d.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((d.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((d.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">$' + ((d.cpm || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((d.ctr || 0).toFixed(2)) + '%</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + ((d.clicks || 0).toLocaleString()) + '</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="10" class="text-center text-slate-500 py-4">暂无数据</td></tr>';"""
content = content.replace(old_daily, new_daily)
print('Fixed Daily Detail + CTR')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('\nAll done')
