# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Novel Ad Factory（小说广告素材工厂）** — 将小说内容批量转化为 Facebook 信息流广告素材（图片+视频）的 Web 应用，面向欧美女性 40+ 受众。同时内置**广告数据看板**、**Meta 直连投放引擎**和**小说管理**，支持双数据源：requests 爬虫同步 pingykj 投放平台数据 + Meta Graph API 直连拉取 Facebook 广告洞察。

## 启动和开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器（带热重载）
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Windows 一键启动
一键启动.bat
```

前端入口：`http://127.0.0.1:8000/static/index.html`

## 架构

整个应用是**单文件 FastAPI 后端 + 单文件 HTML 前端 + SQLite 数据库**，无 ORM。四大子系统：
- **素材工厂**：小说 → LLM → 图片 → 视频
- **数据看板**：双数据源（pingykj 爬虫 + Meta Graph API）→ SQLite → 广告 ROI 分析
- **Meta 直连投放**：Meta Graph API → 审核队列 → 批量创建 Facebook 广告
- **小说管理**：小说库同步 → 章节存储 → 关键词搜索

### 核心生成流水线

```
小说文本 → Chat API (LLM) → image_prompt (JSON)
         → Image API (文生图) → PNG
         → PIL 叠加文字 (composite_text_on_image)
         → ffmpeg 合成滚屏视频 + 背景音乐
```

### 关键文件

| 文件 | 作用 |
|------|------|
| `main.py` (~3800行) | FastAPI 后端全部逻辑：素材生成 API、数据看板 API（pingykj + Meta）、小说管理 API、scraper 控制 API、投放引擎 API、SSE 推送、启动恢复 |
| `static/index.html` (~135KB+) | 单文件前端：生产中心、历史记录、素材浏览、视频样式、小说分析、数据看板、Meta 看板、投放管理等模块 |
| `database.py` | SQLite 数据库层：建表、session CRUD、广告数据 UPSERT、订单写入、小说书籍/章节管理、同步日志、别名管理、Meta 账户管理、投放模板/队列管理 |
| `scraper.py` | 双源爬虫：pingykj API 登录/验证码/token 认证 + 广告日报/订单/小说数据分页同步；Meta Insights 数据同步（通过 meta_api 模块） |
| `meta_api.py` | Meta (Facebook) Graph API 客户端：用 curl 发送 HTTP 请求（解决 Python SSL 与代理兼容问题）、token 管理、速率限制（4次/秒/账户）、广告图片上传、Campaign/AdSet/Ad 创建、Insights 查询 |
| `delivery.py` | 投放引擎：素材审核队列 → 批量创建 Meta 广告（上传创意 → Campaign → AdSet → Ad），SSE 实时推送投放进度，ThreadPoolExecutor 并行投放 |
| `analytics.py` | 数据看板分析引擎：pingykj + Meta 双源 KPI 汇总、日统计、趋势、账户排行、订单查询、小说维度统计、异常检测 |
| `config.json` | 全局配置：API Key/URL、Chat/Image 模型名、分析 prompt、默认并发数 `concurrency`。含 `meta` 配置块（app_id、app_secret、default_access_token、proxy、sync_interval_seconds、rate_limit_per_second、api_version）。通过 `/api/config` 和 `/api/meta/config` 读写 |
| `test_meta_creds.py` | Meta 凭据验证工具：用 curl + 代理测试 access token 有效性，验证 app_id/app_secret |
| `prompts/system_prompt.txt` | LLM 系统提示词 — 6大爆款钩子引擎、购买心理转化、图像模型合规规则 |
| `prompts/rules_core.txt` | 中文绘图规则 — 硬性红线、情感触发器词库、模板融合方法论、预处理 Pipeline |
| `prompts/rules_*.txt` | 按图片类型拆分（scroll/lr_split/tb_split/text_single/shared_modules）的规则片段 |
| `prompts/composition_archetypes.txt` | 视觉基因蓝图 — 按 6 种钩子类型分类的构图参数模板 |
| `prompts/suffix_prompts.txt` | 各类型图片提示词后缀配置（key=value 格式） |
| `prompts/compress_prompt.txt` | 绘图约束压缩模板 — 缩减 prompt 长度至上限以下 |
| `prompts/rules_video_script.txt` | 滚屏视频 AI 文案规则 — 视频旁白/字幕生成规范 |
| `templates_index.json` | 爆款模板索引（由 scripts/build_template_index_v2.py 生成） |
| `video_styles.json` | 视频文字样式配置模板（初始为空，用户通过前端添加样式后填充） |
| `scripts/` | 离线工具脚本：build_template_index.py / v2（构建模板索引）、generate_template_descriptions.py |
| `ziti/` | 字体文件目录（100+ 字体，.ttf/.ttc/.fon） |
| `音乐/` | 背景音乐 MP4 文件目录（需手动创建并放入 MP4 文件，代码中 ffmpeg 合成视频时会从此目录随机选取） |
| `data/dashboard.db` | SQLite 数据库文件（自动创建），存储广告数据、订单、登录会话、同步状态 |

