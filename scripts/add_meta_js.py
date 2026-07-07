"""Add Meta management JS functions to index.html"""
import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

new_meta_js = r'''

    // ====== Meta 管理中心 JS ======

    var _metaAccounts = [];
    var _metaSelectedAccount = null;
    var _metaChart = null;
    var _discoveredMetaAccounts = [];
    var _metaDailyPage = 1;

    // ---- 账户树 ----
    function loadMetaAccountTree() {
      fetch('/api/meta/accounts').then(function(r) { return r.json(); }).then(function(data) {
        _metaAccounts = data.accounts || data || [];
        renderMetaAccountTree();
      });
    }

    function renderMetaAccountTree() {
      var tree = document.getElementById('metaAccountTree');
      var searchText = (document.getElementById('metaAccountSearch').value || '').toLowerCase();
      var accounts = _metaAccounts;
      if (searchText) {
        accounts = accounts.filter(function(a) {
          return (a.act_id || '').toLowerCase().indexOf(searchText) >= 0 ||
                 (a.act_name || '').toLowerCase().indexOf(searchText) >= 0 ||
                 (a.pingykj_account || '').toLowerCase().indexOf(searchText) >= 0;
        });
      }
      var groups = {};
      accounts.forEach(function(a) {
        var bm = a.pingykj_account || '未归类';
        if (!groups[bm]) groups[bm] = [];
        groups[bm].push(a);
      });
      var html = '';
      html += '<div class="meta-tree-item' + (_metaSelectedAccount === null ? ' active' : '') + '" onclick="selectMetaAccount(null)">📊 全部账户</div>';
      var bmNames = Object.keys(groups).sort();
      bmNames.forEach(function(bm) {
        var bmAccounts = groups[bm];
        html += '<div class="meta-tree-bm">' + escapeHtml(bm) + ' (' + bmAccounts.length + ')</div>';
        var bmKey = 'bm:' + bm;
        html += '<div class="meta-tree-item meta-tree-account' + (_metaSelectedAccount === bmKey ? ' active' : '') + '" onclick="selectMetaAccount(\'' + bmKey + '\')">📁 ' + escapeHtml(bm) + ' 汇总</div>';
        bmAccounts.forEach(function(a) {
          var actKey = a.act_id;
          html += '<div class="meta-tree-item meta-tree-account' + (_metaSelectedAccount === actKey ? ' active' : '') + '" onclick="selectMetaAccount(\'' + actKey + '\')">' +
            '<span class="inline-block w-2 h-2 rounded-full mr-1.5 ' + (a.status === 'active' ? 'bg-emerald-400' : 'bg-slate-500') + '"></span>' +
            escapeHtml(a.act_name || a.act_id) + '</div>';
        });
      });
      tree.innerHTML = html || '<div class="p-3 text-slate-500 text-center">暂无账户，请先发现并导入</div>';
      document.getElementById('metaAccountFooter').textContent = '已导入 ' + _metaAccounts.length + ' 个账户';
    }

    function selectMetaAccount(key) {
      _metaSelectedAccount = key;
      renderMetaAccountTree();
      refreshMetaDashboard();
    }

    document.getElementById('metaAccountSearch').addEventListener('input', function() { renderMetaAccountTree(); });

    // ---- 发现/导入面板 ----
    function openDiscoverPanel() {
      var html = '<div id="discoverOverlay" class="fixed inset-0 z-[140] flex items-center justify-center bg-black/60" onclick="if(event.target===this)closeDiscoverPanel()">' +
        '<div class="bg-slate-800 rounded-2xl p-5 w-[480px] max-h-[80vh] overflow-y-auto border border-slate-700 shadow-2xl">' +
        '<h3 class="text-sm font-bold text-white mb-3">发现账户</h3>' +
        '<input id="discoverTokenInput" type="password" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400 mb-3" placeholder="输入你的 Access Token">' +
        '<button onclick="doDiscoverAccounts()" class="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 mb-3 cursor-pointer">发现账户</button>' +
        '<div id="discoverResultList" class="max-h-60 overflow-y-auto text-xs"></div>' +
        '<div id="discoverImportArea" class="mt-2 flex gap-2" hidden>' +
        '<button onclick="toggleSelectAllDiscovered()" class="text-xs text-indigo-400 cursor-pointer">全选</button>' +
        '<button onclick="doImportAccounts()" class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 cursor-pointer">导入选中</button>' +
        '</div>' +
        '<button onclick="closeDiscoverPanel()" class="mt-3 w-full rounded-lg border border-slate-600 py-2 text-xs text-slate-400 hover:text-white cursor-pointer bg-transparent">关闭</button>' +
        '</div></div>';
      document.body.insertAdjacentHTML('beforeend', html);
    }

    function closeDiscoverPanel() {
      var el = document.getElementById('discoverOverlay');
      if (el) el.remove();
    }

    async function doDiscoverAccounts() {
      var token = document.getElementById('discoverTokenInput').value.trim();
      if (!token) { alert('请输入 Access Token'); return; }
      var list = document.getElementById('discoverResultList');
      list.innerHTML = '<div class="text-xs text-slate-400 py-4 text-center">正在发现...</div>';
      var resp = await fetch('/api/meta/discover', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({access_token: token}) });
      var result = await resp.json();
      _discoveredMetaAccounts = result.ad_accounts || [];
      document.getElementById('discoverImportArea').hidden = _discoveredMetaAccounts.length === 0;
      list.innerHTML = _discoveredMetaAccounts.map(function(a, i) {
        return '<label class="flex items-center gap-2 py-1.5 px-2 hover:bg-slate-700 rounded cursor-pointer text-xs text-slate-300">' +
          '<input type="checkbox" class="discover-meta-cb" data-idx="' + i + '" ' + (a.status === 'active' ? 'checked' : '') + '>' +
          '<span class="font-mono">' + (a.id || '') + '</span>' +
          '<span>' + (a.name || '') + '</span>' +
          (a.business_name ? '<span class="text-indigo-400">BM:' + a.business_name + '</span>' : '') +
          '</label>';
      }).join('') || '<div class="text-xs text-slate-500 py-4 text-center">未发现账户</div>';
    }

    function toggleSelectAllDiscovered() {
      var cbs = document.querySelectorAll('.discover-meta-cb');
      var allChecked = Array.from(cbs).every(function(cb) { return cb.checked; });
      cbs.forEach(function(cb) { cb.checked = !allChecked; });
    }

    async function doImportAccounts() {
      var cbs = document.querySelectorAll('.discover-meta-cb');
      var selected = [];
      cbs.forEach(function(cb) {
        if (cb.checked) {
          var idx = parseInt(cb.dataset.idx);
          if (_discoveredMetaAccounts[idx]) selected.push(_discoveredMetaAccounts[idx]);
        }
      });
      if (!selected.length) { alert('请勾选账户'); return; }
      var token = document.getElementById('discoverTokenInput').value.trim();
      var resp = await fetch('/api/meta/accounts/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accounts: selected, access_token: token})
      });
      var result = await resp.json();
      alert('已导入 ' + result.count + ' 个账户');
      closeDiscoverPanel();
      loadMetaAccountTree();
    }

    document.getElementById('btnMetaDiscover').addEventListener('click', openDiscoverPanel);

    // ---- 日期和对比基准 ----
    function getMetaDateRange() {
      var activePill = document.querySelector('#metaDatePills .metaDatePillActive') || document.querySelector('#metaDatePills button');
      var range = activePill ? activePill.dataset.range : 'today';
      var today = new Date().toISOString().slice(0,10);
      var start = today, end = today;
      if (range === 'yesterday') {
        var d = new Date(); d.setDate(d.getDate()-1);
        start = end = d.toISOString().slice(0,10);
      } else if (range === '3') {
        var d = new Date(); d.setDate(d.getDate()-3);
        start = d.toISOString().slice(0,10);
      } else if (range === '7') {
        var d = new Date(); d.setDate(d.getDate()-7);
        start = d.toISOString().slice(0,10);
      } else if (range === '30') {
        var d = new Date(); d.setDate(d.getDate()-30);
        start = d.toISOString().slice(0,10);
      }
      return { start: start, end: end, range: range };
    }

    function getCompareDateRange() {
      var baseline = document.getElementById('metaCompareBaseline').value;
      var start, end;
      if (baseline === 'yesterday') {
        var d = new Date(); d.setDate(d.getDate()-2);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'daybefore') {
        var d = new Date(); d.setDate(d.getDate()-3);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'lastweek') {
        var d = new Date(); d.setDate(d.getDate()-7);
        start = end = d.toISOString().slice(0,10);
      } else if (baseline === 'avg7') {
        var d = new Date(); d.setDate(d.getDate()-1);
        end = d.toISOString().slice(0,10);
        d.setDate(d.getDate()-7);
        start = d.toISOString().slice(0,10);
      } else if (baseline === 'avg30') {
        var d = new Date(); d.setDate(d.getDate()-1);
        end = d.toISOString().slice(0,10);
        d.setDate(d.getDate()-30);
        start = d.toISOString().slice(0,10);
      }
      return { start: start, end: end };
    }

    document.querySelectorAll('#metaDatePills button').forEach(function(btn) {
      btn.addEventListener('click', function() {
        document.querySelectorAll('#metaDatePills button').forEach(function(b) {
          b.classList.remove('metaDatePillActive', 'bg-slate-700', 'text-white');
          b.classList.add('text-slate-400');
        });
        this.classList.add('metaDatePillActive', 'bg-slate-700', 'text-white');
        this.classList.remove('text-slate-400');
        refreshMetaDashboard();
      });
    });

    document.getElementById('metaCompareBaseline').addEventListener('change', function() { refreshMetaDashboard(); });
    document.getElementById('btnMetaSync').addEventListener('click', triggerMetaSync);

    // ---- KPI 卡片 ----
    function renderMetaKpis(summary, compareSummary) {
      var kpis = [
        { id: 'spend', label: '消耗', value: summary.total_spend || 0, prev: compareSummary.total_spend || 0, format: '$', digits: 2 },
        { id: 'purchases', label: '转化', value: summary.purchases || 0, prev: compareSummary.purchases || 0, format: '', digits: 0 },
        { id: 'roi', label: 'ROI', value: summary.roi || 0, prev: compareSummary.roi || 0, format: '', digits: 2, suffix: 'x', positive: true },
        { id: 'cpa', label: 'CPA', value: summary.cpa || 0, prev: compareSummary.cpa || 0, format: '$', digits: 2, inverse: true },
        { id: 'impressions', label: '展示', value: summary.impressions || 0, prev: compareSummary.impressions || 0, format: '', digits: 0, compact: true },
        { id: 'clicks', label: '点击', value: summary.clicks || 0, prev: compareSummary.clicks || 0, format: '', digits: 0, compact: true },
      ];
      var html = '';
      kpis.forEach(function(k) {
        var change = k.prev > 0 ? ((k.value - k.prev) / k.prev * 100) : 0;
        var changeClass = change >= 0 ? 'meta-kpi-up' : 'meta-kpi-down';
        if (k.inverse) changeClass = change <= 0 ? 'meta-kpi-up' : 'meta-kpi-down';
        var arrow = change >= 0 ? '▲' : '▼';
        var valStr = k.format + (k.compact ?
          (k.value >= 1000 ? (k.value/1000).toFixed(1)+'K' : k.value.toLocaleString()) :
          k.value.toLocaleString(undefined, {minimumFractionDigits: k.digits, maximumFractionDigits: k.digits}))
          + (k.suffix || '');
        html += '<div class="meta-kpi-card">' +
          '<div class="meta-kpi-label">' + k.label + '</div>' +
          '<div class="meta-kpi-value">' + valStr + '</div>' +
          '<div class="meta-kpi-change ' + changeClass + '">' + arrow + ' ' + Math.abs(change).toFixed(1) + '%</div>' +
          '</div>';
      });
      document.getElementById('metaKpiRow').innerHTML = html;
    }

    // ---- 趋势图 ----
    function renderMetaChart(trendData) {
      var ctx = document.getElementById('metaChartTrend').getContext('2d');
      if (_metaChart) _metaChart.destroy();
      Chart.defaults.color = '#94a3b8';
      Chart.defaults.borderColor = 'rgba(51,65,85,0.3)';
      _metaChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: trendData.map(function(d) { return d.date; }),
          datasets: [
            { label: '消耗', data: trendData.map(function(d) { return d.spend || 0; }),
              backgroundColor: 'rgba(99,102,241,0.6)', borderRadius: 4, yAxisID: 'y', order: 2 },
            { label: 'ROI', data: trendData.map(function(d) { return d.roi || 0; }),
              type: 'line', borderColor: '#34d399', pointBackgroundColor: '#34d399',
              pointRadius: 2, tension: 0.3, yAxisID: 'y1', order: 1 }
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { intersect: false, mode: 'index' },
          scales: {
            y: { type: 'linear', position: 'left',
              grid: { color: 'rgba(51,65,85,0.2)' },
              ticks: { callback: function(v) { return '$' + (v >= 1000 ? (v/1000).toFixed(1)+'K' : v); } } },
            y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false },
              ticks: { callback: function(v) { return v.toFixed(1) + 'x'; } } },
            x: { grid: { display: false } }
          },
          plugins: { legend: { labels: { boxWidth: 12, padding: 16, font: { size: 10 } } } }
        }
      });
    }

    // ---- BM 汇总 ----
    function renderBmTable(bmData) {
      var html = '<table><thead><tr><th>BM</th><th class="text-right">账户</th><th class="text-right">消耗</th><th class="text-right">ROI</th><th class="text-right">CPA</th></tr></thead><tbody>';
      bmData.forEach(function(bm) {
        html += '<tr>' +
          '<td class="text-indigo-400">' + escapeHtml(bm.bm_name || '未归类') + '</td>' +
          '<td class="text-right">' + (bm.account_count || 0) + '</td>' +
          '<td class="text-right">$' + ((bm.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right ' + ((bm.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((bm.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((bm.cpa || 0).toFixed(2)) + '</td>' +
          '</tr>';
      });
      if (!bmData.length) html += '<tr><td colspan="5" class="text-center text-slate-500 py-4">暂无数据</td></tr>';
      html += '</tbody></table>';
      document.getElementById('metaBmTable').innerHTML = html;
    }

    // ---- 异常预警 ----
    function renderMetaAnomalies(summary, compareSummary, dailyData) {
      var anomalies = [];
      if (compareSummary.total_spend > 0) {
        var spendChange = (summary.total_spend - compareSummary.total_spend) / compareSummary.total_spend * 100;
        if (spendChange < -50) anomalies.push({ level: '🔴', type: '消耗骤降', detail: '下降 ' + Math.abs(spendChange).toFixed(0) + '%', current: '$' + (summary.total_spend || 0).toLocaleString(), prev: '$' + (compareSummary.total_spend || 0).toLocaleString() });
      }
      if (compareSummary.roi > 0) {
        var roiChange = (summary.roi - compareSummary.roi) / compareSummary.roi * 100;
        if (roiChange < -30) anomalies.push({ level: '🟡', type: 'ROI下降', detail: '下降 ' + Math.abs(roiChange).toFixed(0) + '%', current: (summary.roi || 0).toFixed(2) + 'x', prev: (compareSummary.roi || 0).toFixed(2) + 'x' });
      }
      if (compareSummary.cpa > 0) {
        var cpaChange = (summary.cpa - compareSummary.cpa) / compareSummary.cpa * 100;
        if (cpaChange > 100) anomalies.push({ level: '🟠', type: 'CPA翻倍', detail: '上涨 ' + cpaChange.toFixed(0) + '%', current: '$' + (summary.cpa || 0).toFixed(2), prev: '$' + (compareSummary.cpa || 0).toFixed(2) });
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
    }

    // ---- 账户排行 ----
    function renderAccountRank(data) {
      var items = Array.isArray(data) ? data : (data.data || []);
      var html = '<table><thead><tr><th>#</th><th>账户</th><th class="text-right">消耗</th><th class="text-right">转化</th><th class="text-right">ROI</th><th class="text-right">CPA</th></tr></thead><tbody>';
      items.forEach(function(a, i) {
        var actName = a.act_name || a.ad_account || '-';
        html += '<tr>' +
          '<td class="text-slate-500">' + (i+1) + '</td>' +
          '<td>' + escapeHtml(actName) + '</td>' +
          '<td class="text-right">$' + ((a.total_spend || a.spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (a.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((a.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((a.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((a.cpa || 0).toFixed(2)) + '</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="6" class="text-center text-slate-500 py-4">暂无数据</td></tr>';
      html += '</tbody></table>';
      document.getElementById('metaAccountRank').innerHTML = html;
    }

    // ---- 日报明细 ----
    function renderMetaDaily(data) {
      var items = Array.isArray(data) ? data : (data.data || []);
      var html = '<table><thead><tr><th>日期</th><th>账户</th><th class="text-right">消耗</th><th class="text-right">转化</th><th class="text-right">ROI</th><th class="text-right">CPA</th><th class="text-right">展示</th><th class="text-right">点击</th></tr></thead><tbody>';
      items.forEach(function(d) {
        html += '<tr>' +
          '<td>' + (d.date || '-') + '</td>' +
          '<td class="font-mono">' + escapeHtml(d.act_name || d.ad_account || '-') + '</td>' +
          '<td class="text-right">$' + ((d.total_spend || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + (d.purchases || 0) + '</td>' +
          '<td class="text-right ' + ((d.roi || 0) >= 1 ? 'text-emerald-400' : 'text-red-400') + '">' + ((d.roi || 0).toFixed(2)) + 'x</td>' +
          '<td class="text-right">$' + ((d.cpa || 0).toFixed(2)) + '</td>' +
          '<td class="text-right">' + ((d.impressions || 0).toLocaleString()) + '</td>' +
          '<td class="text-right">' + ((d.clicks || 0).toLocaleString()) + '</td>' +
          '</tr>';
      });
      if (!items.length) html += '<tr><td colspan="8" class="text-center text-slate-500 py-4">暂无数据</td></tr>';
      html += '</tbody></table>';
      document.getElementById('metaDailyTable').innerHTML = html;
    }

    // ---- 主刷新函数 ----
    async function refreshMetaDashboard() {
      var range = getMetaDateRange();
      var compareRange = getCompareDateRange();
      var params = '?start=' + range.start + '&end=' + range.end;
      var compareParams = '?start=' + compareRange.start + '&end=' + compareRange.end;
      if (_metaSelectedAccount) {
        if (_metaSelectedAccount.indexOf('bm:') === 0) {
          params += '&bm=' + encodeURIComponent(_metaSelectedAccount.slice(3));
          compareParams += '&bm=' + encodeURIComponent(_metaSelectedAccount.slice(3));
        } else {
          params += '&account=' + _metaSelectedAccount;
          compareParams += '&account=' + _metaSelectedAccount;
        }
      }
      try {
        var [summaryRes, compareRes, trendRes, bmRes, dailyRes, rankRes] = await Promise.all([
          fetch('/api/meta/summary' + params).then(function(r) { return r.json(); }),
          fetch('/api/meta/summary' + compareParams).then(function(r) { return r.json(); }),
          fetch('/api/meta/trend?start=' + range.start + '&end=' + range.end).then(function(r) { return r.json(); }),
          fetch('/api/meta/bm-summary' + params).then(function(r) { return r.json(); }),
          fetch('/api/meta/daily-stats' + params + '&page=' + _metaDailyPage + '&page_size=10').then(function(r) { return r.json(); }),
          fetch('/api/meta/account-ranking' + params).then(function(r) { return r.json(); }),
        ]);
        renderMetaKpis(summaryRes, compareRes);
        renderMetaChart(trendRes.data || trendRes);
        renderBmTable(bmRes.bm_summary || []);
        renderMetaAnomalies(summaryRes, compareRes, dailyRes.data || dailyRes);
        renderAccountRank(rankRes.data || rankRes);
        renderMetaDaily(dailyRes.data || dailyRes);
      } catch(e) { console.error('Meta dashboard error:', e); }
    }

    // ---- 手动同步 ----
    async function triggerMetaSync() {
      var statusEl = document.getElementById('metaSyncStatus');
      statusEl.textContent = '同步中...';
      var resp = await fetch('/api/meta/sync', { method: 'POST' });
      var result = await resp.json();
      if (result.success) {
        statusEl.textContent = '同步完成 (' + (result.total_count || 0) + '条)';
        setTimeout(refreshMetaDashboard, 2000);
      } else {
        statusEl.textContent = '✗ ' + (result.message || '同步失败');
      }
    }

    // ---- API 配置弹窗 ----
    function openMetaConfig() {
      fetch('/api/meta/config').then(function(r) { return r.json(); }).then(function(cfg) {
        var html = '<div id="configOverlay" class="fixed inset-0 z-[140] flex items-center justify-center bg-black/60" onclick="if(event.target===this)closeMetaConfig()">' +
          '<div class="bg-slate-800 rounded-2xl p-5 w-[480px] border border-slate-700 shadow-2xl" onclick="event.stopPropagation()">' +
          '<h3 class="text-sm font-bold text-white mb-4">Meta API 配置</h3>' +
          '<div class="grid grid-cols-2 gap-3 text-xs">' +
          '<div><label class="block text-slate-400 mb-1">App ID</label><input id="cfg-app-id" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" value="' + (cfg.app_id || '') + '"></div>' +
          '<div><label class="block text-slate-400 mb-1">App Secret</label><input id="cfg-app-secret" type="password" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" placeholder="留空不修改"></div>' +
          '<div><label class="block text-slate-400 mb-1">默认 Token</label><input id="cfg-default-token" type="password" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" placeholder="留空不修改"></div>' +
          '<div><label class="block text-slate-400 mb-1">API 版本</label><input id="cfg-api-version" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" value="' + (cfg.api_version || 'v25.0') + '"></div>' +
          '<div><label class="block text-slate-400 mb-1">同步间隔(秒)</label><input id="cfg-sync-interval" type="number" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" value="' + (cfg.sync_interval_seconds || 300) + '"></div>' +
          '<div><label class="block text-slate-400 mb-1">速率(次/秒)</label><input id="cfg-rate-limit" type="number" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" value="' + (cfg.rate_limit_per_second || 4) + '"></div>' +
          '<div class="col-span-2"><label class="block text-slate-400 mb-1">代理</label><input id="cfg-proxy" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-white text-sm" value="' + (cfg.proxy || '') + '"></div>' +
          '</div>' +
          '<div class="mt-4 flex gap-2"><button onclick="saveMetaConfig()" class="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-500 cursor-pointer">保存</button><span id="cfgMsg" class="text-xs text-emerald-400 hidden self-center">✓ 已保存</span></div>' +
          '<button onclick="closeMetaConfig()" class="mt-2 w-full rounded-lg border border-slate-600 py-2 text-xs text-slate-400 hover:text-white cursor-pointer bg-transparent">关闭</button>' +
          '</div></div>';
        document.body.insertAdjacentHTML('beforeend', html);
      });
    }

    function closeMetaConfig() {
      var el = document.getElementById('configOverlay');
      if (el) el.remove();
    }

    async function saveMetaConfig() {
      var body = {
        app_id: document.getElementById('cfg-app-id').value,
        app_secret: document.getElementById('cfg-app-secret').value,
        default_access_token: document.getElementById('cfg-default-token').value,
        api_version: document.getElementById('cfg-api-version').value,
        sync_interval_seconds: parseInt(document.getElementById('cfg-sync-interval').value) || 300,
        rate_limit_per_second: parseInt(document.getElementById('cfg-rate-limit').value) || 4,
        proxy: document.getElementById('cfg-proxy').value,
      };
      var resp = await fetch('/api/meta/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
      if (resp.ok) {
        var el = document.getElementById('cfgMsg');
        el.hidden = false;
        setTimeout(function() { el.hidden = true; }, 2000);
      } else {
        var err = await resp.json();
        alert('保存失败: ' + (err.detail || ''));
      }
    }

    document.getElementById('btnMetaConfig').addEventListener('click', openMetaConfig);
'''

marker = "// ====== 爆款素材登记 ======"
content = content.replace(marker, new_meta_js + "\n\n    " + marker)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("DONE - Meta JS functions added successfully")
