# 多书城（A/B/合计）端到端验证

Task 9 交付物。分两块：**(a) 本机确定性验证**（可随时重跑）与 **(b) 服务器真实端到端清单**（需人工在部署机执行）。
另有 (c) 迁移注意事项。

原始简报（`task-9-brief.md`）Step 1 要求 `curl` 触发两个书城的**真实全量同步**。经编排方裁决（Ruling 14）
**不执行**：会把 B 站整库数据拉进本机 dev 库，且属对真实外部系统的写操作。真实同步留给用户在服务器上做，
步骤见 (b)。

---

## (a) 本机确定性验证：`scripts/verify_site_e2e.py`

### 跑法

```bash
python scripts/verify_site_e2e.py
```

用 `tempfile` 建空库、`database.DB_PATH` 指向它、`database.init_db()`，全程**不碰** `data/dashboard.db`、不联网。
`DB_PATH` 在 `import main` 之前重定向，因此 `main` 模块级的 `init_db()` 与调度器也落在临时库上。

合成数据（`seed()`）：

| | 小说 n1 | 小说 n2（只在 A 站） | 订单 |
|---|---|---|---|
| A 站 `site='a'` | 消耗 100 | 消耗 70 | 2 笔，金额 30 + 20 = 50 |
| B 站 `site='b'` | 消耗 40 | — | 3 笔，金额 10×3 = 30 |
| 合计 `site=None` | 消耗 140 / 5 单 | 消耗 70 / 0 单 | 5 笔，金额 80 |

### 证明的四件事

1. **隔离**（`check_novel_books`）
   - `get_novel_books(site='a')` 书目 = `{n1, n2}`；`site='b'` 书目 = `{n1}` —— 只在 A 站存在的 n2
     不会出现在 B 站；B 站 n1 的 `order_count` = 3，不串入 A 站的 2 单。
2. **合计 = 去重 + 相加**（`check_novel_books`）
   - `site=None` 时 n1 **只有一行**，`book_ad_spend` = 100 + 40 = 140，`order_count` = 2 + 3 = 5
     （订单数不得被 LEFT JOIN 放大），`conversion_cost` = 140/5 = 28.0。
3. **比率用合计后的分子分母重算**（`check_summary`）—— 这是最容易写错的地方
   - `site=None`：`total_spend` 140、`total_revenue` 80、`order_count` 5，均 = 两站之和
   - `roi` = 80/140 = **0.57**；而两站 roi 分别为 0.5 / 0.75，相加 = 1.25、平均 = 0.62 —— 脚本**显式断言**
     合计 roi 不等于这两个错值
   - `cpa` = 140/5 = **28.0**；两站 cpa 为 50.0 / 13.33，相加 = 63.33、平均 = 31.67 —— 同样显式断言不等
   - `subscribe_roi` 亦按 `subscribe_amount` / `total_spend` 重算
4. **路由层真的随 site 变化**（`check_routes`）
   - 用 `dependency_overrides[main.get_current_user]` 注入登录态，**不猜、不改任何用户口令**
   - `/api/dashboard/summary?site=a|b` 的 `total_spend` 分别为 100 / 40，不带 site = 140；`order_count` 2/3/5
   - `/api/novels/list?site=a|b` 返回 `{n1,n2}` / `{n1}`，不带 site 时 n1 `book_ad_spend` = 140、`order_count` = 5
   - 注：Task 6 的 `verify_routes_site.py` 只证明「路由接受 site 且透传」，因 FastAPI 会忽略未知 query 参数，
     不报错 ≠ 过滤生效；真正证明过滤生效的是本脚本这一段。

### 实测结果（当前代码）

```
OK: novel_books 按 site 隔离，合计 = 去重 + 消耗/订单相加
OK: get_summary 合计=两站之和，roi=0.57（非平均/相加）、cpa=28.0
OK: 路由 /api/dashboard/summary 与 /api/novels/list 随 site 变化，合计=两站之和
OK: 多书城端到端验证通过（隔离 / 合计=相加 / 比率重算 / 路由透传）
```

### 反向验证（证明断言不是摆设）

**反向 1 —— 把 `analytics._add_site_filter` 改成空操作**（`return None`）：

