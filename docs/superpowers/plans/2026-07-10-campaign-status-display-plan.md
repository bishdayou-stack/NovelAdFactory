# 广告系列页面投放状态展示 — 实施计划

> **For agentic workers:** 使用 superpowers:subagent-driven-development 或 superpowers:executing-plans 按任务执行。步骤使用 checkbox (`- [ ]`) 跟踪。

**Goal:** 广告系列页面（系列/广告组/广告三层）第一列展示彩色中文投放状态标签。

**Architecture:** 后端 analytics.py 三个查询函数各加 LEFT JOIN `meta_entity_status` 表，前端新增 `renderStatusBadge()` 函数在三种表格行渲染中调用，CSS 新增6种状态色。

**Tech Stack:** Python + SQLite（后端），原生 JS + Tailwind CSS（前端）

## 全局约束

- 仅展示，不修改状态
- 状态数据在定时/手动 Meta Insights 同步时自然带上（`_sync_meta_statuses` 已存在于 scraper.py）
- `effective_status` 为主标签色，`status ≠ effective_status` 时下方灰色小字提示
- 状态中文映射表覆盖 Meta 常见状态值，未匹配回退显示英文原文
- 三层（campaign / adset / ad）使用同一套 Badge 逻辑

---

### Task 1: 后端 — `meta_campaigns()` 加 LEFT JOIN

**Files:**
- Modify: `analytics.py:688-725`

**Interfaces:**
- Consumes: `meta_entity_status` 表（entity_id, level, effective_status, status, user_id）
- Produces: 返回 dict 新增 `effective_status` (str|None) 和 `status` (str|None)

- [ ] **Step 1: 修改 SQL 为子查询 + LEFT JOIN**

