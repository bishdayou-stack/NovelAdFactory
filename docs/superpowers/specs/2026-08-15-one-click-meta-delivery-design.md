# 一键发布素材到 Meta（投放向导）设计文档

日期：2026-08-15
状态：待评审

## 目标

让「生产中心」和「小说分析」生成的素材（图片 + 视频），通过一个**投放向导**，配置成
`1 系列 → N 广告组 → N 广告` 的层级结构，一键以 **PAUSED 草稿**创建到 Meta（不发布、
不花钱），之后在 Meta 管理中心 / 定时规则里手动开启。

## 结构模型（1-N-N）

- **1** 广告系列（Campaign）：销量目标，用户填名称
- **N** 广告组（AdSet）：每个绑定一个受众模版 + 预算 + 成效目标 + 归因
- **N** 广告（Ad）：每组内 N 条，每条对应一个素材（图/视频）

素材分配：用户选 `M` 个素材、设 `N` 个广告组 → 每组 `M/N` 个广告（均分）。
例：15 素材 + 3 广告组 = 1-3-5（每组 5 条）。

## 资产获取（实测验证）

Token 需具备 `ads_management`、`business_management`、`pages_show_list` 等权限。
三类资产均用「discover → import 落库」模式（沿用 `meta_accounts` 先例）：

| 资产 | 端点 | 用途 | 实测 |
|---|---|---|---|
| 主页 Page | `GET /me/accounts` | 广告归属（object_story_spec.page_id 必填） | ✅ 4 个 |
| 数据集 Pixel | `GET /act_{id}/adspixels` | 销量目标转化追踪（promoted_object.pixel_id） | ✅ GS-1（两账户共用） |
| 受众模版 | `GET /act_{id}/saved_audiences` | 广告组 targeting 直接透传 | ✅ 含完整 targeting |

注意：`/me/businesses` 返回空，**不走 BM 级发现**，走用户级 + 账户级端点。

## 三层字段映射（下划线 = 实测创建成功）

### Campaign 层（POST /act_{id}/campaigns）

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | 用户填 | 系列名称 |
| `objective` | `OUTCOME_SALES` | 销量目标（用户可选，本期固定销量） |
| `is_adset_budget_sharing_enabled` | `false`/`true` | **必填**（实测缺失报 subcode 4834011）。false=广告组预算(ABO)，true=允许组共享 20% 预算 |
| `status` | `PAUSED` | 草稿 |
| `special_ad_categories` | `[]` | 空数组 |

### AdSet 层（POST /act_{id}/adsets）

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | 默认生成 `{系列名}-adset-{i}` | 用户可改 |
| `campaign_id` | 系列 id | |
| `daily_budget` | 用户填 | ABO 预算（分）。CBO 见「待核实」 |
| `bid_strategy` | `LOWEST_COST_WITHOUT_CAP` | 默认 |
| `billing_event` | `IMPRESSIONS` | |
| `optimization_goal` | `OFFSITE_CONVERSIONS` | 销量+网站转化 |
| `destination_type` | `WEBSITE` | 转化位置=网站 |
| `promoted_object` | `{"pixel_id": "...", "custom_event_type": "PURCHASE"}` | 数据集（custom_event 可选配） |
| `attribution_spec` | `[{CLICK_THROUGH,7},{VIEW_THROUGH,1}]` | 归因：点击7天/浏览1天（用户可选） |
| `targeting` | 受众模版 JSON | 已含 `targeting_automation.advantage_audience=1`（客户生命周期策略=促进所有受众转化） |
| `status` | `PAUSED` | |

### Ad 层（POST /act_{id}/ads）

| 字段 | 值 | 说明 |
|---|---|---|
| `name` | 用户填 | 广告名称 |
| `adset_id` | 广告组 id | |
| `creative.object_story_spec.page_id` | 主页 id | 身份（主页下拉） |
| `creative.object_story_spec.link_data.link` | 用户填书城 URL | 目标位置=网站 |
| `link_data.image_hash` / `video_id` | 素材 | 图片/视频格式 |
| `link_data.message` | 文案（可选） | |
| `link_data.call_to_action` | `{type:"LEARN_MORE", value:{link}}` | **必填**（缺则 ill formed） |
| `status` | `PAUSED` | |

