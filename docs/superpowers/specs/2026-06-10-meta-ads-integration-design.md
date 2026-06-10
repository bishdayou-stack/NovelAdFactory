# Meta 广告集成 — 设计文档

## 概述

为 Novel Ad Factory 新增 **Meta (Facebook) 广告投放**和**投放数据同步**两大能力，与现有素材工厂和数据看板无缝集成。

### 核心指标

- **投放管控**：全流程（Campaign → AdSet → Ad 创建）
- **数据合并**：Meta 数据与现有 pingykj 数据合并到统一看板
- **规模**：20+ 广告账户，200+ 条广告/天
- **工作流**：半自动审核流程（素材 → 审核队列 → 批量推送）

---

## 系统架构

### 新增两个模块

| 模块 | 文件 | 职责 |
|------|------|------|
| Meta 投放引擎 | 新建 `delivery.py` | 素材审核队列、投放模板管理、创意上传、广告创建、状态跟踪 |
| Meta 数据同步 | 扩展 `scraper.py` | Ads Insights API 数据拉取、多账户并行同步、增量更新 |

### 数据流

```
投放线: 素材历史 → 审核队列 → 选择模板 → 上传FB创意 → 创建Ad → 本地记录
数据线: Meta Insights API → 定时拉取 → 合并到ad_daily_stats → 统一看板
账户线: FB Access Token管理 → 多账户轮换 → 速率限制控制
```

---

## 数据库扩展

### 新表

#### `meta_accounts`
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| act_id | TEXT UNIQUE | Meta 广告账户 ID（如 act_12345） |
| act_name | TEXT | 账户名称 |
| access_token | TEXT | 该账户或关联用户的 access token |
| token_expires_at | TIMESTAMP | Token 过期时间 |
| pingykj_account | TEXT | 映射到 pingykj 的 ad_account 字段 |
| status | TEXT | active / paused / revoked |
| rate_limit_remaining | INTEGER | 当前速率限制剩余配额 |
| created_at / updated_at | TIMESTAMP | 时间戳 |

#### `delivery_templates`
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| name | TEXT | 模板名称 |
| source | TEXT | "manual" / "imported_from_fb"（来源标注） |
| source_adset_id | TEXT | 如果从 FB 导入，记录原始 AdSet ID |
| targeting_json | TEXT | 受众定位 JSON（地区、年龄、性别、语言、详细定位、排除条件） |
| placements_json | TEXT | 版位配置 JSON |
| budget_type | TEXT | daily_budget / lifetime_budget |
| budget_value | INTEGER | 预算金额（分） |
| bid_strategy | TEXT | 出价策略 |
| optimization_goal | TEXT | 优化目标 |
| billing_event | TEXT | 计费事件 |
| conversion_event | TEXT | 转化事件（pixel 事件名） |
| ad_account_id | TEXT | 关联的 Meta 账户（NULL = 通用模板） |
| created_at / updated_at | TIMESTAMP | 时间戳 |

#### `delivery_queue`
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| batch_id | TEXT | 素材批次 ID（关联素材工厂输出目录） |
| image_type | TEXT | 图片类型（scroll/lr_split/tb_split/text_single） |
| image_path | TEXT | 素材文件路径 |
| image_prompt | TEXT | 对应的提示词 |
| overlay_text | TEXT | 叠加的文字内容 |
| status | TEXT | pending / approved / rejected / uploading / delivered / failed |
| reviewer | TEXT | 审核人 |
| template_id | INTEGER | 使用的投放模板（FK → delivery_templates） |
| delivery_params_json | TEXT | 投放时使用的完整参数（模板快照） |
| fb_campaign_id | TEXT | Facebook 创建的 Campaign ID |
| fb_adset_id | TEXT | Facebook 创建的 AdSet ID |
| fb_ad_id | TEXT | Facebook 创建的 Ad ID |
| fb_creative_id | TEXT | Facebook 上传的创意 ID |
| error_message | TEXT | 失败原因 |
| created_at / updated_at | TIMESTAMP | 时间戳 |

### 现有表修改

#### `ad_daily_stats` 扩展
- 唯一约束从 `UNIQUE(date, ad_account)` 改为 `UNIQUE(date, ad_account, source)`
- 新增 `source` TEXT 列：`"pingykj"` | `"meta"`
- 新增 `meta_account_id` TEXT 列：Meta act_id（用于关联 meta_accounts 表）
- 新增指标列（Meta 专有，pingykj 行留 NULL）：
  - `ctr` REAL
  - `cpm` REAL
  - `cpc` REAL（单次链接点击成本）
  - `inline_link_clicks` INTEGER
  - `inline_link_click_ctr` REAL
  - `add_to_cart` INTEGER
  - `add_to_cart_cost` REAL
  - `purchases` INTEGER（转化数）
  - `cost_per_purchase` REAL（成效成本）
  - `purchase_value` REAL（成效价值）

---

## API 设计

### 投放管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/delivery/queue` | 审核队列列表（支持 status/paging/filter） |
| POST | `/api/delivery/queue/{id}/approve` | 审核通过 |
| POST | `/api/delivery/queue/{id}/reject` | 驳回 |
| POST | `/api/delivery/queue/batch-approve` | 批量审核通过 |
| POST | `/api/delivery/submit` | 提交投放（指定模板，后台异步执行） |
| GET | `/api/delivery/progress/{batch_id}` | 投放进度查询 |
| GET | `/api/delivery/stream/{batch_id}` | SSE 实时推送投放进度 |
| GET | `/api/delivery/records` | 投放记录列表（已创建到 FB 的广告） |

