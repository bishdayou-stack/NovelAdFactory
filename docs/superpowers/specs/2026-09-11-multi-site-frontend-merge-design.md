# 多站点（A / B / 合计）前端切换与合并 — 设计

日期：2026-09-11

## 背景

同一份代码部署在两台服务器上，各自连自己的 pingykj 书城，各自独立的 SQLite 库，数据不互通。
需求：在**一个界面**里切换查看 **A 站点数据 / B 站点数据 / 两边合计**，默认显示合计。

合计范围仅限 **数据看板 + 小说管理**。两个书城是独立渠道，同一本小说（相同 `novel_id`）在两边的消耗**相加**。

## 方案：前端数据源路由层

前端已有全局 fetch 拦截器（`static/index.html:1630`，统一注入 `Authorization`），所有请求都经过它。
把它扩展为「数据源路由层」，则 **18 处业务 fetch 无需改动**，合并逻辑集中在一处。

- **模式 A**：`/api/*` 保持同源（A 服务器）+ A 的 token
- **模式 B**：`/api/*` 重写到 B 服务器地址 + B 的 token
- **模式 合计（默认）**：白名单内的**只读 GET** 并发请求 A、B，按规则合并成一份响应返回

跨域：后端已是 `allow_origins=["*"]`（`main.py:466`），浏览器可直连 B。

## 组件

1. **站点设置**（localStorage）：B 服务器地址 + B 的登录账号密码（或直接填 token）。
   未配置时：下拉中 B 不可选，合计 = 仅 A，并提示未配置。
2. **站点下拉**（顶栏）：`合计（默认） / A / B`。切换后刷新当前 tab 数据。
3. **路由层**（扩展 fetch 拦截器）：
   - 判定模式；写操作（POST/PUT/DELETE）不发合计。
   - 合计模式下，命中白名单的 GET → 并发两边 → 调对应 merge 函数 → 返回合成 JSON。
4. **合并规则表**（见下节）。
5. **分页处理**：合计模式下，对分页接口将 `page_size` 放大（取全量），两边取回后合并去重，
   **在前端本地分页**（复用现有 `renderPager`）。只合并"当前页"会导致排序与 `total` 错误，不可接受。
6. **只读约束**：合计模式下写操作禁用并提示「请先切换到 A 或 B」。

## 合并规则（字段级）

| 接口 | 合并 key | 相加字段 | 需重算字段 | 备注 |
|---|---|---|---|---|
| `/api/dashboard/summary` | 无（聚合值） | total_spend, total_revenue, subscribe_amount, order_count, subscribe_count, coin_count, total_ads | roi, subscribe_roi, cpa | `active_days`（日期去重计数）、`account_count`（账户去重计数）**不可相加** |
| `/api/dashboard/daily-stats` | `(date, ad_account, user_name)` | total_spend, total_revenue, ad_count, impressions, clicks | roi | 分页；**不用 user_id**（见下）|
| `/api/dashboard/trend` | `date` | spend, revenue | roi, spend_ma7, revenue_ma7 | ma7 用合并后的序列重算 |
| `/api/dashboard/accounts` | `account_id` | —（无数值） | — | 取并集 |
| `/api/dashboard/account-ranking` | `(ad_account, user_name)` | spend, revenue, total_ads | roi | 合并后重排再分页 |
| `/api/dashboard/user-ranking` | `user_name` | total_spend, total_revenue, subscribe_amount, order_count, subscribe_count, coin_count | roi, subscribe_roi, cpa | 无分页 |
| `/api/dashboard/orders` | `order_id` | amount | — | 并集去重；分页 |
| `/api/dashboard/novel-stats` | `novel_id`（空则 `novel_name`） | order_count, total_amount, book_ad_spend, recent_spend | conversion_cost | 消耗**相加**；`recent_spend` 可为 null（null 视为 0） |
| `/api/novels/list` | `novel_id` | order_count, book_ad_spend | conversion_cost | 其余元数据（字数/章节数/封面等）取 A 优先；分页 |
| `/api/novels/{id}` | `novel_id` | — | — | 取 A 优先（前端目前未调用） |
| `/api/novels/{id}/chapters` | `(novel_id, chapter_no)` | — | — | 取并集；分页 |
| `/api/novels/chapters/{id}` | — | — | — | 章节内容取 A（跨服务器 `id` 可能不同，按需先解析到 A 的 id） |

**命名陷阱**：`trend` 用 `revenue`/`spend`，`summary`/`ranking` 用 `total_revenue`/`total_spend`，不要混用。

**跨服务器合并键不能用 `user_id`**：两台服务器用户表互相独立，同一个人的 `user_id` 不对应，
用它当 key 会导致同一条数据两边各算一份。凡涉及用户的维度统一改用 **`user_name`**（登录名/显示名），
输出的 `user_id` 取 A 的值。

## 边界与降级

- B 未配置 → 合计 = 仅 A，并在下拉处标注「B 未配置」。
- B 请求超时/失败 → 合计降级为显示 A 的数据，并在页面顶部提示「B 站点不可用，当前仅显示 A」。
- B 的 token 失效（401）→ 提示重新配置 B 凭据，不静默丢弃。
- 比率字段一律由合并后的分子分母重算，禁止对两边的比率求平均或相加。

## 不做（本次范围外）

- 后端不改（除必要小修）。
- Meta / 投放 / 素材工厂 / 历史记录等**不参与合计**，这些页面在合计模式下按 A 显示。
- 不做跨服务器的实时同步或数据复制。

## 待确认/已知问题

- 用户维度按 `user_name` 合并的前提是**两台的用户登录名一致**（同一批人）。若两边用户名不同，
  合计里的用户排行会把同一个人拆成两行 —— 需要确认两台的用户名是否对齐。
- 合计算力：合计模式下每次只读请求会打两台服务器，注意 B 的负载。
- 既有缺陷（与本次无关）：`/api/dashboard/book-stats`（`main.py:4518`）调用的 `database.get_book_stats` 在仓库中不存在，该接口当前必抛错。