合创广告 / 多广告主广告 / 动态素材：默认关，**不传对应字段**。

## 数据模型变更（database.py）

- 新表 `meta_pages`（page_id, page_name, bm_id, access_token 来源, 时间戳）
- 新表 `meta_pixels`（pixel_id, pixel_name, act_id 绑定, 时间戳）
- 新表 `meta_saved_audiences`（audience_id, name, act_id 绑定, targeting_json, 时间戳）
- 新表 `delivery_campaigns`（name, objective, budget_strategy, is_adset_budget_sharing_enabled, status, 时间戳）
- 新表 `delivery_adsets`（campaign_id, name, ad_account_id, page_id, pixel_id, audience_id, daily_budget, bid_strategy, optimization_goal, billing_event, destination_type, custom_event_type, attribution_spec_json, targeting_json, status）
- 复用 `delivery_queue` 存广告层素材（`image_path` 存本地路径，扩展名区分图/视频），加 `delivery_adset_id` 关联广告组

## 后端改动（main.py + delivery.py + meta_api.py）

1. `meta_api` 新增：`get_pixels(act_id, token)`、`get_saved_audiences(act_id, token)`；
   `create_campaign` 加 `is_adset_budget_sharing_enabled`；`create_adset` 加
   `destination_type`/`promoted_object`/`attribution_spec`；`create_ad` 加 `call_to_action`
2. 新路由：`/api/meta/pages`、`/api/meta/pixels`、`/api/meta/saved-audiences`（discover + import）
3. 新路由：`/api/delivery/campaigns`、`/api/delivery/adsets`（投放向导的系列/组 CRUD）
4. 重构 `delivery.py`：`submit_delivery_batch` 从「1 素材=1 系列+1 组+1 广告」改为**分层执行**
   （建 1 系列 → 循环建 N 组 → 组内循环上传素材建 n 广告），SSE 按三层上报进度
5. **URL→路径解析**：前端素材是 `/static/output/{batch_id}/{name}`，服务端转 `OUTPUT_ROOT/{batch_id}/{name}`
   并校验存在（杜绝路径穿越）
6. 新端点 `POST /api/delivery/publish`：入参 = 完整向导配置（系列 + 广告组数组 + 每组素材 URL 数组），
   全部 PAUSED 创建，返回 batch_id + 各层 fb_*_id

## 前端改动（static/index.html）

1. 新 tab「投放向导」：三步向导
   - ① 系列：名称、目标（销量）、预算策略（系列/组）
   - ② 广告组：可加 N 个；每个配 受众模版下拉、主页下拉、数据集下拉、预算、成效目标、归因
   - ③ 广告：从生产中心/小说分析选素材，分配到各广告组（均分，可调整）；填书城 URL、文案、CTA
2. 生产中心 + 小说分析 各加「发布到 Meta」按钮 → 跳投放向导并预填选中素材
3. 发布后 SSE 进度 + 完成后列出各层 fb_*_id

## 实测记录（2026-08-15，token 用户 guanliyuan，账户 act_1557179239285773）

- 建 Campaign：`objective=OUTCOME_SALES` + `is_adset_budget_sharing_enabled=false` → 成功，id 120249667236320344
- 建 AdSet：全字段（destination_type/promoted_object/attribution_spec/targeting）→ 成功，id 120249667264200344
- Ad 层：本机 MSYS 环境阻断（PIL 存图段错误 + 中文路径），字段从官方文档确认

## 待核实项（实现阶段用真实账户再验）

1. **CBO（系列预算）**字段：预算放 campaign 的确切字段名（`daily_budget` on campaign + 关掉 adset 预算？）
2. **单次成效目标费用**：`bid_strategy` = `COST_CAP`（成本上限）还是 `TARGET_COST`（目标费用）+ `bid_amount`
3. `attribution_spec` 的「互动观看」event_type 枚举名
4. 合创/多广告主/动态素材的确切字段（本期默认关，不传，暂不阻塞）