```
Traceback (most recent call last):
  File "scripts/verify_site_e2e.py", line 163, in main_
    check_summary()
  File "scripts/verify_site_e2e.py", line 97, in check_summary
    assert sa["total_spend"] == A_SPEND and sb["total_spend"] == B_SPEND, (sa, sb)
AssertionError: ({'total_spend': 140.0, ... 'roi': 0.57, ... 'order_count': 5, ... 'cpa': 28.0, ...},
                 {'total_spend': 140.0, ... 'roi': 0.57, ... 'order_count': 5, ... 'cpa': 28.0, ...})
```

`site='a'` 与 `site='b'` 都返回了合计值 140 —— 隔离失效被立刻抓住，脚本退出码 1。

**反向 2 —— 把 `database.get_novel_books` 的 `nb.site = ?` 过滤去掉**：

```
Traceback (most recent call last):
  File "scripts/verify_site_e2e.py", line 162, in main_
    check_novel_books()
  File "scripts/verify_site_e2e.py", line 72, in check_novel_books
    assert a["n1"]["book_ad_spend"] == A_SPEND, a["n1"]
AssertionError: {'novel_id': 'n1', ..., 'book_ad_spend': 140.0, ..., 'order_count': 5, 'conversion_cost': 28.0}
```

A 站书目查询返回了合计消耗 140 —— 小说维度的隔离失效同样被抓住，退出码 1。

两处改动均已还原（`git diff` 对 `analytics.py` / `database.py` 为空）。

### 回归

以下 8 个脚本在本机全部通过（7 个既有 + 本次新增）：

```
verify_sites_config        PASS  OK: 站点配置读取正确
verify_site_io             PASS  OK: 读写按 site 隔离，合计=去重+相加
verify_site_migration      PASS  OK: 半迁移表（有 site 列、唯一键无 site）会被继续重建
verify_scraper_site        PASS  OK: 会话按 (user, site) 分开，URL 与落库均按站点取
verify_analytics_site      PASS  OK: 看板按 site 过滤，合计=两站相加
verify_routes_site         PASS  OK: 非法 site —— 写类 400 / 读类归一为合计
verify_scheduler_sites     PASS  ALL OK: 定时任务按站点遍历（站点=['a', 'b']）
verify_site_e2e            PASS  OK: 多书城端到端验证通过（隔离 / 合计=相加 / 比率重算 / 路由透传）
```

另有 `python -c "import main"` 退出码 0（无语法/导入错误）。运行过程中 `data/dashboard.db` 的 mtime 未变。

---

## (b) 服务器真实端到端清单（需人工执行，本机未做）

> 以下命令里的 `<token>` 为登录后拿到的 `session_token`；`BASE=http://127.0.0.1:8000`。
> 登录：`curl -s -X POST $BASE/api/auth/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"<你的口令>"}'`

**Step 0 —— 先备份数据库**（迁移不可逆，且没有自动备份）

```bash
cp data/dashboard.db data/dashboard.db.bak-$(date +%Y%m%d-%H%M%S)
```

**Step 1 —— 确认两个站点都已配置**

```bash
python -c "import json;print([s['key'] for s in json.load(open('config.json'))['meta']['pingykj_sites']])"
```

Expected：`['a', 'b']`。若缺 `b`，在 `config.json` 的 `meta.pingykj_sites` 补齐
（`key` / `name` / `base_url` / `content_url`，换域名只改这里，不改代码）。

**Step 2 —— 确认 B 站凭据**

`users.pingykj_username` / `pingykj_password` 是每用户凭据，两站复用同一份（不新增凭据列）。
B 站账号密码若与 A 站不同，需先在前端「用户管理」里重新绑定。
可用 `GET /api/users/{id}/pingykj-captcha` + `POST /api/users/{id}/reconnect-pingykj` 验证能被 B 站接受。

**Step 3 —— 分别同步两个站点**

```bash
curl -s -X POST "$BASE/api/scraper/sync?site=a" -H "Authorization: Bearer <token>"
curl -s -X POST "$BASE/api/scraper/sync?site=b" -H "Authorization: Bearer <token>"
```

Expected：两次均返回成功，进度经 `/api/meta/sync-progress` 或 `/api/scraper/sync-status` 可见。
不带 `site` 的 `POST /api/scraper/sync` 会**遍历所有站点**（Task 7 定时任务同此行为）。