### 图片类型

- **text_single**（1:1 方图）：单帧图，底部叠加文字，必须 ≥2 人物冲突对峙
- **lr_split**（1:1 左右分屏）：垂直分割，左右不同人物，需加左右标签 + 底部叙事文字
- **tb_split**（1:1 上下分屏）：水平分割，上下不同人物/对比
- **scroll**（9:16 竖图）：滚屏视频底图，文字由代码以滚动方式合成

### 并发模型

- 全局 `ThreadPoolExecutor(max_workers=4)` 用于后台生成任务
- 每批次内方图生成使用独立 `ThreadPoolExecutor`，并发数由前端 `concurrency` 参数控制（默认 2，最大 16）
- SSE 通过 `queue.Queue` + `asyncio` 实现事件推送
- 批次取消通过 `threading.Event` 实现

### 输出目录

- Windows：`D:\每日小说`
- 其他系统：`./output`
- 可通过环境变量 `NOVEL_OUTPUT_ROOT` 覆盖
- 每个批次一个子目录（以 batch_id 命名），含 `_progress.json` 和 `_meta.json`

### Chat API 调用

调用兼容 OpenAI Chat Completions 格式的 API 端点 `/chat/completions`。`main.py` 中的 `request_image_prompt_plan` 函数构造 system/user prompt 并解析 JSON 响应。支持分批调用模式 `request_image_prompt_plan_batched`，每批 batch_size 张图。

### 合规要求

`_COMPLIANCE_MAP` 定义了自动替换词表，将可能触发图像模型安全过滤的词汇替换为安全版本。规则在 `prompts/system_prompt.txt` 的 "Image Model Compliance" 部分和 `prompts/rules_core.txt` 中详细说明。

### 数据看板子系统

SQLite 数据库（`data/dashboard.db`）通过 `database.py` 管理，包含以下表：
- `login_session` — 爬虫登录会话（cookies + 过期时间）
- `ad_daily_stats` — 广告日报数据（消耗、收入、展示、点击，按 date + ad_account 唯一）
- `orders` — 订单记录（含 novel_id / chapter_no 关联小说）
- `novel_books` — 小说元数据（novel_id, novel_name, author, cover_url, status, category, intro 等）
- `novel_chapters` — 章节内容（novel_id, chapter_no, chapter_name, content, word_count）
- `sync_logs` / `sync_state` — 同步日志和状态追踪
- `account_aliases` — 广告账户别名（用户可自定义命名）
- `raw_ad_stats` / `raw_orders` — 原始 API 响应存档
- `meta_accounts` — Meta 广告账户（act_id, act_name, access_token, token_expires_at, pingykj_account, status）
- `delivery_templates` — 投放模板（name, targeting_json, placements_json, budget_type/value, bid_strategy, optimization_goal, billing_event, conversion_event, ad_account_id）
- `delivery_queue` — 素材审核队列（batch_id, image_type, image_path, overlay_text, status, reviewer, template_id, error_message）
- `hit_materials` — 爆款素材标记（image_url, video_url, prompt, label, novel_id, novel_name, spend, revenue, roi, impressions, clicks, score）

`database.py` 使用 `contextmanager` 获取连接，自动提交/回滚，PRAGMA WAL 模式 + 外键 + 5s busy_timeout。

### 爬虫子系统

`scraper.py` 负责双源数据同步，纯 `requests` 库实现，无浏览器依赖：

