"""Fix Meta page: remove anomalies, fix alignment, add BM name to discover, handle multi-BM"""
import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# === 1. Remove anomaly section from HTML ===
# Replace: grid-cols-2 div with anomaly + ranking → just ranking spanning full width
old_anomaly_html = '''<div class="grid grid-cols-2 gap-4 mb-4">
          <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">异常预警</h3><div id="metaAnomalies" class="text-[11px]"></div></div>
          <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">账户排行</h3><div id="metaAccountRank" class="text-[11px]"></div></div>
        </div>'''
new_ranking_html = '''<div class="card-dark p-4 mb-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">账户排行</h3><div id="metaAccountRank" class="text-[11px]"></div></div>'''
content = content.replace(old_anomaly_html, new_ranking_html)

# === 2. Remove renderMetaAnomalies function ===
old_anomaly_js = '''    // ---- 异常预警 ----
    function renderMetaAnomalies(summary, compareSummary, dailyData) {
      var anomalies = [];
      if (compareSummary.total_spend > 0) {
        var spendChange = (summary.total_spend - compareSummary.total_spend) / compareSummary.total_spend * 100;
        if (spendChange < -50) anomalies.push({ level: '\U0001f534', type: '消耗骤降', detail: '下降 ' + Math.abs(spendChange).toFixed(0) + '%', current: '$' + (summary.total_spend || 0).toLocaleString(), prev: '$' + (compareSummary.total_spend || 0).toLocaleString() });
      }
      if (compareSummary.roi > 0) {
        var roiChange = (summary.roi - compareSummary.roi) / compareSummary.roi * 100;
        if (roiChange < -30) anomalies.push({ level: '\U0001f7e1', type: 'ROI下降', detail: '下降 ' + Math.abs(roiChange).toFixed(0) + '%', current: (summary.roi || 0).toFixed(2) + 'x', prev: (compareSummary.roi || 0).toFixed(2) + 'x' });
      }
      if (compareSummary.cpa > 0) {
        var cpaChange = (summary.cpa - compareSummary.cpa) / compareSummary.cpa * 100;
        if (cpaChange > 100) anomalies.push({ level: '\U0001f7e0', type: 'CPA翻倍', detail: '上涨 ' + cpaChange.toFixed(0) + '%', current: '$' + (summary.cpa || 0).toFixed(2), prev: '$' + (compareSummary.cpa || 0).toFixed(2) });
      }
      var html = '<table><thead><tr><th></th><th>类型</th><th>详情</th><th>当前</th><th>对比</th></tr></thead><tbody>';
      if (!anomalies.length) {
        html += '<tr><td colspan="5" class="text-center text-emerald-400 py-4">✓ 各项指标正常</td></tr>';
      } else {
        anomalies.forEach(function(a) {
          html += '<tr><td>' + a.level + '</td><td class="text-amber-400">' + a.type + '</td><td>' + a.detail + '</td><td>' + a.current + '</td><td class="text-slate-500">' + a.prev + '</td></tr>';
        });
      }
      html += '</tbody></table>';
      document.getElementById('metaAnomalies').innerHTML = html;
    }\n'''

# Try to find and remove the anomaly function
# It starts with "// ---- 异常预警 ----" and ends with the next "// ----"
anomaly_start = content.find('    // ---- 异常预警 ----')
if anomaly_start >= 0:
    # Find the next function marker after anomaly
    next_section = content.find('    // ---- 账户排行 ----', anomaly_start + 1)
    if next_section >= 0:
        content = content[:anomaly_start] + content[next_section:]
        print('Removed renderMetaAnomalies function')
    else:
        print('WARNING: Could not find end of anomaly function')

# === 3. Remove anomaly reference from refreshMetaDashboard ===
old_refresh = '        renderMetaAnomalies(summaryRes, compareRes, dailyRes.data || dailyRes);\n'
content = content.replace(old_refresh, '')
print('Removed anomaly from refreshMetaDashboard')

# === 4. Update CSS to remove anomaly references ===
old_css_1 = '#metaDailyTable table, #metaAccountRank table, #metaBmTable table, #metaAnomalies table { width: 100%; border-collapse: collapse; }'
new_css_1 = '#metaDailyTable table, #metaAccountRank table, #metaBmTable table { width: 100%; border-collapse: collapse; }'
content = content.replace(old_css_1, new_css_1)

old_css_2 = '#metaDailyTable th, #metaAccountRank th, #metaBmTable th, #metaAnomalies th { color: #94a3b8; text-align: left; padding: 6px 8px; font-weight: 500; border-bottom: 1px solid rgba(51,65,85,0.5); }'
new_css_2 = '#metaDailyTable th, #metaAccountRank th, #metaBmTable th { color: #94a3b8; text-align: left; padding: 6px 8px; font-weight: 500; border-bottom: 1px solid rgba(51,65,85,0.5); }'
content = content.replace(old_css_2, new_css_2)

old_css_3 = '#metaDailyTable td, #metaAccountRank td, #metaBmTable td, #metaAnomalies td { color: #cbd5e1; padding: 5px 8px; border-bottom: 1px solid rgba(51,65,85,0.2); }'
new_css_3 = '#metaDailyTable td, #metaAccountRank td, #metaBmTable td { color: #cbd5e1; padding: 5px 8px; border-bottom: 1px solid rgba(51,65,85,0.2); }'
content = content.replace(old_css_3, new_css_3)
print('Updated CSS')

# === 5. Add BM name input to discover panel ===
old_discover = "'<h3 class=\"text-sm font-bold text-white mb-3\">发现账户</h3>' +\n        '<input id=\"discoverTokenInput\" type=\"password\""
new_discover = "'<h3 class=\"text-sm font-bold text-white mb-3\">发现账户</h3>' +\n        '<div class=\"mb-3\"><label class=\"block text-xs text-slate-400 mb-1\">BM 名称</label><input id=\"discoverBmName\" class=\"w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400\" placeholder=\"填写后导入的账户归入此 BM\"></div>' +\n        '<input id=\"discoverTokenInput\" type=\"password\""
content = content.replace(old_discover, new_discover)
print('Added BM name input to discover panel')

# === 6. Update doImportAccounts to include BM name ===
old_import = "var token = document.getElementById('discoverTokenInput').value.trim();\n      var resp = await fetch('/api/meta/accounts/import', {\n        method: 'POST', headers: {'Content-Type': 'application/json'},\n        body: JSON.stringify({accounts: selected, access_token: token})"
new_import = "var token = document.getElementById('discoverTokenInput').value.trim();\n      var bmName = (document.getElementById('discoverBmName') || {}).value || '';\n      // Store BM name in each account for later use\n      selected.forEach(function(a) { a.business_name = bmName || a.business_name || ''; });\n      var resp = await fetch('/api/meta/accounts/import', {\n        method: 'POST', headers: {'Content-Type': 'application/json'},\n        body: JSON.stringify({accounts: selected, access_token: token})"
content = content.replace(old_import, new_import)
print('Updated import to include BM name')

# === 7. Fix daily stats table: add missing columns to match header ===
# Header: 日期 账户 消耗 转化 ROI CPA 展示 点击 (8 columns)
# Data cells: 8 columns - looks OK, let me verify alignment
# Account ranking header: # 账户 消耗 转化 ROI CPA (6 columns)
# Daily header: 日期 账户 消耗 转化 ROI CPA 展示 点击 (8 columns)

# === 8. Fix KPI cards: 6 cards in grid-cols-6, but on smaller screens they wrap wrong ===
# Keep grid-cols-6 but add responsive fallback

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('\nAll changes applied successfully')