**Step 4 —— 核对库内 site 分布**

```bash
python -c "
import database
with database.get_conn() as c:
    for t in ['ad_daily_stats','orders','novel_books','novel_chapters','raw_ad_stats','sync_state']:
        print(t, dict(c.execute(f'SELECT site, COUNT(*) FROM {t} GROUP BY site').fetchall()))
"
```

Expected：B 站同步成功后，各表都能看到 `a` 与 `b` 两组。`orders` / `novel_chapters` 里
同一业务 id 会在两站各存一行（唯一键为 `(site, ...)`）；只有一边有数据说明那站同步失败。

**Step 5 —— 核对合计 = A + B**

```bash
python -c "
import analytics
for site in ['a','b',None]:
    r = analytics.get_summary(site=site)
    print('site=', site, 'spend=', r['total_spend'], 'revenue=', r['total_revenue'], 'orders=', r['order_count'])
"
```

Expected：`site=a` + `site=b` 的 `total_spend` / `total_revenue` / `order_count` 分别等于 `site=None` 的值。
再核对比率：`site=None` 的 `roi` 应等于 `合计revenue / 合计spend`，**不等于**两站 roi 的相加或平均
（本机反向验证已证明这两者数值确实不同）。

**Step 6 —— 核对小说去重 + 消耗相加**

```bash
python -c "
import database
a = {b['novel_id']: b['book_ad_spend'] for b in database.get_novel_books(page_size=9999, site='a')['data']}
b = {x['novel_id']: x['book_ad_spend'] for x in database.get_novel_books(page_size=9999, site='b')['data']}
allb = {x['novel_id']: x['book_ad_spend'] for x in database.get_novel_books(page_size=9999, site=None)['data']}
dup = sorted(set(a) & set(b))
print('两站共有小说数:', len(dup))
for nid in dup[:5]:
    assert abs(allb[nid] - (a[nid] + b[nid])) < 0.01, (nid, a[nid], b[nid], allb[nid])
print('OK: 两站共有小说消耗 = A + B；合计不重复计数')
"
```

Expected：`OK: 两站共有小说消耗 = A + B`。

**Step 7 —— 前端核对**

「数据看板」与「小说管理」顶部切「合计 / A站 / B站」：同名小说在合计下只出现一行，消耗为两站相加；
切到 B 站时不应出现只在 A 站存在的书。合计为只读，不可写入。

---

## (c) 迁移注意事项

- **首次启动会动 10 张表（9 张重建 + 1 张补列）**：`_SITE_REBUILD_DDL` 覆盖 `ad_daily_stats`、`orders`、`raw_ad_stats`、
  `raw_orders`、`sync_state`、`account_aliases`、`novel_books`、`novel_chapters`、`novel_spend_snapshots`
  共 9 张，另有 `sync_logs` 只补列。老数据统一回落 `site='a'`（`SITE_DEFAULT`）。
- **库文件会临时膨胀到约 2 倍**：重建走 `ALTER TABLE ... RENAME TO <t>__pre_site` → `CREATE` → `INSERT SELECT`
  → `DROP`，新旧两份数据在事务内共存。**先确认磁盘剩余空间 > 当前库文件大小**再启动。
  重建在 `BEGIN IMMEDIATE` 显式事务里，中途失败会整体回滚（不会留下「空表 + 孤儿 `*__pre_site`」）。
- **代码不会自动备份**：迁移里没有任何 `shutil.copy` / `.bak` 逻辑
  （`data/dashboard.db.bak-20260911` 是人工做的）。**务必先 `cp` 一份再启动新版本**：

  ```bash
  cp data/dashboard.db data/dashboard.db.bak-$(date +%Y%m%d-%H%M%S)
  ```

  同时确认没有 `-wal` / `-shm` 遗留（服务须先停干净，否则拷贝到的可能是不一致的中间态）。
- **幂等**：迁移按「`site` 列是否存在 + 唯一键是否含 `site`」（`_site_unique_ok`）判断，
  半迁移状态（列已加、唯一键未改）会被继续重建；重复启动无副作用。
- 迁移只在 `init_db()` 里跑一次；`data/dashboard.db` 已达 GB 级时首次启动会明显变慢，属正常。
