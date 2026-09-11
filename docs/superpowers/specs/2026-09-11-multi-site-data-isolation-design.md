# 多书城（站点 A / B / 合计）数据隔离 — 设计

日期：2026-09-11

## 背景

现有系统从 pingykj 书城后台同步广告消耗、订单、小说数据。现新增**第二个同款书城后台**，
两个书城域名不同、数据不同、**账号密码相同**，两个都会同时使用。

实测确认两者同款（jeecgboot）：

| | A 书城 | B 书城 |
|---|---|---|
| 后台 base | `https://hw.manage.pingykj.com` | `https://manage.relishnovel.com` |
| 章节内容 | `https://hw.manage.api.pingykj.com` | `https://manage.api.relishnovel.com` |
| 登录 | `/jeecgboot/sys/login` | 同路径 |

需求：在一个界面里切换 **A 数据 / B 数据 / 两边合计**，默认合计；合计覆盖**数据看板 + 小说管理**；
两个书城的数据**分开存储**；同一本小说（相同 `novel_id`）在两边的消耗**相加**。

> 注：浏览器无法直连书城（需登录取 token + 验证码），因此数据必须由后端同步，**不做前端聚合**。

## 方案：单库 + `site` 维度

两个书城的数据进**同一个库**，pingykj 来源的表增加 `site` 列：

- **看 A / 看 B** = 查询加 `site = ?` 过滤
- **合计** = 不加 `site` 过滤

关键收益：**合计不需要改写任何聚合 SQL**。表内本就有 `GROUP BY novel_id` + `SUM(book_ad_spend)`，
两个书城的数据同表存放、不加站点过滤时，**天然得到「同本小说去重 + 消耗相加」**——
无需手写合并函数，口径也不会写错。

同理，账户维度合计 = 两书城**同名账户**的消耗自动相加（`GROUP BY ad_account`）；
订单合计 = 两书城订单取并集（订单号各自独立，**不跨站去重**）。

## 配置

站点列表放进 `config.json` 的 `meta.pingykj_sites` —— 复用 `update.sh` 既有的「保留 meta 块」机制，
服务器上改域名/加站点不会被 `git pull` 覆盖，也无需改更新脚本：

```json
"meta": {
  "pingykj_sites": [
    {"key": "a", "name": "A站(pingykj)",   "base_url": "https://hw.manage.pingykj.com",    "content_url": "https://hw.manage.api.pingykj.com"},
    {"key": "b", "name": "B站(relishnovel)","base_url": "https://manage.relishnovel.com",  "content_url": "https://manage.api.relishnovel.com"}
  ]
}
```

- 未配置时回落到内置默认（即上表两条），保证兼容。
- `site` 存 `key`（`'a'`/`'b'`），换域名不影响库内数据。

## 数据层改动

### 加 `site` 列（`TEXT NOT NULL DEFAULT 'a'`）的表

`ad_daily_stats`、`orders`、`raw_ad_stats`、`raw_orders`、`sync_state`、`sync_logs`、
`account_aliases`、`novel_books`、`novel_chapters`、`novel_spend_snapshots`

老数据自动落到 `'a'`（`ALTER TABLE ADD COLUMN ... DEFAULT 'a'`）。

**重建的表在重建时直接带上 `site` 列，不要再单独 `ALTER`**（避免重复加列报错）。

### 唯一键/主键重建（需重建表）

| 表 | 现唯一键 | 改为 |
|---|---|---|
| `ad_daily_stats` | `(date, ad_account, source, user_id)` | + `site` |
| `orders` | `order_id` | `(site, order_id)` |
| `sync_state` | `(sync_type, user_id)` | + `site` |
| `account_aliases` | `(account_id, user_id)` | + `site` |
| `novel_books` | `UNIQUE(novel_id)` | `UNIQUE(site, novel_id)` |
| `novel_chapters` | `UNIQUE(novel_id, chapter_no)` | + `site` |
| `novel_spend_snapshots` | `UNIQUE(novel_id, snap_date)` | + `site` |

