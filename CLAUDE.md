# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Novel Ad Factory（小说广告素材工厂）** — 将小说内容批量转化为 Facebook 信息流广告素材（图片+视频）的 Web 应用，面向欧美女性 40+ 受众。同时内置**广告数据看板**、**Meta 直连投放引擎**、**小说管理**和**Meta 管理中心**，支持双数据源：requests 爬虫同步 pingykj 投放平台数据 + Meta Graph API 直连拉取 Facebook 广告洞察。

整个应用是**单文件 FastAPI 后端 + 单文件 HTML 前端 + SQLite 数据库**，无 ORM。子系统：
- **素材工厂**：小说 → LLM → 图片 → 视频
- **数据看板**：双数据源（pingykj 爬虫 + Meta Graph API）→ SQLite → 广告 ROI 分析
- **Meta 直连投放**：Meta Graph API → 审核队列 → 批量创建 Facebook 广告
- **Meta 管理中心**：BM/App 多租户管理、账户树、广告系列/广告组/广告三层查询、定时状态规则、创意库
- **小说管理**：小说库同步 → 章节存储 → 关键词搜索

## 启动和开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器（带热重载）
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Windows 一键启动（检查依赖 → 释放 8000 端口 → 启动并打开浏览器）
一键启动.bat
```

前端入口：`http://127.0.0.1:8000/static/index.html`

依赖见 [requirements.txt](requirements.txt)：fastapi、uvicorn、requests、pillow、moviepy、numpy、python-multipart、sse-starlette、imageio-ffmpeg、apscheduler、beautifulsoup4、lxml。视频合成依赖系统 `ffmpeg`（`imageio-ffmpeg` 提供二进制）。

**没有单元测试框架**。唯一的测试工具是 [test_meta_creds.py](test_meta_creds.py)，用于验证 Meta access token / app_id / app_secret 有效性（`python test_meta_creds.py` 或 [test_meta_creds.bat](test_meta_creds.bat)）。功能验证通常靠启动服务后走前端或 `curl` 打 API。

## 核心生成流水线

```
小说文本 → Chat API (LLM) → image_prompt (JSON)
         → Image API (文生图) → PNG
         → PIL 叠加文字 (composite_text_on_image)
         → ffmpeg 合成滚屏视频 + 背景音乐
```

另有一条**独立 AI 视频路径**（`video_gen.py`，与图片生成解耦）：小说原文 → LLM 镜头分解脚本（`prompts/video_script.txt`）→ 视频模型逐镜头生成（OpenAI 兼容 `/v1/videos`，sora/veo/grok 等模型族）→ ffmpeg 拼接。前端走 `/api/video/analyze|generate`，进度经 `/api/video/progress|cancel`，产物入历史记录。

## 关键文件

