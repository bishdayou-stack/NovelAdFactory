# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Novel Ad Factory（小说广告素材工厂）** — 将小说内容批量转化为 Facebook 信息流广告素材（图片+视频）的 Web 应用，面向欧美女性 40+ 受众。同时内置**广告数据看板**，通过 Playwright 爬虫同步投放平台的广告消耗与订单数据。

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

整个应用是**单文件 FastAPI 后端 + 单文件 HTML 前端 + SQLite 数据库**，无 ORM。两大子系统：
- **素材工厂**：小说 → LLM → 图片 → 视频
- **数据看板**：Playwright 爬虫 → SQLite → 广告 ROI 分析

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
| `main.py` (~3100行) | FastAPI 后端全部逻辑：素材生成 API、数据看板 API、scraper 控制 API、SSE 推送、启动恢复 |
| `static/index.html` (~135KB+) | 单文件前端：生产中心、历史记录、素材浏览、视频样式、小说分析、数据看板等模块 |
| `database.py` | SQLite 数据库层：建表、session CRUD、广告数据 UPSERT、订单写入、同步日志、别名管理 |
| `scraper.py` | Playwright 爬虫：浏览器登录（手动验证码）、认证头捕获、广告日报/订单 API 分页同步 |
| `analytics.py` | 数据看板分析引擎：KPI 汇总、日统计、趋势、账户排行、异常检测 |
| `config.json` | 全局配置：API Key/URL、Chat/Image 模型名、分析 prompt、并发数（通过 `/api/config` 读写） |
| `prompts/system_prompt.txt` | LLM 系统提示词 — 6大爆款钩子引擎、购买心理转化、图像模型合规规则 |
| `prompts/rules_core.txt` | 中文绘图规则 — 硬性红线、情感触发器词库、模板融合方法论、预处理 Pipeline |
| `prompts/rules_*.txt` | 按图片类型拆分（scroll/lr_split/tb_split/text_single/shared_modules）的规则片段 |
| `prompts/composition_archetypes.txt` | 视觉基因蓝图 — 按 6 种钩子类型分类的构图参数模板 |
| `prompts/suffix_prompts.txt` | 各类型图片提示词后缀配置（key=value 格式） |
| `templates_index.json` | 爆款模板索引（由 scripts/build_template_index.py 生成） |
| `video_styles.json` | 视频文字样式库（字体、颜色、背景类型等） |
| `scripts/` | 离线工具脚本：构建模板索引、生成模板描述 |
| `ziti/` | 字体文件目录（80+ 字体） |
| `音乐/` | 背景音乐 MP4 文件目录 |
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
- `orders` — 订单记录
- `sync_logs` / `sync_state` — 同步日志和状态追踪
- `account_aliases` — 广告账户别名（用户可自定义命名）
- `raw_ad_stats` / `raw_orders` — 原始 API 响应存档

`database.py` 使用 `contextmanager` 获取连接，自动提交/回滚，PRAGMA WAL 模式 + 外键 + 5s busy_timeout。

### 爬虫子系统

`scraper.py` 负责从广告投放平台（pingykj.com）同步数据：

1. **登录**：前端输入账号密码 → `POST /jeecgboot/sys/login` 获取 token → 保存到 `data/auth_token.json`，有效期 2 小时
2. **数据同步**：`requests` 库 + `X-Access-Token` 头直接调用后端 API 分页拉取广告日报和订单，写入 SQLite
3. **定时同步**：APScheduler 后台线程，默认每 3 分钟执行 `run_full_sync()`，间隔可通过 `/api/scraper/sync-interval` 读写

Token 过期后需重新登录。

### API 路由结构（完整）

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
- `GET /api/dashboard/account-aliases` / `POST` / `DELETE` — 账户别名管理

**爬虫控制**
- `POST /api/scraper/login` — 触发 Playwright 浏览器登录
- `POST /api/scraper/sync` — 手动触发全量同步
- `GET /api/scraper/session-status` — 查询登录会话状态
- `POST /api/scraper/logout` — 登出，清除 token
- `GET /api/scraper/sync-interval` / `POST` — 读写定时同步间隔（秒）
