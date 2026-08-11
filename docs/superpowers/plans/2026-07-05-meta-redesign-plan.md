# Meta 管理中心重设计 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Meta 数据看板 + 账户配置合并为暗色专业风「Meta 管理中心」单页，支持左侧账户树、BM 汇总、异常预警，同时删除投放管理页面。

**Architecture:** 前端单文件 `static/index.html` 改造，后端新增 1 个 API。左侧面板（w-64）展示按 BM 分组的账户树+发现导入，右侧主区域展示 KPI/趋势/排行/日报。暗色 slate-900 主题。

**Tech Stack:** FastAPI, Chart.js 4.4, Tailwind CSS (CDN), vanilla JavaScript

## Global Constraints

- 所有 UI 改动在 `static/index.html` 单文件中完成
- 后端 `main.py` 仅新增 `GET /api/meta/bm-summary` 端点
- 保留所有现有后端路由（`/api/delivery/*` 等）不删除，仅移除前端入口
- 不修改 `database.py`、`analytics.py`、`scraper.py`、`meta_api.py`
- 暗色主题：bg-slate-900/950，卡片 bg-slate-800，文字 white/slate-400
- 合并 `tab-meta-data` + `tab-meta-accounts` → `tab-meta`

---

## File Structure

| 文件 | 操作 | 说明 |
|------|------|------|
| `static/index.html` | 大幅修改 | 删除投放管理、合并两页、新 UI、暗色主题 |
| `main.py` | 小幅修改 | 新增 `GET /api/meta/bm-summary` 端点 |

### 单元边界

- `tab-meta`：新的 Meta 管理中心 Tab，包含左侧面板 + 右侧主区域
- `bm-summary API`：按 BM 聚合的 KPI 汇总（独立端点，前端可独立调用）
- 投放管理删除：HTML + JS + 侧边栏导航全部移除

---

### Task 1: 后端 — 新增 BM 汇总 API

**Files:**
- Modify: `main.py`（在 `_meta_account_ranking` 之后插入新端点）

**Interfaces:**
- Produces: `GET /api/meta/bm-summary?start=&end=&user:` → `{bm_summary: [{bm_name, account_count, spend, purchases, revenue, roi, cpa, impressions, clicks}]}`

- [ ] **Step 1: 在 main.py 添加 BM 汇总端点**

在 `_meta_account_ranking` 函数后面（约 line 4614）插入：

```python
@app.get("/api/meta/bm-summary")
def _meta_bm_summary(start: str = Query(default=None), end: str = Query(default=None),
                      user: dict = Depends(get_current_user)):
    """按 BM（pingykj_account）聚合 Meta 账户 KPI"""
    uid = _opt_user_id(user)
    with database.get_conn() as conn:
        where = ["source = 'meta'"]
        params = []
        if start:
            where.append("date >= ?"); params.append(start)
        if end:
            where.append("date <= ?"); params.append(end)
        if uid is not None:
            where.append("ads.user_id = ?"); params.append(uid)

        rows = conn.execute(f"""
            SELECT
                COALESCE(ma.pingykj_account, '未归类') AS bm_name,
                COUNT(DISTINCT ads.ad_account) AS account_count,
                COALESCE(SUM(ads.total_spend), 0) AS spend,
                COALESCE(SUM(ads.purchases), 0) AS purchases,
                COALESCE(SUM(ads.purchase_value), 0) AS revenue,
                CASE WHEN SUM(ads.total_spend) > 0
                     THEN ROUND(COALESCE(SUM(ads.purchase_value), 0) / SUM(ads.total_spend), 2)
                     ELSE NULL END AS roi,
                CASE WHEN SUM(ads.purchases) > 0
                     THEN ROUND(SUM(ads.total_spend) / SUM(ads.purchases), 2)
                     ELSE NULL END AS cpa,
                COALESCE(SUM(ads.impressions), 0) AS impressions,
                COALESCE(SUM(ads.clicks), 0) AS clicks
            FROM ad_daily_stats ads
            LEFT JOIN meta_accounts ma ON ads.ad_account = ma.act_id AND ads.user_id = ma.user_id
            WHERE {' AND '.join(where)}
            GROUP BY COALESCE(ma.pingykj_account, '未归类')
            ORDER BY spend DESC
        """, params).fetchall()

        return {"bm_summary": [dict(r) for r in rows]}
```