**pingykj 平台同步**：
1. **登录**：前端输入账号密码 → 调用 `login_via_api()` → `POST /jeecgboot/sys/login` 获取 token → 保存到 `data/auth_token.json`，有效期 2 小时
2. **验证码**：通过 `GET /jeecgboot/sys/randomImage/{checkKey}` 获取 base64 图片，前端展示后用户手动输入
3. **数据同步**：`X-Access-Token` 头直接调用后端 API 分页拉取广告日报、订单和小说数据，写入 SQLite
4. **定时同步**：APScheduler 后台线程，默认每 3 分钟执行 `run_full_sync()`（含 `sync_novel_books()` 小说数据同步），间隔可通过 `/api/scraper/sync-interval` 读写

**Meta Insights 同步**：
5. `sync_all_meta_insights()` 遍历所有 active 状态的 Meta 账户，通过 `meta_api.py` 调用 Facebook Graph API `/act_{id}/insights` 拉取广告数据，写入 `ad_daily_stats` 表（含 `meta_account_id` 字段区分来源）

Token 过期后需重新登录。

### Meta API 子系统

`meta_api.py` 封装 Facebook Graph API v25.0，核心设计：

- **HTTP 后端**：使用 `subprocess` 调用 `curl` 命令，而非 Python `requests`/`urllib`。原因是 Windows 环境下 Python SSL 库与某些代理不兼容，curl 更可靠
- **代理支持**：优先级 `config.json` 的 `meta.proxy` → 环境变量 `HTTPS_PROXY` → `https_proxy` → `HTTP_PROXY`
- **速率限制**：`_RATE_LIMITS` 字典按 act_id 跟踪每秒剩余调用次数（默认 4次/秒），`_check_rate_limit()` 在超限时自动 sleep
- **Token 管理**：通过 `/api/meta/accounts` CRUD 管理，支持 `/api/meta/accounts/{act_id}/refresh-token` 刷新
- **核心 API**：
  - `get_ad_accounts(token)` — 获取有权访问的广告账户列表（含 owned + client 账户）
  - `get_insights(act_id, token, ...)` — 拉取广告洞察数据（impressions, clicks, spend, ctr, cpm 等）
  - `upload_ad_image(act_id, token, image_path)` — 上传广告创意图片
  - `create_campaign / create_adset / create_ad` — 创建广告层级结构
  - `get_adsets(act_id, token)` — 获取现有 AdSet（用于导入模板）

### 投放引擎子系统

`delivery.py` 管理从素材到 Facebook 广告的完整投放流程：

1. **审核队列**：素材先进入 `delivery_queue` 表，状态为 `pending` → 人工审核 `approved`/`rejected`
2. **批量投放**：`submit_delivery_batch()` 对已审批的素材，按模板并行创建广告（上传创意 → 创建 Campaign → 创建 AdSet → 创建 Ad），全部以 PAUSED 状态创建
3. **SSE 进度推送**：通过 `_delivery_events` + `_delivery_queues` 实时推送投放进度到前端
4. **并发**：使用 `ThreadPoolExecutor` 并行投放多条广告

### API 路由结构（完整）

**小说管理**
- `GET /api/novels/list` — 小说库列表（分页 + 关键词搜索）
- `GET /api/novels/{novel_id}` — 单本小说详情
- `GET /api/novels/{novel_id}/chapters` — 小说的章节列表
- `GET /api/novels/chapters/{chapter_id}` — 单章节内容（含前后章导航）
- `POST /api/novels/sync-books` — 从投放平台同步书籍列表到本地库
- `POST /api/novels/sync-content` — 同步指定小说的章节内容

**素材生成**
- `POST /api/generate` — 提交生成任务，返回 batch_id，后台异步执行
- `POST /api/cancel` — 取消任务
- `GET /api/progress` / `GET /api/progress/{batch_id}` — 查询进度
- `GET /api/generate/stream/{batch_id}` — SSE 实时推送进度和图片
- `GET /api/history` / `GET /api/history/{batch_id}` — 历史记录
- `DELETE /api/history/{batch_id}` — 删除批次
- `POST /api/history/batch-delete` — 批量删除
- `POST /api/fetch-novel` — 通过 ID 获取小说内容
- `POST /api/analyze-novel` — 分析小说内容
- `POST /api/generate-from-analysis` — 从分析结果生成图片
- `POST /api/generate-from-prompts` — 从已有提示词生成图片
- `GET /api/config` / `POST /api/config` — 全局配置读写
- `GET /api/video-styles` / `POST /api/video-styles` — 视频样式管理
- `GET /api/templates` — 模板索引状态
- `GET /api/fonts` — 可用字体列表
- `GET /api/prompt-rules` — 提示词规则
- `GET /api/stats` — 素材生成统计