SQLite 不能直接改唯一键 → 走「建新表 → `INSERT SELECT` → 删旧表 → 改名」，包在一个事务里。

**迁移与回滚**：迁移前**先复制 `data/dashboard.db` 备份**；迁移在单事务内完成，失败自动回滚；
`ad_daily_stats` 已存在两次同类重建迁移（`_migrate_user_isolation`、`_ensure_user_id_columns`），
本次按同样模式追加，不新造机制。

### 读写函数

- **写**（~14 个，`database.py`）：`upsert_ad_stats`、`upsert_orders`、`save_raw_ad_stats`、
  `save_raw_orders`、`set_last_sync_date`、`delete_sync_state`、`set_account_alias`、
  `delete_account_alias`、`delete_account_all`、`log_sync`、`upsert_novel_books`、
  `save_novel_spend_snapshots`、`upsert_novel_chapters` —— 均增加 `site` 参数并写入该列。
- **读**（~13 个）：同上表的查询函数增加 `site` 参数；`site` 为空 = 不过滤（合计）。
- `novel_books`/`novel_chapters`/`novel_spend_snapshots` 三张表原本没有 `user_id`，本次只加 `site`，
  维持"全局单例"语义（合计数按 `site` 分组）。

### analytics.py

12 个函数（`get_summary`、`get_daily_stats`、`get_trend`、`get_accounts`、`get_account_ranking`、
`get_orders`、`get_novel_stats`、`get_user_ranking` 等）增加 `site=None` 参数，构造 `AND site = ?`。
`site=None` 即合计。

**无需改**：现有的 `source IS NULL OR source != 'meta'` 否定式过滤保持原样——
两个书城的数据 `source` 仍为 `'pingykj'`，Meta 仍为 `'meta'`，站点是独立维度，不冲突。

## 同步层改动（scraper.py）

- `BASE_URL` / `_CONTENT_API` 常量改为按站点取：`site_base_url(site)` / `site_content_url(site)`。
- `ScraperSession` 会话键由 `user_id` 改为 `(user_id, site)`；登录、验证码、keepalive 均按站点。
- 同步函数增加 `site` 参数：`sync_ads`、`sync_orders`、`sync_novel_books`、`sync_missing_chapters`、
  `sync_novel_chapters`、`sync_all_novel_content`、`run_full_sync`。
- 同步游标（`sync_state`）按 `(sync_type, user_id, site)` 独立。
- 凭据复用 `users.pingykj_username` / `pingykj_password_encrypted`（两书城账号相同，**不新增列**）。

## 调度

- 定时任务 `_auto_sync_all_users`、`_auto_full_novel_sync`、`_auto_chapter_sync` 改为**遍历站点**执行。
- `/api/scraper/sync`、`/api/scraper/reset-sync`、`/api/scraper/captcha`、`/api/scraper/login`
  增加 `site` 参数（验证码/登录按站点分别进行）。

## 路由与前端

- 看板/小说相关路由增加 `site: str = Query("")`（空 = 合计），透传给 analytics/database。
- 前端顶栏新增「站点」下拉：**合计（默认） / A站 / B站**，切换后刷新当前 tab。
- 看板与小说管理的请求带上 `site`；**合计模式下写操作（同步、删除账户、改别名等）禁用**，
  提示「请先切换到具体站点」。

## 范围外（本次不做）

- Meta / 投放 / 素材工厂 / 历史记录不参与站点维度，不受影响。
- 不做跨服务器的数据复制（数据本来就进同一个库）。

## 已知问题与风险

- **迁移是本次最大风险**：7 张表要重建唯一键，必须备份 + 事务 + 逐表验证行数一致。
- 同步量翻倍：两个书城各同步一次，注意书城接口频率与耗时。
- `meta_accounts.pingykj_account` 是 Meta 账户到书城账号名的映射，若两书城存在同名账户，
  该映射不区分站点（暂不处理，记录在案）。
- 既有缺陷（与本次无关）：`/api/dashboard/book-stats`（`main.py:4518`）调用的
  `database.get_book_stats` 在仓库中不存在，该接口当前必抛错。