- [ ] **Step 2: 验证语法**

```bash
cd e:/xiangmu/5-28 && python -c "import py_compile; py_compile.compile('main.py', doraise=True); print('OK')"
```

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: 新增 GET /api/meta/bm-summary 按BM聚合KPI端点"
```

---

### Task 2: 删除投放管理页面

**Files:**
- Modify: `static/index.html`（删除 tab-delivery HTML + JS + 导航）

**Interfaces:**
- Consumes: 现有 `tab-delivery` 代码块位置
- Produces: 干净的代码库，投放管理入口完全移除

- [ ] **Step 1: 删除侧边栏投放管理导航按钮**

找到包含 `投放管理` 的导航按钮（约 line 123），删除整个 `<button>` 元素：

```html
<!-- 删除以下按钮 -->
<button type="button" id="nav-delivery" class="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm text-slate-400 hover:bg-white/5 hover:text-slate-200 transition-all border-0 cursor-pointer">
  <svg>...</svg>
  <span class="truncate">投放管理</span>
</button>
```

- [ ] **Step 2: 删除 tab-delivery HTML 区块**

删除 `<!-- ====== Tab: 投放管理 ====== -->` 到 `<!-- ====== Tab: Meta 数据 ====== -->` 之间的全部 HTML（约 70 行，lines 757-827）。

- [ ] **Step 3: 删除投放管理 JS 代码**

搜索并删除以下 JS 区块：
- `switchTab` 中的 `delivery` 路由处理
- `titles` 对象中的 `'delivery': '投放管理'`
- `allTabs` 数组中的 `'delivery'`
- `showStats` 中如果包含 delivery
- 所有投放相关函数（搜索 `delivery`、`queue`、`template` 关键词定位）

- [ ] **Step 4: 删除 nav-delivery 事件监听**

删除 `document.getElementById('nav-delivery').addEventListener(...)` 行

- [ ] **Step 5: 提交**

```bash
git add static/index.html
git commit -m "refactor: 删除投放管理页面（前端入口移除，后端保留）"
```

---

### Task 3: 合并两页 + 暗色框架

**Files:**
- Modify: `static/index.html`（创建 tab-meta，合并两个旧 tab 的 HTML）

**Interfaces:**
- Consumes: 旧的 `tab-meta-data` + `tab-meta-accounts` HTML 区块位置
- Produces: 新的 `tab-meta` 框架（左侧面板 + 右侧主区域 + 暗色主题）

- [ ] **Step 1: 删除旧的 tab-meta-data 和 tab-meta-accounts HTML**

删除 `<!-- ====== Tab: Meta 数据 ====== -->` 整块（约 65 行）
删除 `<!-- ====== Tab: 账户配置 ====== -->` 整块（约 35 行）

- [ ] **Step 2: 插入新的 tab-meta HTML 框架**

在两个旧 tab 的原位置插入：

```html
<!-- ====== Tab: Meta 管理中心 ====== -->
<div id="tab-meta" class="flex-1 flex overflow-hidden" hidden>
  <!-- 左侧面板 -->
  <div class="w-64 shrink-0 bg-slate-950 border-r border-slate-800 flex flex-col overflow-hidden">
    <div class="p-3 border-b border-slate-800">
      <h2 class="text-sm font-bold text-white">Meta 管理中心</h2>
    </div>
    <!-- 搜索 -->
    <div class="px-3 py-2">
      <input id="metaAccountSearch" type="text" placeholder="搜索账户..." class="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none">
    </div>
    <!-- 工具栏 -->
    <div class="px-3 py-1.5 flex gap-1.5">
      <button id="btnMetaDiscover" class="flex-1 rounded-lg bg-indigo-600 px-2 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-500 transition-all cursor-pointer">+ 发现账户</button>
      <button id="btnMetaConfig" class="w-8 h-8 rounded-lg bg-slate-800 text-slate-400 text-sm hover:bg-slate-700 hover:text-white transition-all flex items-center justify-center cursor-pointer border-0" title="API 配置">⚙</button>
    </div>
    <!-- 账户树 -->
    <div id="metaAccountTree" class="flex-1 overflow-y-auto px-2 py-1 text-xs"></div>
    <!-- 底部统计 -->
    <div id="metaAccountFooter" class="px-3 py-2 border-t border-slate-800 text-[10px] text-slate-500"></div>
  </div>

  <!-- 右侧主区域 -->
  <div class="flex-1 bg-slate-900 overflow-y-auto px-5 py-4">
    <!-- 工具栏 -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-1 bg-slate-800 rounded-lg p-0.5" id="metaDatePills">
        <button data-range="today" class="metaDatePillActive px-3 py-1.5 text-[11px] font-medium rounded-md transition-all bg-slate-700 text-white">今天</button>
        <button data-range="yesterday" class="px-3 py-1.5 text-[11px] font-medium rounded-md transition-all text-slate-400 hover:text-white">昨天</button>
        <button data-range="3" class="px-3 py-1.5 text-[11px] font-medium rounded-md transition-all text-slate-400 hover:text-white">近3天</button>
        <button data-range="7" class="px-3 py-1.5 text-[11px] font-medium rounded-md transition-all text-slate-400 hover:text-white">近7天</button>
        <button data-range="30" class="px-3 py-1.5 text-[11px] font-medium rounded-md transition-all text-slate-400 hover:text-white">近30天</button>
      </div>
      <div class="flex items-center gap-3">
        <select id="metaCompareBaseline" class="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-[11px] text-slate-300 focus:border-indigo-500 focus:outline-none cursor-pointer">
          <option value="yesterday">对比: 昨天</option>
          <option value="daybefore">对比: 前天</option>
          <option value="lastweek">对比: 上周同期</option>
          <option value="avg7" selected>对比: 前7天均值</option>
          <option value="avg30">对比: 前30天均值</option>
        </select>
        <button id="btnMetaSync" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-500 transition-all cursor-pointer">手动同步</button>
        <span id="metaSyncStatus" class="text-[11px] text-slate-500"></span>
      </div>
    </div>

    <!-- KPI 卡片 -->
    <div class="grid grid-cols-6 gap-3 mb-4" id="metaKpiRow"></div>

    <!-- 趋势图 + BM 汇总 -->
    <div class="grid grid-cols-3 gap-4 mb-4">
      <div class="col-span-2 card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">趋势</h3><div class="h-48"><canvas id="metaChartTrend"></canvas></div></div>
      <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">BM 汇总</h3><div id="metaBmTable" class="text-[11px]"></div></div>
    </div>

    <!-- 异常预警 + 账户排行 -->
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">异常预警</h3><div id="metaAnomalies" class="text-[11px]"></div></div>
      <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">账户排行</h3><div id="metaAccountRank" class="text-[11px]"></div></div>
    </div>

    <!-- 日报明细 -->
    <div class="card-dark p-4"><h3 class="text-xs font-semibold text-slate-300 mb-2">日报明细</h3><div id="metaDailyTable" class="text-[11px]"></div><div id="metaDailyPager" class="mt-2"></div></div>
  </div>