| 文件 | 作用 |
|------|------|
| [main.py](main.py) (~6800行) | FastAPI 后端全部路由与逻辑：素材生成、数据看板（pingykj + Meta）、小说管理、scraper 控制、投放引擎、Meta 管理中心（BM/App/账户/广告/定时规则）、多用户认证、SSE 推送、启动恢复。全部 `@app.*` 路由都在此文件 |
| [static/index.html](static/index.html) (~8200行) | 单文件前端（原生 JS + Tailwind）：生产中心、历史记录、素材浏览、视频样式、小说分析、数据看板、Meta 管理中心（账户树 + BM 分组）、投放管理、用户管理等模块 |
| [database.py](database.py) | SQLite 数据层：建表/迁移、session CRUD、广告数据 UPSERT、订单写入、小说书籍/章节、同步日志、别名、Meta 账户、投放模板/队列、用户/权限、app_config/bm_config、实体状态、定时规则 |
| [scraper.py](scraper.py) | 双源爬虫：pingykj API 登录/验证码/token 认证 + 广告日报/订单/小说数据分页同步；Meta Insights 同步（经 meta_api 模块） |
| [meta_api.py](meta_api.py) | Meta (Facebook) Graph API v25.0 客户端：用 `subprocess` 调 curl 发 HTTP（规避 Windows SSL 与代理兼容问题）、token 管理、速率限制（4次/秒/账户）、广告图片上传、Campaign/AdSet/Ad 创建与查询、Insights |
| [analytics.py](analytics.py) | 数据看板分析引擎：pingykj + Meta 双源 KPI 汇总、日统计、趋势、账户/用户排行、订单、小说维度统计、异常检测、Meta 三层（campaign/adset/ad）统计查询 |
| [delivery.py](delivery.py) | 投放引擎辅助：素材审核队列 → 批量创建 Meta 广告，SSE 推送投放进度，ThreadPoolExecutor 并行投放（路由在 main.py） |
| [video_gen.py](video_gen.py) | 独立 AI 视频生成模块（区别于图片→滚屏视频）：小说 → LLM 镜头脚本（`prompts/video_script.txt`）→ OpenAI 兼容 `/v1/videos` 接口逐镜头生成（sora/veo/grok 模型族，字段按家族适配）→ ffmpeg 可选拼接。路由 `/api/video/*`（在 main.py），接入历史记录与 SSE 进度 |
| [config.json](config.json) | 全局配置：API Key/URL、Chat/Image 模型名、分析 prompt、默认并发数 `concurrency`；含 `meta` 块（app_id、app_secret、default_access_token、proxy、sync_interval_seconds、rate_limit_per_second、api_version）。经 `/api/config` 和 `/api/meta/config` 读写。**含密钥，勿提交真实值** |
| [prompts/](prompts/) | 提示词规则：`system_prompt.txt`（6大钩子引擎）、`rules_core.txt`（绘图红线/情感词库）、`rules_*.txt`（按图片类型拆分）、`composition_archetypes.txt`（视觉基因模板）、`suffix_prompts.txt`、`compress_prompt.txt`、`rules_video_script.txt`（滚屏视频脚本）、`video_script.txt`（独立 AI 视频镜头分解，见 video_gen.py） |
| [templates_index.json](templates_index.json) | 爆款模板索引（由 scripts/build_template_index_v2.py 生成） |
| [video_styles.json](video_styles.json) | 视频文字样式配置模板（前端添加样式后填充） |
| [scripts/](scripts/) | 离线工具脚本：build_template_index*（构建模板索引）、generate_template_descriptions.py，以及若干一次性迁移/修复脚本（fix_meta_*.py、add_meta_js.py 等） |
| [ziti/](ziti/) | 字体文件目录（100+ 字体，.ttf/.ttc/.fon） |
| [docs/superpowers/](docs/superpowers/) | Meta 功能的**设计文档与实施计划**（specs + plans），按日期命名 |
| `音乐/` | 背景音乐 MP4 目录（需手动创建并放入 MP4，ffmpeg 合成时随机选取） |
| [data/dashboard.db](data/dashboard.db) | SQLite 数据库（自动创建），存储广告数据、订单、用户、同步状态 |

## 图片类型

- **text_single**（1:1 方图）：单帧图，底部叠加文字，必须 ≥2 人物冲突对峙
- **lr_split**（1:1 左右分屏）：垂直分割，左右不同人物，需加左右标签 + 底部叙事文字
- **tb_split**（1:1 上下分屏）：水平分割，上下不同人物/对比
- **scroll**（9:16 竖图）：滚屏视频底图，文字由代码以滚动方式合成
- **three_panel**（1:1 异形三宫格）：2+1 不规则分屏，4 种布局（左2右1/左1右2/上2下1/上1下2，可 random，布局词由代码注入 `resolve_three_panel_layout`）；三屏按剧情递进（困境→反击→结果），文字由 AI 输出、可代码叠加。另有 `cinematic_collage`（电影拼贴风）为 style 开关，作用于 text_single/scroll

## 并发模型

- 全局 `ThreadPoolExecutor(max_workers=4)` 用于后台生成任务
- 每批次内方图生成使用独立 `ThreadPoolExecutor`，并发数由前端 `concurrency` 参数控制（默认 2，最大 16）
- SSE 通过 `queue.Queue` + `asyncio` 实现事件推送
- 批次取消通过 `threading.Event` 实现