### 投放模板管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/delivery/templates` | 模板列表 |
| POST | `/api/delivery/templates` | 创建模板（手动填写） |
| GET | `/api/delivery/templates/fb-adsets/{account_id}` | 从 FB 账户拉取已有 AdSet 列表 |
| POST | `/api/delivery/templates/import` | 从指定 AdSet 导入为模板 |
| PUT | `/api/delivery/templates/{id}` | 编辑模板 |
| DELETE | `/api/delivery/templates/{id}` | 删除模板 |

### Meta 账户管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/meta/accounts` | 账户列表 |
| POST | `/api/meta/accounts` | 添加账户（act_id + token） |
| PUT | `/api/meta/accounts/{id}` | 编辑账户（更新 token/映射） |
| DELETE | `/api/meta/accounts/{id}` | 删除账户 |
| POST | `/api/meta/accounts/{id}/refresh-token` | 刷新 access token |

### 数据同步控制

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/meta/sync` | 手动触发 Meta 数据同步 |
| GET | `/api/meta/sync-status` | 查询同步状态 |
| GET | `/api/meta/sync-interval` | 读取同步间隔 |
| POST | `/api/meta/sync-interval` | 设置同步间隔（秒） |

---

## Meta API 对接要点

### 认证
- 使用 **Facebook Business App** + **长期 access token**（60天有效期）
- Token 管理：系统级 token 存储于 `config.json`，每个账户也可配置独立 token
- Token 过期检测：API 返回 190 错误码时标记过期，通知用户刷新

### 速率限制
- Meta Marketing API 标准限制：每个 ad account 每秒约 4 次调用
- 系统策略：全局令牌桶，每账户独立并发控制，超过限制自动退避重试
- 20+ 账户并行可以分摊限制

### API 版本
- 使用最新的 Graph API 版本（当前 v25.0+）
- 数据同步使用 Ads Insights API 的 `/{ad-account-id}/insights` 端点
- 投放创建使用 `/{ad-account-id}/campaigns`、`/adsets`、`/ads`、`/adimages`、`/advideos`

### 关键端点速查

| 操作 | Graph API 端点 |
|------|---------------|
| 拉取 AdSet 配置 | `GET /{act_id}/adsets?fields=name,targeting,daily_budget,bid_strategy,...` |
| 上传图片 | `POST /{act_id}/adimages` |
| 创建 Campaign | `POST /{act_id}/campaigns` |
| 创建 AdSet | `POST /{act_id}/adsets` |
| 创建 Ad (含创意) | `POST /{act_id}/ads` |
| 读取 Insights | `GET /{act_id}/insights?fields=spend,impressions,clicks,actions,...` |

---

## 投放流水线

```
① 素材生成 → 历史记录中勾选 → "加入投放队列"
② 进入审核队列 → 预览素材+文案 → 确认/驳回 → 选择投放模板
③ 投放模板预设受众/预算/排期/出价策略
④ 批量提交 → 后台异步：上传创意 → 创建AdSet → 创建Ad → 记录ID回写
⑤ 初始状态 PAUSED → 人工到FB检查/系统一键激活
```

### Campaign 层级结构

```
Campaign: [小说名]_[日期]_[账户]    例: "霸总复仇_2024-06-10_act_12345"
  └ AdSet: [图类型]_[受众细分]_[预算]  例: "scroll_欧美45+_日50"
     └ Ad: [图类型]_[章节]            例: "scroll_第3章_冲突高潮"
```

---

## 数据同步策略

### 同步字段（最终顺序）
```
日期 → 消耗 → 成效(转化数) → 展示 → 千展CPM → 点击(全部) → CTR(全部)
→ 链接点击 → 链接点击率 → CPC → 加购次数 → 加购成本 → 成效成本 → 成效价值
```

### 同步策略
- **定时全量**：APScheduler 每 5 分钟同步一次（可配置），覆盖近 90 天数据
- **增量更新**：记录每账户 last_sync_date，增量拉取减少 API 调用
- **多账户并行**：ThreadPoolExecutor 并行拉取，单个账户失败不影响其他
- **速率控制**：每账户每秒 < 4 次 API 调用

### 账户映射
手动绑定：在"账户配置"页面将 Meta 广告账户关联到 pingykj 账户 ID，辅助跨数据源分析。

---

## 前端新增区域

在前端单文件 HTML 中新增 3 个独立 Tab：

| Tab | 功能 |
|-----|------|
| **投放管理** | 审核队列 + 投放模板管理 + 批量创建广告 + 投放进度 + 投放记录 |
| **Meta 数据** | 统一看板增强（数据源标注 Meta/pingykj）+ 账户映射配置 + 同步日志 |
| **账户配置** | Meta 账户 CRUD + Token 管理 + 速率限制监控 |

---

## 实施范围（NOT in scope）

以下功能不在本次实施范围内：
- Facebook 应用审核（App Review）— 开发模式够用
- 自动调整出价/预算（Smart Automation）
- A/B 测试（Split Testing）
- 动态广告（Dynamic Ads）
- Instagram Story 专属版位设计
- 自定义转化事件创建（假设已有 pixel 和事件配置）
