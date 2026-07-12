# BM 管理 & 多应用支持 — 设计文档

**日期**: 2026-07-12  
**状态**: 已确认

## 需求概述

1. **BM 管理**：BM 作为独立实体管理，System User Token 提到 BM 级别，BM 名称自动获取只读，支持编辑 Token 和删除
2. **多应用支持**：支持配置多套 Meta App（app_id/app_secret），每个 BM 可绑定不同的 App，支持设置默认 App

Meta 管理页面现有功能（看板、账户树、发现导入、同步）全部保持不变。

## 架构设计

### 新增表

**`app_config`** — 应用配置

```sql
CREATE TABLE IF NOT EXISTS app_config (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name    TEXT NOT NULL,          -- 应用别名（如"主应用"、"A线"）
    app_id      TEXT NOT NULL,          -- Meta App ID
    app_secret  TEXT NOT NULL,          -- Meta App Secret
    is_default  INTEGER DEFAULT 0,      -- 是否默认应用
    user_id     INTEGER DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**`bm_config`** — BM 配置

```sql
CREATE TABLE IF NOT EXISTS bm_config (
    bm_id         TEXT PRIMARY KEY,     -- Meta BM ID
    bm_name       TEXT NOT NULL,        -- BM 名称（发现时自动获取）
    system_token  TEXT,                 -- System User Token
    app_id        TEXT,                 -- 绑定的 App ID（FK → app_config.app_id，可为空）
    user_id       INTEGER DEFAULT 1,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 修改表

**`meta_accounts`** — 新增 `bm_id` 字段：

```sql
ALTER TABLE meta_accounts ADD COLUMN bm_id TEXT;
```

- `bm_id` 关联 `bm_config.bm_id`
- `access_token` 保留，作为兼容过渡，Token 获取优先级见下文
- `pingykj_account` 字段保留，不再作为 BM 分组依据（`bm_id` 替代）

### Token 获取优先级

同步或 API 调用时需要 Token 时，按以下顺序查找：

1. `bm_config.system_token` — BM 级别的 System User Token（主路径）
2. `meta_accounts.access_token` — 账户级 Token（兼容旧数据）
3. `config.json → meta.default_access_token` — 全局默认（最终兜底）

### App 绑定优先级

1. `bm_config.app_id` — BM 绑定的 App
2. `app_config` 中 `is_default=1` 的 App
3. `config.json → meta.app_id` — 全局默认
4. 无 App 配置时仅用 Access Token（当前行为）

## 后端 API

### App 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/app/list` | 获取所有 App 列表 |
| POST | `/api/app` | 新增 App（body: app_name, app_id, app_secret, is_default） |
| PUT | `/api/app/{id}` | 编辑 App |
| DELETE | `/api/app/{id}` | 删除 App |

- 设置 `is_default=1` 时自动取消其他默认
- 删除默认 App 时自动将下一个 App 设为默认

### BM 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/bm/list` | BM 列表（含每个 BM 的账户数） |
| POST | `/api/bm/discover` | 输入 System User Token，发现 BM 并自动保存 |
| POST | `/api/bm` | 手动新增/更新 BM |
| PUT | `/api/bm/{bm_id}` | 编辑 Token 或 App 绑定 |
| DELETE | `/api/bm/{bm_id}` | 删除 BM（不删除关联账户） |
| GET | `/api/bm/{bm_id}/accounts` | 查看 BM 下账户列表 |

- `POST /api/bm/discover` 调用 `meta_api.discover_businesses(token)`，获取 BM 列表后自动写入 `bm_config`
- 删除 BM 不会删除关联的 `meta_accounts`，账户的 `bm_id` 字段保留（用于后续重新绑定）

### 同步适配

`_sync_one_meta_account` 和 `sync_all_meta_insights` 的 Token 获取改为上述优先级链。

## 前端

### BM/App 管理页面

新增一个标签页（Tab），放在"Meta 管理"旁边。

**上半部分 — App 管理：**

- 表格展示所有 App：名称、App ID（脱敏显示）、是否默认、操作按钮
- "新增 App"按钮 → 弹窗填写 app_name / app_id / app_secret / 设为默认
- 每行操作：编辑（弹窗）、删除（确认后删除）
- 默认 App 行高亮标记

**下半部分 — BM 管理：**

- 表格展示所有 BM：名称、BM ID、账户数、绑定的 App（下拉选择）、Token 状态（有/无/过期）
- Token 列可点击编辑（弹窗输入新 Token）
- App 列下拉选择已配置的 App（含"使用默认"选项）
- "发现 BM"按钮 → 弹窗输入 System User Token → 调 Meta API 自动发现 BM 并写入
- "手动添加"按钮 → 弹窗输入 BM ID + 名称 + Token
- 删除按钮 → 确认后删除

### Meta 管理页面

不变。账户树、发现导入、看板、同步全部保持现有行为。

## 数据迁移

部署后首次启动自动执行：

1. 创建 `app_config` 和 `bm_config` 表
2. `meta_accounts` 添加 `bm_id` 列
3. 将 `config.json` 中的全局 App 配置迁移到 `app_config` 表（作为默认 App）
4. 根据 `meta_accounts.pingykj_account` 的字符串值尝试匹配已有 BM（过渡期逻辑，匹配不上则 `bm_id` 留空）

## 不涉及

- 不修改 Meta 管理页面
- 不修改账户发现/导入流程（维持现有交互）
- 不实现 OAuth / Token 自动交换（System User Token 仍手动输入）
