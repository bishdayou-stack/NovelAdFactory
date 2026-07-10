# 广告系列页面投放状态展示 — 设计文档

**日期**: 2026-07-10  
**状态**: 已确认  
**涉及文件**: `analytics.py`, `static/index.html`

## 需求概述

广告系列页面（`tab-meta-campaign`）当前展示系列/广告组/广告三层的消耗、转化、ROI 等性能指标，但缺少投放状态。现需在表格第一列增加彩色状态标签，显示 Meta 广告实体的 `effective_status` 和 `status`。

**仅展示，不修改。** 状态数据在现有定时/手动同步中顺带拉取，无需额外 API。

## 数据来源

`meta_entity_status` 表（已存在），`_sync_meta_statuses()` 在每次 Meta Insights 同步时写入。三层分别存储：

| level | 对应 entity_id | 数据量（1个账户实测） |
|-------|---------------|---------------------|
| campaign | campaign_id | 12 |
| adset | adset_id | 445 |
| ad | ad_id | 475 |

## 后端改动：`analytics.py`

三个查询函数各加 LEFT JOIN `meta_entity_status`，按 `entity_id` + `level` 匹配：

### `meta_campaigns()` — 系列层级
```sql
-- 原有查询：从 meta_adset_stats GROUP BY campaign_id
-- 新增 LEFT JOIN：
LEFT JOIN meta_entity_status s
  ON m.campaign_id = s.entity_id AND s.level = 'campaign'
-- 新增返回字段：s.effective_status, s.status
```

### `meta_adsets()` — 广告组层级
```sql
LEFT JOIN meta_entity_status s
  ON m.adset_id = s.entity_id AND s.level = 'adset'
```

### `meta_ads()` — 广告层级
```sql
LEFT JOIN meta_entity_status s
  ON m.ad_id = s.entity_id AND s.level = 'ad'
```

三个函数均在返回 dict 中添加 `effective_status` 和 `status` 字段（可能为 None，前端兜底为 "未知"）。

## 前端改动：`static/index.html`

### 表格列结构

状态列作为第一列，放在实体名称之前：

```
┌──────────┬───────────────────────────┬────────┬──────┬─────┐
│ 投放状态  │ 广告系列                   │ 消耗    │ 转化  │ ... │
├──────────┼───────────────────────────┼────────┼──────┼─────┤
│ 🟢 投放中 │ 系列名 / ID               │ $12.50 │ 3    │ ... │
│ 🟡 已暂停 │ 系列名 / ID               │ $0.00  │ 0    │ ... │
└──────────┴───────────────────────────┴────────┴──────┴─────┘
```

### 状态标签（Badge）组件

新增 JS 函数 `renderStatusBadge(effectiveStatus, status)`：

**主标签**：圆角彩色标签，显示 `effective_status` 的中文映射

| effective_status | 中文 | 颜色 | CSS 类 |
|---|---|---|---|
| ACTIVE | 投放中 | #22c55e 绿 | `status-active` |
| PAUSED | 已暂停 | #eab308 黄 | `status-paused` |
| ARCHIVED | 已归档 | #6b7280 灰 | `status-archived` |
| DELETED | 已删除 | #ef4444 红 | `status-deleted` |
| IN_REVIEW | 审核中 | #3b82f6 蓝 | `status-review` |
| ADS_IN_REVIEW | 广告审核中 | #3b82f6 蓝 | `status-review` |
| CAMPAIGN_PAUSED | 系列暂停 | #eab308 黄 | `status-paused` |
| ADSET_PAUSED | 广告组暂停 | #eab308 黄 | `status-paused` |
| WITH_ISSUES | 有问题 | #f97316 橙 | `status-issues` |
| 其他 / null | 显示原文或"未知" | #6b7280 灰 | `status-unknown` |

**状态差异提示**：当 `status ≠ effective_status` 时，标签下方显示一行灰色小字：
> *用户设为：投放中*

### 修改的渲染函数

1. **`campLoadTable()`** — 主表格渲染：表头加"投放状态"列，每行加 `renderStatusBadge()`
2. **`campPageToggle()`** — 展开广告组子表格：同上
3. **`campAdsetToggle()`** — 展开广告子表格：同上

### CSS 新增

```css
.status-badge { display:inline-block; padding:2px 10px; border-radius:12px;
  font-size:12px; font-weight:600; color:#fff; white-space:nowrap; }
.status-active { background:#22c55e; }
.status-paused { background:#eab308; color:#333; }
.status-archived { background:#6b7280; }
.status-deleted { background:#ef4444; }
.status-review { background:#3b82f6; }
.status-issues { background:#f97316; }
.status-unknown { background:#6b7280; }
.status-hint { font-size:10px; color:#9ca3af; display:block; }
```

## 同步：无需改动

`scraper.py` 中的 `_sync_meta_statuses()` 已在 `_sync_one_meta_account()` 末尾被调用（每账户 Insights 同步后立即拉取状态），无需额外触发。

## 不涉及

- 不新增 API 端点
- 不新增数据库表
- 不支持状态修改
- 不支持批量选择