**数据看板**
- `GET /api/dashboard/summary` — KPI 汇总（总消耗、总收入、活跃天数、广告数）
- `GET /api/dashboard/daily-stats` — 按日期分页查询广告数据
- `GET /api/dashboard/accounts` — 广告账户列表
- `GET /api/dashboard/trend` — 消耗/收入趋势（支持日/周/月粒度）
- `GET /api/dashboard/orders` — 订单列表
- `GET /api/dashboard/account-ranking` — 按消耗/收入排行
- `GET /api/dashboard/anomalies` — 异常检测（消耗突增/骤降）
- `GET /api/dashboard/novel-stats` — 按小说维度统计订单数据（关联 novels 表）
- `GET /api/dashboard/account-aliases` / `POST` / `DELETE` — 账户别名管理

**爬虫控制**
- `POST /api/scraper/login` — 触发 API 登录，返回验证码图片和 checkKey
- `GET /api/scraper/captcha` — 刷新登录验证码
- `POST /api/scraper/sync` — 手动触发全量同步（含广告、订单、小说数据）
- `GET /api/scraper/session-status` — 查询登录会话状态
- `POST /api/scraper/logout` — 登出，清除 token
- `GET /api/scraper/sync-interval` / `POST` — 读写定时同步间隔（秒）

**Meta 账户管理**
- `GET /api/meta/accounts` — Meta 广告账户列表
- `POST /api/meta/accounts` — 添加/更新 Meta 账户（act_id, act_name, access_token）
- `PUT /api/meta/accounts/{act_id}` — 更新账户信息
- `DELETE /api/meta/accounts/{act_id}` — 删除账户
- `POST /api/meta/accounts/{act_id}/refresh-token` — 刷新账户 access token
- `POST /api/meta/discover` — 用 access token 一键发现所有有权访问的广告账户、BM、主页
- `POST /api/meta/accounts/import` — 从发现结果导入选中账户到本地库

**Meta 数据看板**（独立于 pingykj 看板，使用 `meta_` 前缀方法）
- `GET /api/meta/summary` — Meta 广告 KPI 汇总（总消耗、总收入、活跃天数）
- `GET /api/meta/daily-stats` — Meta 广告按日期分页查询
- `GET /api/meta/trend` — Meta 广告消耗/收入趋势
- `GET /api/meta/account-ranking` — Meta 广告账户排行

**Meta 配置与同步**
- `GET /api/meta/config` / `POST /api/meta/config` — Meta API 配置读写（app_id, app_secret, proxy, sync_interval, rate_limit）
- `POST /api/meta/sync` — 手动触发 Meta Insights 同步
- `GET /api/meta/sync-status` — 查询各账户最后同步状态
- `GET /api/meta/sync-interval` / `POST` — 读写 Meta 同步间隔（秒，默认 300）

**投放引擎**
- `GET /api/delivery/templates` / `POST` — 投放模板列表 / 创建
- `PUT /api/delivery/templates/{id}` / `DELETE` — 更新 / 删除模板
- `GET /api/delivery/templates/fb-adsets/{account_id}` — 从 Facebook 拉取现有 AdSet 列表
- `POST /api/delivery/templates/import` — 从现有 AdSet 导入为投放模板
- `POST /api/delivery/queue` — 添加素材到审核队列
- `GET /api/delivery/queue` — 审核队列列表（分页 + 状态筛选）
- `POST /api/delivery/queue/{id}/approve` — 审核通过（绑定投放模板）
- `POST /api/delivery/queue/{id}/reject` — 审核拒绝
- `POST /api/delivery/queue/batch-approve` — 批量审核通过
- `POST /api/delivery/submit` — 提交投放批次（后台异步执行）
- `GET /api/delivery/progress/{batch_id}` — 查询投放进度
- `GET /api/delivery/stream/{batch_id}` — SSE 实时推送投放进度
- `GET /api/delivery/records` — 投放记录列表

**爆款素材**
- `GET /api/hit-materials` — 爆款素材列表
- `POST /api/hit-materials` — 标记素材为爆款
- `GET /api/hit-materials/lookup` — 按 image_url 查找是否已标记
- `PUT /api/hit-materials/{mid}` — 更新爆款素材信息（评分、备注等）
- `DELETE /api/hit-materials/{mid}` — 取消爆款标记