## 输出目录

- Windows：`D:\每日小说`；其他系统：`./output`；可用环境变量 `NOVEL_OUTPUT_ROOT` 覆盖
- 每个批次一个子目录（以 batch_id 命名），含 `_progress.json` 和 `_meta.json`

## Chat API 调用

调用兼容 OpenAI Chat Completions 格式的端点 `/chat/completions`。`main.py` 中 `request_image_prompt_plan` 构造 system/user prompt 并解析 JSON 响应；支持分批模式 `request_image_prompt_plan_batched`（每批 batch_size 张）。

## 合规要求

`_COMPLIANCE_MAP` 定义自动替换词表，将可能触发图像模型安全过滤的词汇替换为安全版本。规则见 `prompts/system_prompt.txt` 的 "Image Model Compliance" 部分和 `prompts/rules_core.txt`。

## 多用户认证与权限

**新增于 2026-07 前后**，取代早期单用户的 `login_session` 表（迁移中已删除）。

- **认证方式**：`HTTPBearer`（`main.py` 中 `security = HTTPBearer(auto_error=False)`）+ `Depends(get_current_user)`。前端登录后拿到 `session_token`，后续请求放 `Authorization: Bearer <token>` 头
- **users 表**：username、password_hash、salt、role（`admin`/`user`）、display_name、is_active、`pingykj_username`/`pingykj_password`（加密存储）、session_token/session_expires_at、last_login_at/ip
- **默认管理员**：`admin` / `admin123`（`init_db()` 自动创建，需尽快改密码）
- **权限**：`role` 区分 admin（可管理用户、看全局数据）与普通 user（仅见自己分组/账户）
- **每用户 pingykj 凭据**：普通用户可绑定自己的 pingykj 账号，爬虫按当前登录用户拉取其数据（`GET /api/users/{id}/pingykj-captcha` + `POST /api/users/{id}/reconnect-pingykj`）
- **user_config 表**：key/value 形式的每用户配置，读取时优先于全局值

## 数据看板子系统

SQLite（`data/dashboard.db`）由 `database.py` 管理，`contextmanager` 获取连接，自动提交/回滚，PRAGMA WAL + 外键 + 5s busy_timeout。主要表：

- `users` / `user_config` — 用户、角色、会话 token、每用户配置
- `ad_daily_stats` — 广告日报（消耗、收入、展示、点击，按 date + ad_account 唯一，含 `meta_account_id` 区分来源）
- `orders` — 订单（novel_id / chapter_no 关联小说）
- `novel_books` / `novel_chapters` / `novel_spend_snapshots` — 小说元数据 / 章节内容 / 消耗快照
- `sync_logs` / `sync_state` / `raw_ad_stats` / `raw_orders` — 同步日志、状态、原始响应存档
- `account_aliases` — 广告账户别名
- `meta_accounts` — Meta 广告账户（act_id、act_name、access_token、pingykj_account、**bm_id**、status）
- `app_config` / `bm_config` — Meta 应用（App）与 Business Manager 配置（见「Meta 管理中心」）
- `meta_ad_stats` / `meta_adset_stats` / `meta_ad_creatives` — Meta 广告/广告组统计与创意缓存
- `meta_entity_status` — 各层实体（campaign/adset/ad）的 `effective_status`/`status`
- `meta_scheduled_rules` — 定时状态规则（预约开启/暂停实体）
- `meta_account_snapshots` / `meta_campaign_snapshots` — 账户/系列快照（用于趋势对比）
- `delivery_templates` / `delivery_queue` / `hit_materials` — 投放模板、审核队列、爆款标记

## 爬虫子系统

`scraper.py` 负责双源数据同步，纯 `requests` 实现，无浏览器依赖。