</div>
```

- [ ] **Step 3: 添加暗色主题 CSS**

在 `<style>` 标签中添加：

```css
.card-dark { background: #1e293b; border: 1px solid rgba(51,65,85,0.5); border-radius: 1rem; }
.meta-tree-item { padding: 6px 12px; border-radius: 8px; cursor: pointer; transition: all 0.15s; color: #94a3b8; }
.meta-tree-item:hover { background: rgba(255,255,255,0.05); color: #e2e8f0; }
.meta-tree-item.active { background: rgba(99,102,241,0.2); color: #a5b4fc; }
.meta-tree-bm { font-weight: 600; color: #e2e8f0; padding: 8px 12px 4px; text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
.meta-tree-account { padding-left: 20px; }
.meta-kpi-card { background: #1e293b; border: 1px solid rgba(51,65,85,0.5); border-radius: 1rem; padding: 16px; text-align: center; }
.meta-kpi-value { font-size: 1.5rem; font-weight: 700; color: #fff; margin-top: 2px; }
.meta-kpi-label { font-size: 10px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
.meta-kpi-change { font-size: 10px; margin-top: 4px; }
.meta-kpi-up { color: #34d399; }
.meta-kpi-down { color: #f87171; }
#metaDailyTable table, #metaAccountRank table, #metaBmTable table, #metaAnomalies table { width: 100%; border-collapse: collapse; }
#metaDailyTable th, #metaAccountRank th, #metaBmTable th, #metaAnomalies th { color: #94a3b8; text-align: left; padding: 6px 8px; font-weight: 500; border-bottom: 1px solid rgba(51,65,85,0.5); }
#metaDailyTable td, #metaAccountRank td, #metaBmTable td, #metaAnomalies td { color: #cbd5e1; padding: 5px 8px; border-bottom: 1px solid rgba(51,65,85,0.2); }
```

- [ ] **Step 4: 更新侧边栏导航**

把旧的 `nav-meta-data` 和 `nav-meta-accounts` 删除，替换为单个按钮：

```html
<button type="button" id="nav-meta" class="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm text-slate-400 hover:bg-white/5 hover:text-slate-200 transition-all border-0 cursor-pointer">
  <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 3v18h18"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16l4-4 4 4 4-6"/></svg>
  <span class="truncate">Meta 管理</span>
</button>
```

- [ ] **Step 5: 更新 JS 路由**

更新 `allTabs`、`titles`、`switchTab` 等引用，把 `'meta-data'` 和 `'meta-accounts'` 替换为 `'meta'`，删除 delivery 引用。

- [ ] **Step 6: 提交**

```bash
git add static/index.html
git commit -m "feat: 合并Meta数据+账户配置为Meta管理中心，暗色主题框架"
```

---

### Task 4: 左侧账户树 + 发现导入面板

**Files:**
- Modify: `static/index.html`（`renderMetaAccountTree` 函数 + 发现导入面板 JS）

**Interfaces:**
- Consumes: `GET /api/meta/accounts`、`POST /api/meta/discover`、`POST /api/meta/accounts/import`
- Produces: `renderMetaAccountTree()`、`openDiscoverPanel()`、`importSelectedAccounts()`

- [ ] **Step 1: 实现账户树渲染函数**

```javascript
var _metaAccounts = [];
var _metaSelectedAccount = null;  // null=全部, 'bm:xxx'=按BM过滤, 'act_xxx'=按账户过滤

function loadMetaAccountTree() {
  fetch('/api/meta/accounts').then(function(r) { return r.json(); }).then(function(data) {
    _metaAccounts = data.accounts || data || [];
    renderMetaAccountTree();
  });
}

function renderMetaAccountTree(filter) {
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
  // 按 BM 分组
  var groups = {};
  accounts.forEach(function(a) {
    var bm = a.pingykj_account || '未归类';
    if (!groups[bm]) groups[bm] = [];
    groups[bm].push(a);
  });

  var html = '';
  // 全部账户入口
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
```

- [ ] **Step 2: 实现发现/导入面板**

在 JS 中添加：

```javascript
var _discoveredMetaAccounts = [];

function openDiscoverPanel() {
  var token = document.getElementById('meta-access-token') ? document.getElementById('meta-access-token').value.trim() : '';
  var html = '<div id="discoverOverlay" class="fixed inset-0 z-[140] flex items-center justify-center bg-black/60" onclick="if(event.target===this)closeDiscoverPanel()">' +
    '<div class="bg-slate-800 rounded-2xl p-5 w-[480px] max-h-[80vh] overflow-y-auto border border-slate-700 shadow-2xl">' +
    '<h3 class="text-sm font-bold text-white mb-3">发现账户</h3>' +
    '<input id="discoverTokenInput" type="password" class="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400 mb-3" placeholder="输入你的 Access Token" value="' + (token || '') + '">' +
    '<button onclick="doDiscoverAccounts()" class="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 mb-3 cursor-pointer">发现账户</button>' +
    '<div id="discoverResultList"></div>' +
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
```

- [ ] **Step 3: 提交**

```bash
git add static/index.html
git commit -m "feat: 左侧账户树按BM分组 + 发现导入弹窗"
```

---

### Task 5: KPI 卡片 + 趋势图暗色主题

**Files:**
- Modify: `static/index.html`（`refreshMetaDashboard` 函数重写）

**Interfaces:**
- Consumes: `GET /api/meta/summary`、`GET /api/meta/trend`、`GET /api/meta/bm-summary`
- Produces: `refreshMetaDashboard()`、`renderMetaKpis()`、`renderMetaChart()`

- [ ] **Step 1: 实现 KPI 卡片渲染**

```javascript
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
    var valStr = k.format + (k.compact ? (k.value >= 1000 ? (k.value/1000).toFixed(1)+'K' : k.value.toLocaleString()) : k.value.toLocaleString(undefined, {minimumFractionDigits: k.digits, maximumFractionDigits: k.digits})) + (k.suffix || '');
    html += '<div class="meta-kpi-card">' +
      '<div class="meta-kpi-label">' + k.label + '</div>' +
      '<div class="meta-kpi-value">' + valStr + '</div>' +
      '<div class="meta-kpi-change ' + changeClass + '">' + arrow + ' ' + Math.abs(change).toFixed(1) + '%</div>' +
      '</div>';
  });
  document.getElementById('metaKpiRow').innerHTML = html;
}
```

- [ ] **Step 2: 实现暗色趋势图**

```javascript
var _metaChart = null;

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
        {
          label: '消耗',
          data: trendData.map(function(d) { return d.spend || 0; }),
          backgroundColor: 'rgba(99,102,241,0.6)',
          borderColor: 'rgba(99,102,241,0)',
          borderRadius: 4,
          yAxisID: 'y',
          order: 2,
        },
        {
          label: 'ROI',
          data: trendData.map(function(d) { return d.roi || 0; }),
          type: 'line',
          borderColor: '#34d399',
          backgroundColor: 'rgba(52,211,153,0)',
          pointBackgroundColor: '#34d399',
          pointRadius: 2,
          tension: 0.3,
          yAxisID: 'y1',
          order: 1,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        y: { type: 'linear', position: 'left', grid: { color: 'rgba(51,65,85,0.2)' }, ticks: { callback: function(v) { return '$' + (v >= 1000 ? (v/1000).toFixed(1)+'K' : v); } } },
        y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, ticks: { callback: function(v) { return v.toFixed(1) + 'x'; } } },
        x: { grid: { display: false } }
      },
      plugins: { legend: { labels: { boxWidth: 12, padding: 16, font: { size: 10 } } } }
    }
  });
}
```

- [ ] **Step 3: 实现主刷新函数**

```javascript
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
  var today = new Date();
  var start, end;
  if (baseline === 'yesterday') {
    today.setDate(today.getDate()-2);
    start = end = today.toISOString().slice(0,10);
  } else if (baseline === 'daybefore') {
    today.setDate(today.getDate()-3);
    start = end = today.toISOString().slice(0,10);
  } else if (baseline === 'lastweek') {
    end = new Date(today); end.setDate(end.getDate()-7); end = end.toISOString().slice(0,10);
    start = end;
  } else if (baseline === 'avg7') {
    end = new Date(today); end.setDate(end.getDate()-1); end = end.toISOString().slice(0,10);
    start = new Date(today); start.setDate(start.getDate()-8); start = start.toISOString().slice(0,10);
  } else if (baseline === 'avg30') {
    end = new Date(today); end.setDate(end.getDate()-1); end = end.toISOString().slice(0,10);
    start = new Date(today); start.setDate(start.getDate()-31); start = start.toISOString().slice(0,10);
  }
  return { start: start, end: end };
}

async function refreshMetaDashboard() {
  var range = getMetaDateRange();
  var compareRange = getCompareDateRange();
  var params = '?start=' + range.start + '&end=' + range.end;
  var compareParams = '?start=' + compareRange.start + '&end=' + compareRange.end;
  // 如果有选中账户，加过滤
  if (_metaSelectedAccount) {
    if (_metaSelectedAccount.indexOf('bm:') === 0) {
      // BM 过滤：传 bm 参数
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
      fetch('/api/meta/trend' + params.replace('start=', 'days=30&start=')).then(function(r) { return r.json(); }),
      fetch('/api/meta/bm-summary' + params).then(function(r) { return r.json(); }),
      fetch('/api/meta/daily-stats' + params + '&page_size=10').then(function(r) { return r.json(); }),
      fetch('/api/meta/account-ranking' + params).then(function(r) { return r.json(); }),
    ]);
    renderMetaKpis(summaryRes, compareRes);
    renderMetaChart(trendRes.data || trendRes);
    renderBmTable(bmRes.bm_summary || []);
    renderMetaAnomalies(summaryRes, compareRes, dailyRes.data || dailyRes);
    renderAccountRank(rankRes.data || rankRes);
    renderMetaDaily(dailyRes.data || dailyRes);
  } catch(e) { console.error('Meta dashboard refresh error:', e); }
}
```

- [ ] **Step 4: 绑定日期快捷键和对比选择器事件**

```javascript
document.querySelectorAll('#metaDatePills button').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('#metaDatePills button').forEach(function(b) { b.classList.remove('metaDatePillActive', 'bg-slate-700', 'text-white'); b.classList.add('text-slate-400'); });
    this.classList.add('metaDatePillActive', 'bg-slate-700', 'text-white');
    this.classList.remove('text-slate-400');
    refreshMetaDashboard();
  });
});
document.getElementById('metaCompareBaseline').addEventListener('change', function() { refreshMetaDashboard(); });
document.getElementById('btnMetaSync').addEventListener('click', triggerMetaSync);
```

- [ ] **Step 5: 提交**

```bash
git add static/index.html
git commit -m "feat: Meta暗色KPI卡片+趋势图+日期切换+对比基准联动"
```

---

### Task 6: BM 汇总 + 异常预警 + 日报/排行暗色改造

**Files:**
- Modify: `static/index.html`（渲染函数）

**Interfaces:**
- Consumes: KPI/BM/日报/排行 API 数据
- Produces: `renderBmTable()`、`renderMetaAnomalies()`、`renderAccountRank()`、`renderMetaDaily()`

- [ ] **Step 1: BM 汇总表**

```javascript
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
```

- [ ] **Step 2: 异常预警**

```javascript
function renderMetaAnomalies(summary, compareSummary, dailyData) {
  var anomalies = [];
  // 消耗下降 > 50%
  var spendChange = compareSummary.total_spend > 0 ? ((summary.total_spend - compareSummary.total_spend) / compareSummary.total_spend * 100) : 0;
  if (spendChange < -50) anomalies.push({ level: '🔴', type: '消耗骤降', detail: '下降 ' + Math.abs(spendChange).toFixed(0) + '%', current: '$' + (summary.total_spend || 0).toLocaleString(), prev: '$' + (compareSummary.total_spend || 0).toLocaleString() });
  // ROI 下降 > 30%
  var roiChange = compareSummary.roi > 0 ? ((summary.roi - compareSummary.roi) / compareSummary.roi * 100) : 0;
  if (roiChange < -30) anomalies.push({ level: '🟡', type: 'ROI下降', detail: '下降 ' + Math.abs(roiChange).toFixed(0) + '%', current: (summary.roi || 0).toFixed(2) + 'x', prev: (compareSummary.roi || 0).toFixed(2) + 'x' });
  // CPA 翻倍
  var cpaChange = compareSummary.cpa > 0 ? ((summary.cpa - compareSummary.cpa) / compareSummary.cpa * 100) : 0;
  if (cpaChange > 100) anomalies.push({ level: '🟠', type: 'CPA翻倍', detail: '上涨 ' + cpaChange.toFixed(0) + '%', current: '$' + (summary.cpa || 0).toFixed(2), prev: '$' + (compareSummary.cpa || 0).toFixed(2) });
  // 单账户维度（遍历 dailyData）
  var accountAnomalies = {};
  (dailyData || []).forEach(function(d) {
    var key = d.ad_account || d.act_id;
    if (!accountAnomalies[key]) accountAnomalies[key] = { spend: 0 };
    accountAnomalies[key].spend += (d.total_spend || 0);
  });
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
```

- [ ] **Step 3: 账户排行 + 日报暗色表格**

```javascript
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
```

- [ ] **Step 4: 提交**

```bash
git add static/index.html
git commit -m "feat: BM汇总+异常预警+暗色日报/排行表格"
```

---

### Task 7: API 配置弹窗 + 收尾清理

**Files:**
- Modify: `static/index.html`（配置弹窗 + 旧代码清理 + 事件绑定）

- [ ] **Step 1: API 配置弹窗**

```javascript
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
```

- [ ] **Step 2: 更新 switchTab 和初始化**

在 `switchTab` 函数中：
```javascript
if (tab === 'meta') {
  loadMetaAccountTree();
  refreshMetaDashboard();
}
```

更新 `titles` 对象、`allTabs` 数组、`showStats` 逻辑，删除 `'meta-data'`、`'meta-accounts'`、`'delivery'`。

更新导航事件监听：
```javascript
document.getElementById('nav-meta').addEventListener('click', function() { switchTab('meta'); });
```

删除旧的 `nav-meta-data`、`nav-meta-accounts`、`nav-delivery` 事件监听。

- [ ] **Step 3: 清理旧的 Meta JS 函数**

删除以下不再需要的旧函数（搜索定位后移除）：
- 旧的 `refreshMetaDashboard`、`renderMetaKpis` 等函数
- `loadMetaAccounts`、`discoverAccounts`、`importSelectedAccounts` 等旧实现
- `triggerMetaSync`（保留但需适配新的 sync status 元素 ID）
- `loadMetaConfig`、`saveMetaConfig` 旧实现
- 旧的 `switchTab` 中对 `meta-data`、`meta-accounts`、`delivery` 的路由

保留 `triggerMetaSync`，更新它对 `metaSyncStatus` 的引用。

- [ ] **Step 4: 修改 triggerMetaSync 适配新 UI**

```javascript
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
```

- [ ] **Step 5: 验证语法 + 提交**

```bash
git add static/index.html
git commit -m "feat: API配置弹窗+旧代码清理+事件绑定+收尾"
```

---

### Task 8: 最终验证与推送

- [ ] **Step 1: 检查所有引用一致性**

```bash
cd e:/xiangmu/5-28 && grep -n "tab-meta-data\|tab-meta-accounts\|tab-delivery\|nav-meta-data\|nav-meta-accounts\|nav-delivery" static/index.html
```
预期：无残留引用。

- [ ] **Step 2: 确认后端语法**

```bash
cd e:/xiangmu/5-28 && python -c "import py_compile; py_compile.compile('main.py', doraise=True); print('OK')"
```

- [ ] **Step 3: 启动测试**

启动服务，点击 Meta 管理 Tab，确认左侧面板和右侧区域正常渲染，无 JS 报错。

- [ ] **Step 4: 提交**

```bash
git add -A && git status
git commit -m "chore: 最终验证，清理残留引用"
```