将 [analytics.py:704-715](analytics.py#L704-L715) 的 SQL 改为子查询包裹，外层 LEFT JOIN `meta_entity_status`：

```python
        sql = f"""
            SELECT agg.*, es.effective_status, es.status
            FROM (
                SELECT campaign_id, MAX(campaign_name) AS campaign_name, MAX(ad_account) AS ad_account,
                    COALESCE(SUM(spend),0) AS spend,
                    COALESCE(SUM(impressions),0) AS impressions,
                    COALESCE(SUM(clicks),0) AS clicks,
                    COALESCE(SUM(purchases),0) AS purchases,
                    COALESCE(SUM(purchase_value),0) AS purchase_value
                FROM meta_adset_stats
                WHERE {' AND '.join(where)}
                GROUP BY campaign_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.campaign_id = es.entity_id
                AND es.level = 'campaign'
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY spend DESC"
```

- [ ] **Step 2: 在结果构建中提取状态字段**

修改 [analytics.py:718-724](analytics.py#L718-L724) 的 `for r in rows:` 循环，在 `out.append(m)` 前加入：

```python
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
```

完整循环变为：
```python
        out = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["campaign_id"] = r["campaign_id"]
            m["campaign_name"] = r["campaign_name"] or r["campaign_id"] or "(未命名系列)"
            m["ad_account"] = r["ad_account"]
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
            out.append(m)
```

- [ ] **Step 3: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
from analytics import meta_campaigns
rows = meta_campaigns('act_935917789227134', user_id=2)
for r in rows[:3]:
    print(f'{r[\"campaign_name\"][:30]} | effective: {r[\"effective_status\"]} | status: {r[\"status\"]}')
"
```

预期：打印3条记录，每条含 `effective_status` 和 `status` 字段。

- [ ] **Step 4: 提交**

```bash
git add analytics.py
git commit -m "feat: meta_campaigns LEFT JOIN meta_entity_status 返回投放状态"
```

---

### Task 2: 后端 — `meta_adsets()` 加 LEFT JOIN

**Files:**
- Modify: `analytics.py:728-764`

**Interfaces:**
- Consumes: `meta_entity_status` 表
- Produces: 返回 dict 新增 `effective_status` (str|None) 和 `status` (str|None)

- [ ] **Step 1: 修改 SQL 为子查询 + LEFT JOIN**

将 [analytics.py:741-753](analytics.py#L741-L753) 的 SQL 改为：

```python
        sql = f"""
            SELECT agg.*, es.effective_status, es.status
            FROM (
                SELECT adset_id, MAX(adset_name) AS adset_name,
                    campaign_id, MAX(campaign_name) AS campaign_name,
                    COALESCE(SUM(spend),0) AS spend,
                    COALESCE(SUM(impressions),0) AS impressions,
                    COALESCE(SUM(clicks),0) AS clicks,
                    COALESCE(SUM(purchases),0) AS purchases,
                    COALESCE(SUM(purchase_value),0) AS purchase_value
                FROM meta_adset_stats
                WHERE {' AND '.join(where)}
                GROUP BY adset_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.adset_id = es.entity_id
                AND es.level = 'adset'
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY spend DESC"
```

- [ ] **Step 2: 在结果构建中提取状态字段**

修改 [analytics.py:757-763](analytics.py#L757-L763) 的循环：

```python
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
            out.append(m)
```

- [ ] **Step 3: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
from analytics import meta_adsets
rows = meta_adsets('act_935917789227134', user_id=2)
for r in rows[:3]:
    print(f'{r[\"adset_name\"][:30]} | effective: {r[\"effective_status\"]} | status: {r[\"status\"]}')
"
```

- [ ] **Step 4: 提交**

```bash
git add analytics.py
git commit -m "feat: meta_adsets LEFT JOIN meta_entity_status 返回投放状态"
```

---

### Task 3: 后端 — `meta_ads()` 加 LEFT JOIN

**Files:**
- Modify: `analytics.py:767-811`

**Interfaces:**
- Consumes: `meta_entity_status` 表
- Produces: 返回 dict 新增 `effective_status` (str|None) 和 `status` (str|None)

- [ ] **Step 1: 修改 SQL，在已有 LEFT JOIN 基础上再加一个 LEFT JOIN**

[analytics.py:781-797](analytics.py#L781-L797) 已有 `LEFT JOIN meta_ad_creatives c`，再追加 `meta_entity_status`：

```python
        sql = f"""
            SELECT agg.*, es.effective_status, es.status
            FROM (
                SELECT s.ad_id, MAX(s.ad_name) AS ad_name,
                    s.adset_id, MAX(s.adset_name) AS adset_name,
                    s.campaign_id, MAX(s.campaign_name) AS campaign_name,
                    COALESCE(SUM(s.spend),0) AS spend,
                    COALESCE(SUM(s.impressions),0) AS impressions,
                    COALESCE(SUM(s.clicks),0) AS clicks,
                    COALESCE(SUM(s.purchases),0) AS purchases,
                    COALESCE(SUM(s.purchase_value),0) AS purchase_value,
                    MAX(c.local_path) AS local_path,
                    MAX(c.thumbnail_url) AS thumbnail_url,
                    MAX(c.video_id) AS video_id
                FROM meta_ad_stats s
                LEFT JOIN meta_ad_creatives c ON c.ad_id = s.ad_id AND c.user_id = s.user_id
                WHERE {' AND '.join(where)}
                GROUP BY s.ad_id
            ) agg
            LEFT JOIN meta_entity_status es ON agg.ad_id = es.entity_id
                AND es.level = 'ad'
        """
        if user_id is not None:
            sql += "\n            AND es.user_id = ?"
            params.append(user_id)
        sql += "\n            ORDER BY spend DESC"
```

- [ ] **Step 2: 在结果构建中提取状态字段**

修改 [analytics.py:801-811](analytics.py#L801-L811) 的循环：

```python
        out = []
        for r in rows:
            m = _row_metrics(r["spend"], r["impressions"], r["clicks"],
                             r["purchases"], r["purchase_value"])
            m["ad_id"] = r["ad_id"]
            m["ad_name"] = r["ad_name"] or r["ad_id"] or "(未命名广告)"
            m["adset_id"] = r["adset_id"]
            m["campaign_name"] = r["campaign_name"] or ""
            m["thumb"] = ("/static/" + r["local_path"]) if r["local_path"] else (r["thumbnail_url"] or "")
            m["video_id"] = r["video_id"] or ""
            m["effective_status"] = r["effective_status"] if "effective_status" in r.keys() else None
            m["status"] = r["status"] if "status" in r.keys() else None
            out.append(m)
```

- [ ] **Step 3: 验证**

```bash
cd e:\xiangmu\5-28 && python -c "
from analytics import meta_ads
rows = meta_ads('act_935917789227134', user_id=2)
for r in rows[:3]:
    print(f'{r[\"ad_name\"][:30]} | effective: {r[\"effective_status\"]} | status: {r[\"status\"]} | thumb: {bool(r[\"thumb\"])}')
"
```

- [ ] **Step 4: 提交**

```bash
git add analytics.py
git commit -m "feat: meta_ads LEFT JOIN meta_entity_status 返回投放状态"
```

---

### Task 4: 前端 — CSS + `renderStatusBadge()` 函数

**Files:**
- Modify: `static/index.html` — CSS 区域（第 46 行之前）+ JS 区域（`metaMetricCells` 之前）

**Interfaces:**
- Produces: `STATUS_MAP` 对象（全局）+ `renderStatusBadge(effectiveStatus, status)` 函数，返回 HTML 字符串

- [ ] **Step 1: 在 CSS 区（`</style>` 前，第 46 行之前）新增状态标签样式**

```css
    .status-badge { display:inline-block; padding:2px 10px; border-radius:12px;
      font-size:12px; font-weight:600; color:#fff; white-space:nowrap;
      text-align:center; min-width:56px; }
    .status-active { background:#22c55e; }
    .status-paused { background:#eab308; color:#333; }
    .status-archived { background:#6b7280; }
    .status-deleted { background:#ef4444; }
    .status-review { background:#3b82f6; }
    .status-issues { background:#f97316; }
    .status-unknown { background:#6b7280; }
    .status-hint { font-size:10px; color:#9ca3af; display:block; line-height:1.2; }
```

- [ ] **Step 2: 在 `metaMetricCells` 函数之前（约第 4868 行）新增状态映射和渲染函数**

```javascript
    var STATUS_MAP = {
      'ACTIVE':          {label:'投放中',   cls:'status-active'},
      'PAUSED':          {label:'已暂停',   cls:'status-paused'},
      'ARCHIVED':        {label:'已归档',   cls:'status-archived'},
      'DELETED':         {label:'已删除',   cls:'status-deleted'},
      'IN_REVIEW':       {label:'审核中',   cls:'status-review'},
      'ADS_IN_REVIEW':   {label:'广告审核中', cls:'status-review'},
      'CAMPAIGN_PAUSED': {label:'系列暂停',   cls:'status-paused'},
      'ADSET_PAUSED':    {label:'广告组暂停', cls:'status-paused'},
      'WITH_ISSUES':     {label:'有问题',   cls:'status-issues'},
    };

    function renderStatusBadge(effectiveStatus, status) {
      var eff = (effectiveStatus || '').toString().toUpperCase();
      var st  = (status || '').toString().toUpperCase();
      var info = STATUS_MAP[eff] || {label: eff || '未知', cls:'status-unknown'};
      var html = '<td class="text-center"><span class="status-badge ' + info.cls + '">' + info.label + '</span>';
      if (st && eff && st !== eff) {
        var stInfo = STATUS_MAP[st] || {label: st};
        html += '<span class="status-hint">用户设为：' + stInfo.label + '</span>';
      }
      html += '</td>';
      return html;
    }
```

- [ ] **Step 3: 验证 — 浏览器 console 快速测试**

在浏览器 console 执行：
```javascript
renderStatusBadge('ACTIVE', 'ACTIVE')
// 预期: '<td class="text-center"><span class="status-badge status-active">投放中</span></td>'

renderStatusBadge('ADSET_PAUSED', 'ACTIVE')
// 预期: 含 "用户设为：投放中" 灰色小字
```

- [ ] **Step 4: 提交**

```bash
git add static/index.html
git commit -m "feat: 新增投放状态 Badge CSS + renderStatusBadge JS 函数"
```

---

### Task 5: 前端 — `campLoadTable()` 加状态列

**Files:**
- Modify: `static/index.html:5014-5043`

- [ ] **Step 1: 表头加"投放状态"列**

将 [line 5024-5028](static/index.html#L5024-L5028) 的表头行从：
```javascript
        var html = '<table class="w-full"><thead><tr>' +
          '<th class="text-left">广告系列</th><th class="text-right">消耗</th>...
```
改为（在"广告系列"前插入"投放状态"列，并调整 colspan）：
```javascript
        var html = '<table class="w-full"><thead><tr>' +
          '<th class="text-center" style="width:80px">投放状态</th>' +
          '<th class="text-left">广告系列</th><th class="text-right">消耗</th><th class="text-right">转化</th>' +
          '<th class="text-right">转化金额</th><th class="text-right">ROI</th><th class="text-right">转化成本</th>' +
          '<th class="text-right">千展成本</th><th class="text-right">点击率</th><th class="text-right">展示</th><th class="text-right">点击</th>' +
          '</tr></thead><tbody>';
```

- [ ] **Step 2: 数据行在名称前插入状态单元格**

将 [line 5034-5036](static/index.html#L5034-L5036) 的行渲染改为：
```javascript
          html += '<tr class="cursor-pointer hover:bg-white/5" onclick="campPageToggle(this,\'' + encodeURIComponent(cid) + '\',\'' + cacct + '\')">' +
            renderStatusBadge(c.effective_status, c.status) +
            '<td class="text-left"><span class="camp-arrow inline-block w-3 text-slate-500">▶</span> ' + acctBadge + escapeHtml(c.campaign_name || '(未命名)') + '</td>' +
            metaMetricCells(c) + '</tr>';
```

- [ ] **Step 3: 展开隐藏行 colspan 从 10 改为 11**

将 [line 5037](static/index.html#L5037)：
```javascript
          html += '<tr class="camp-adsets" hidden><td colspan="10" class="p-0"><div class="adset-slot"></div></td></tr>';
```
改为：
```javascript
          html += '<tr class="camp-adsets" hidden><td colspan="11" class="p-0"><div class="adset-slot"></div></td></tr>';
```

- [ ] **Step 4: 空数据行 colspan 从 10 改为 11**

将 [line 5039](static/index.html#L5039)：
```javascript
        if (!rows.length) html += '<tr><td colspan="10" class="text-center text-slate-500 py-6">该账户暂无系列数据（需同步后生成）</td></tr>';
```
改为：
```javascript
        if (!rows.length) html += '<tr><td colspan="11" class="text-center text-slate-500 py-6">该账户暂无系列数据（需同步后生成）</td></tr>';
```

- [ ] **Step 5: 验证 — 启动服务器，打开广告系列页面**

```bash
cd e:\xiangmu\5-28 && python main.py
```
浏览器打开 `http://127.0.0.1:8000/static/index.html`，切换到"广告系列"标签，选择账户，确认：
- 表头出现"投放状态"列
- 每行显示彩色中文状态标签
- 点击展开广告组，子表格也有状态列

- [ ] **Step 6: 提交**

```bash
git add static/index.html
git commit -m "feat: campLoadTable 表格第一列显示投放状态标签"
```

---

### Task 6: 前端 — `campPageToggle()` + `campAdsetToggle()` 加状态列

**Files:**
- Modify: `static/index.html:5045-5073` (campPageToggle), `5075-5101` (campAdsetToggle)

- [ ] **Step 1: `campPageToggle()` 广告组行加状态**

将 [line 5063-5065](static/index.html#L5063-L5065) 的行渲染改为：
```javascript
          html += '<tr class="text-slate-300 cursor-pointer hover:bg-white/5" onclick="campAdsetToggle(this,\'' + encodeURIComponent(aid) + '\',\'' + acct + '\')">' +
            renderStatusBadge(a.effective_status, a.status) +
            '<td class="text-left pl-6"><span class="aset-arrow inline-block w-3 text-slate-500">▶</span> ' + escapeHtml(a.adset_name || '(未命名组)') + '</td>' +
            metaMetricCells(a) + '</tr>';
```

将 [line 5066](static/index.html#L5066) 的 colspan 从 10 改为 11：
```javascript
          html += '<tr class="aset-ads" hidden><td colspan="11" class="p-0"><div class="ad-slot"></div></td></tr>';
```

将 [line 5068](static/index.html#L5068) 的空行 colspan 从 10 改为 11：
```javascript
        if (!rows.length) html += '<tr><td colspan="11" class="text-center text-slate-500 py-2 text-[10px]">无广告组数据</td></tr>';
```

- [ ] **Step 2: `campAdsetToggle()` 广告行加状态**

将 [line 5092-5094](static/index.html#L5092-L5094) 的行渲染改为：
```javascript
          html += '<tr class="text-slate-400">' +
            renderStatusBadge(a.effective_status, a.status) +
            '<td class="text-left pl-8">' + thumb + escapeHtml(a.ad_name || '(未命名广告)') + '</td>' +
            metaMetricCells(a) + '</tr>';
```

将 [line 5096](static/index.html#L5096) 的 colspan 从 10 改为 11：
```javascript
        if (!rows.length) html += '<tr><td colspan="11" class="text-center text-slate-500 py-2 text-[10px]">无广告数据</td></tr>';
```

- [ ] **Step 3: 调整 CSS 的 `#campPageTable th:first-child`**

当前 [line 46](static/index.html#L46) 的 CSS 规则 `#campPageTable th:first-child, #campPageTable td:first-child { width: 30%; }` 现在 `first-child` 变成了状态列而非名称列。需更新为：

```css
#campPageTable th:first-child, #campPageTable td:first-child { width: 80px; }
```

- [ ] **Step 4: 验证 — 完整三层展开**

打开广告系列页面 → 选择账户 → 展开系列（看到广告组状态）→ 展开广告组（看到广告状态），确认：
- 三层均有彩色状态标签
- 状态差异时出现"用户设为：xxx"提示
- 无状态数据时显示灰色"未知"

- [ ] **Step 5: 提交**

```bash
git add static/index.html
git commit -m "feat: campPageToggle + campAdsetToggle 加投放状态列，三层完整覆盖"
```

---

### Task 7: 端到端验证 + 同步确认

- [ ] **Step 1: 确保同步覆盖所有账户的状态**

```bash
cd e:\xiangmu\5-28 && python -c "
import sqlite3
conn = sqlite3.connect('data/dashboard.db')
c = conn.cursor()
c.execute('SELECT COUNT(DISTINCT ad_account) FROM meta_entity_status')
print('已同步状态的账户数:', c.fetchone()[0])
c.execute('SELECT level, COUNT(*) FROM meta_entity_status GROUP BY level')
print('各层级记录数:', c.fetchall())
conn.close()
"
```

如果账户数远小于 42，手动触发一次 Meta 同步以填充状态数据：
```bash
cd e:\xiangmu\5-28 && python -c "
from scraper import sync_all_meta_insights
sync_all_meta_insights(user_id=2)
"
```

- [ ] **Step 2: 前端完整验收**

打开页面验证以下场景：
1. 系列层状态正常显示
2. 展开广告组 — 每组显示各自状态
3. 展开广告 — 每条广告显示各自状态
4. `effective_status ≠ status` 时出现"用户设为"提示
5. 多账户 BM 聚合模式（逗号分隔 account）下状态正常
6. 状态为 null/空时显示"未知"

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "chore: 端到端验证通过，投放状态三层展示完成"
```