**pingykj 平台同步**：
1. 登录 → `POST /jeecgboot/sys/login` 获取 token（按用户绑定凭据）
2. 验证码 → `GET /jeecgboot/sys/randomImage/{checkKey}` 返回 base64，用户手动输入
3. 数据同步 → `X-Access-Token` 头分页拉取广告日报/订单/小说，写 SQLite
4. 定时同步 → APScheduler 后台线程，默认每 3 分钟 `run_full_sync()`，间隔经 `/api/scraper/sync-interval` 读写

**Meta Insights 同步**：`sync_all_meta_insights()` 遍历 active 账户，经 `meta_api.py` 拉取 `/act_{id}/insights` 写 `ad_daily_stats`。同步可异步带进度（`/api/meta/sync-progress`），并顺带写实体状态（`meta_entity_status`）。

Token 过期后需重新登录。

## Meta API 子系统

`meta_api.py` 封装 Facebook Graph API v25.0，核心设计：

- **HTTP 后端**：`subprocess` 调 `curl`（不用 Python requests/urllib），规避 Windows SSL 与代理兼容问题
- **代理支持**：`config.json` 的 `meta.proxy` → `HTTPS_PROXY` → `https_proxy` → `HTTP_PROXY`
- **速率限制**：`_RATE_LIMITS` 按 act_id 跟踪每秒剩余调用（默认 4次/秒），超限自动 sleep
- **Token 管理**：三级优先级 **`bm_config.system_token` → `meta_accounts.access_token` → `config.json` 的 `default_access_token`**
- **App 绑定优先级**：`bm_config.app_id` → `app_config` 中 is_default=1 → `config.json` 的 `meta.app_id`
- **核心 API**：`get_ad_accounts`（含 owned + client）、`get_insights`、`upload_ad_image`、`create_campaign/create_adset/create_ad`、`get_adsets`，以及 campaign/adset/ad 查询与状态读取

## Meta 管理中心（新增于 2026-07）

前端单页 `tab-meta`：左侧账户树（按 BM 分组）+ 右侧数据面板。对应后端能力：

- **BM 管理**：`bm_config` 表把 BM 作为独立实体（bm_id、bm_name、system_token、app_id），`meta_accounts.bm_id` 关联；BM 名称发现时自动获取，支持编辑 token、删除（不删关联账户）
- **多 App 支持**：`app_config` 表配置多套 app_id/app_secret，可设默认 App，BM 可绑定不同 App
- **账户发现/导入**：`POST /api/meta/discover` 发现有权访问的账户/BM/主页，`POST /api/meta/accounts/import` 导入；`/api/meta/assign` 分配账户到 BM
- **三层广告查询**：`/api/meta/campaigns`、`/api/meta/adsets`、`/api/meta/ads` 返回消耗/转化/ROI + `effective_status`/`status`（LEFT JOIN `meta_entity_status`）
- **分阶段统计**：`/api/meta/stage-stats` 与 `/api/meta/campaign-stage-stats`（账户/系列维度漏斗）
- **创意库**：`/api/meta/gallery` 浏览 `meta_ad_creatives` 缓存的广告创意
- **定时状态规则**：`/api/meta/scheduled-rules` CRUD + `meta_scheduled_rules` 表，后台任务在预约时间调 `POST /api/meta/entity/{id}/status` 开启/暂停实体
- **导出**：`/api/meta/export/daily-stats`、`/api/meta/export/account-ranking`

## 投放引擎子系统

`delivery.py`（路由在 main.py）管理素材到 Facebook 广告的投放流程：

1. 素材进 `delivery_queue`（`pending` → 人工 `approved`/`rejected`）
2. `submit_delivery_batch()` 对已审批素材按模板并行创建广告（上传创意 → Campaign → AdSet → Ad），全部以 PAUSED 状态创建
3. `_delivery_events` + `_delivery_queues` 推送 SSE 进度；ThreadPoolExecutor 并行投放

## API 路由结构

路由全部集中在 `main.py`（`grep -nE '@app\.(get|post|put|delete)' main.py` 可看全量）。按模块分组：

- **认证/用户**：`/api/auth/login|logout|me`、`/api/auth/pingykj-credentials`、`/api/users` CRUD、`/api/users/{id}/reconnect-pingykj`、`/api/users/{id}/pingykj-captcha`
- **小说管理**：`/api/novels/list`、`/api/novels/{id}`、`/api/novels/{id}/chapters`、`/api/novels/chapters/{id}`、`/api/novels/sync-books`、`/api/novels/sync-books-full`(+`/progress`)、`/api/novels/sync-content`
- **素材生成**：`/api/generate`、`/api/cancel`、`/api/progress[/{batch_id}]`、`/api/generate/stream/{batch_id}`、`/api/history[/{batch_id}]`、`/api/history/batch-delete`、`/api/history/{id}/download`、`/api/fetch-novel`、`/api/analyze-novel`、`/api/generate-from-analysis|prompts`、`/api/config`、`/api/templates`、`/api/fonts`、`/api/prompt-rules`、`/api/stats`；**AI 视频**：`/api/video/analyze`、`/api/video/generate`、`/api/video/progress|cancel/{batch_id}`、`/api/video-styles`
- **数据看板**：`/api/dashboard/summary`、`/daily-stats`、`/accounts`(+DELETE `/{account_id}`)、`/trend`、`/orders`、`/account-ranking`、`/user-ranking`、`/anomalies`、`/novel-stats`、`/book-stats`、`/account-aliases`
- **爬虫控制**：`/api/scraper/login|captcha|sync|sync-status|reset-sync|session-status|logout|sync-interval`
- **Meta 账户/BM/App**：`/api/meta/accounts` CRUD、`/api/meta/accounts/{id}/refresh-token`、`/api/meta/accounts/add-manual`、`/api/meta/assign`、`/api/meta/discover`、`/api/meta/accounts/import`、`/api/bm` CRUD(+`/discover`、`/{id}/owner`)、`/api/app` CRUD
- **Meta 数据/管理**：`/api/meta/summary`、`/daily-stats`、`/trend`、`/account-ranking`、`/campaigns|adsets|ads`、`/gallery`、`/stage-stats`、`/campaign-stage-stats`、`/bm-summary`、`/user-summary`、`/entity/{id}/status`、`/scheduled-rules`、`/export/*`
- **Meta 配置与同步**：`/api/meta/config`、`/sync`、`/sync-account/{id}`、`/sync-progress`、`/sync-status`、`/last-sync`、`/sync-interval`
- **投放引擎**：`/api/delivery/templates`(+`/fb-adsets/{account_id}`、`/import`)、`/queue`(+`/{id}/approve|reject`、`/batch-approve`)、`/submit`、`/progress/{batch_id}`、`/stream/{batch_id}`、`/records`
- **爆款素材**：`/api/hit-materials`(+`/lookup`、`/{mid}`)

## 部署与运维

- **Windows**：`一键启动.bat`（开发机启动）、`打包.bat`（生成 `NovelAdFactory_deploy_*.zip` 部署包，含源码/前端/prompts/字体/data/启动脚本）、`更新.bat`（`git pull` + 恢复本地 config.json 的 meta 块）
- **Linux**：`start.sh`（nohup 启动 + 依赖安装标记）、`update.sh`（pkill + pull + 恢复配置）
- **仓库**：`origin` → `github.com/bishdayou-stack/NovelAdFactory.git`，主分支 `master`
- **config.json 处理**：更新脚本会备份并恢复 `meta` 块（保留服务器上的 proxy 等设置），避免 pull 覆盖

## 项目文档与开发方法论

本仓库使用 **superpowers** 方法论（spec → plan → subagent-driven-development）。`docs/superpowers/specs/` 存设计文档、`docs/superpowers/plans/` 存实施计划（按日期命名，如 `2026-07-12-bm-app-management-*`）。新增功能前先看这些文档了解既有设计意图。`.claude/settings.json` 启用了插件（claude-md-management、ponytail、frontend-design）和一组常用命令的权限白名单。
