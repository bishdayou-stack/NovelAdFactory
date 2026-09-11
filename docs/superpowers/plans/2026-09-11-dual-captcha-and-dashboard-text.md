# 通用模式双站验证码登录 + 看板顶部文案瘦身 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ① 书城凭据弹窗选「通用」时，把**两个书城各自的验证码都展示出来**，一次把两站都登录上；② 数据看板顶部的凭据按钮与状态文案瘦身，不再因为文字过长而换行。

**Architecture:** ① 纯前端循环调用**已有**接口（`GET /api/scraper/captcha?site=<key>` 与 `POST /api/scraper/login?site=<key>` 都已支持按站点），无需新接口；② 可见文案改短、把站点明细放进元素的 `title`（悬停可见），并加 `nowrap`，信息不丢但不占宽。

**Tech Stack:** 原生 JS 单文件前端 `static/index.html` + 既有 FastAPI 接口。

**Spec:** 无独立 spec —— 设计在对话中确认（用户明确要求「两个书城的验证码都展示出来让用户输入」；文案瘦身由实现方决定方式）。

## Global Constraints

- **不新增后端接口**：两站的验证码与登录都用现有的、已支持 `site` 参数的接口。
- **不改数据库、不改 `config.json`**。
- 站点列表从 `GET /api/sites` 取（**不要硬编码 a/b**），站点 `key` 不可变。
- 选「A站 / B站」时**保持现有单站流程不变**；只有选「通用」才走多站流程。
- 一站失败**不得**阻断其它站点；逐站显示成败。
- 看板文案：可见文字必须**短且不换行**；站点明细放 `title`（悬停提示），**信息不丢失**。
- 本仓库没有前端测试框架，验证靠手工/Playwright，报告须如实区分「已实测」与「未验证」。

---

### Task 1: 通用模式下的双站验证码登录

**Files:**
- Modify: `static/index.html`（书城凭据弹窗的 HTML 与脚本）

**Interfaces:**
- Consumes: `GET /api/sites`（站点列表）、`GET /api/scraper/captcha?username=&password=&site=<key>`、
  `POST /api/scraper/login?site=<key>`（body `{username,password,captcha,check_key}`）、
  `PUT /api/auth/pingykj-credentials`（body `{pingykj_username,pingykj_password,site}`）
- Produces: `window._pendingCaptchas`（`{siteKey: {check_key, image}}`）

- [ ] **Step 1: 把单行验证码区改成可容纳多行的容器**

现在的 `#pingykjCaptchaRow`（`static/index.html` 凭据弹窗内，约 `:663`）是**一行固定的**图 + 输入 + 刷新按钮。
改成：

```html
                <div id="pingykjCaptchaRows" class="space-y-2"></div>
```

保留原有元素 id 的**兼容**不必做（下面第 2 步会把引用它们的脚本一并改掉）。
原来的「刷新」按钮并入每行的行内按钮。

- [ ] **Step 2: 渲染 N 行验证码 + 记录每站 check_key**

```javascript
    window._pendingCaptchas = {};   // { siteKey: {check_key, image} }

    // 需要展示验证码行的站点：选「通用」= 全部站点；选具体站 = 只有该站
    function _credsTargetSites() {
      var sel = document.getElementById('pingykjCredsSite');
      var one = sel ? sel.value : '';
      if (one) return [one];
      return Object.keys(window._siteNames || {});
    }

    function _renderCaptchaRows(list) {   // list: [{site, image, check_key, error}]
      var box = document.getElementById('pingykjCaptchaRows');
      box.innerHTML = list.map(function (it) {
        var name = (window._siteNames && window._siteNames[it.site]) || it.site;
        var head = '<div class="text-[11px] font-medium text-slate-600 mb-1">' + escapeHtml(name) + '</div>';
        if (it.error) {
          return '<div class="rounded-lg border border-amber-200 bg-amber-50/60 p-2">' + head +
                 '<div class="text-[11px] text-amber-700">取码失败：' + escapeHtml(it.error) +
                 '（可直接点「登录验证」试试免验证码登录）</div>' +
                 '<input data-captcha-site="' + it.site + '" type="text" placeholder="验证码（可留空）" ' +
                 'class="mt-1 w-full px-2 py-1.5 border border-slate-200 rounded-lg text-xs" /></div>';
        }
        return '<div class="rounded-lg border border-slate-200 p-2">' + head +
               '<div class="flex items-center gap-2">' +
               '<img src="' + it.image + '" title="点击刷新" data-captcha-refresh="' + it.site + '" ' +
               'class="h-9 rounded border border-slate-200 cursor-pointer" />' +
               '<input data-captcha-site="' + it.site + '" type="text" placeholder="验证码" ' +
               'class="flex-1 px-2 py-1.5 border border-slate-200 rounded-lg text-xs" />' +
               '<button type="button" data-captcha-refresh="' + it.site + '" ' +
               'class="px-2 py-1.5 text-[11px] rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 cursor-pointer">刷新</button>' +
               '</div><div data-captcha-result="' + it.site + '" class="text-[11px] mt-1"></div></div>';
      }).join('');
    }
```

- [ ] **Step 3: 取码改为按站点循环**

```javascript
    function _fetchCaptchas(username, password) {
      var sites = _credsTargetSites();
      var box = document.getElementById('pingykjCaptchaRows');
      box.innerHTML = '<div class="text-[11px] text-slate-400">正在获取验证码…</div>';
      return Promise.all(sites.map(function (s) {
        var qs = '?username=' + encodeURIComponent(username) + '&password=' + encodeURIComponent(password) +
                 '&site=' + encodeURIComponent(s);
        return fetch('/api/scraper/captcha' + qs)
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (r) {
            if (r.ok && r.d.image) {
              window._pendingCaptchas[s] = { check_key: r.d.check_key, image: r.d.image };
              return { site: s, image: r.d.image, check_key: r.d.check_key };
            }
            return { site: s, error: (r.d && r.d.detail) || '未知错误' };
          })
          .catch(function (e) { return { site: s, error: String(e.message || e) }; });
      })).then(function (list) { _renderCaptchaRows(list); });
    }
```

点击行内的图或「刷新」按钮时，只刷新该站的：绑定一个事件委托（在弹窗容器上监听 `click`，取 `data-captcha-refresh`），
调用 `_fetchCaptchas` 的**单站版本**（可复用同一函数，把 `_credsTargetSites()` 换成 `[site]`——用一个可选参数实现）。

- [ ] **Step 4: 登录验证改为逐站登录**

```javascript
    function _doLogin(username, password) {
      var sites = _credsTargetSites();
      var box = document.getElementById('pingykjCaptchaRows');
      return Promise.all(sites.map(function (s) {
        var input = box.querySelector('input[data-captcha-site="' + s + '"]');
        var captcha = input ? input.value.trim() : '';
        var pend = window._pendingCaptchas[s] || {};
        var res = box.querySelector('[data-captcha-result="' + s + '"]');
        if (res) { res.textContent = '登录中…'; res.className = 'text-[11px] mt-1 text-slate-400'; }
        return fetch('/api/scraper/login?site=' + encodeURIComponent(s), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: username, password: password,
                                 captcha: captcha, check_key: pend.check_key || '' })
        }).then(function (r) { return r.json().then(function (d) { return { s: s, ok: r.ok, d: d }; }); })
          .then(function (r) {
            var ok = !!(r.d && (r.d.status === 'ok' || r.d.success));
            if (res) {
              res.textContent = ok ? '✅ 登录成功' : ('❌ ' + ((r.d && r.d.message) || '登录失败'));
              res.className = 'text-[11px] mt-1 ' + (ok ? 'text-emerald-600' : 'text-red-500');
            }
            return { site: s, ok: ok };
          })
          .catch(function () {
            if (res) { res.textContent = '❌ 请求失败'; res.className = 'text-[11px] mt-1 text-red-500'; }
            return { site: s, ok: false };
          });
      })).then(function (results) {
        var anyOk = results.some(function (r) { return r.ok; });
        if (anyOk) {
          // 任一站登录成功即保存凭据（选「通用」时 site 传空 → 存通用凭据）
          return fetch('/api/auth/pingykj-credentials', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pingykj_username: username, pingykj_password: password,
                                   site: document.getElementById('pingykjCredsSite').value || '' })
          }).then(function () { return anyOk; });
        }
        return anyOk;
      });
    }
```

**注意**：`/api/scraper/login` 返回的是 `{status: "ok"|"failed", message}`（不是 `success`），
断言时以实际返回为准——**先读一遍 `main.py` 的该接口**再落笔。

- [ ] **Step 5: 接上原有的两个按钮**

把「获取验证码」按钮的处理器改为调用 `_fetchCaptchas(username, password)`；
「登录验证」按钮改为 `_doLogin(...)`，成功后沿用既有的收尾（关弹窗 / 刷新状态 / `loadSiteOptions()` 如涉及）。
删除旧的单行取码/登录代码（`_pingykjCheckKey` 等变量一并清理，注意**全仓 grep** 确认没有别处引用）。

- [ ] **Step 6: 手工验证**

启动 `python -m uvicorn main:app --port 8000`，用浏览器/Playwright 验证：
1. 弹窗选「通用」→ 点「获取验证码」→ **出现两行**（A站、B站各一行，各带图与输入框）；
2. 选「A站」→ 只出现**一行**（回归：单站流程不变）；
3. 某站取码失败时该行显示提示，**不阻断**另一站，且仍可提交（走免验证码登录）；
4. 点「登录验证」→ 两行各自显示成功/失败（可用打桩或真实验证码；真实验证码视你能否看到图）。
   如无法真实登录，请**打桩** `fetch` 验证两站的请求 URL 与 body 都正确，并在报告中如实说明未做真实登录。
**不要**改 `data/dashboard.db`；验证完关掉服务。

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat(site): 通用模式同时展示两个书城的验证码并逐站登录（任一站成功即保存凭据）"
```

---

### Task 2: 看板顶部文案瘦身（不换行）

**Files:**
- Modify: `static/index.html`（`updatePingykjCredsUI` / `setDashSessionStatus` 与它们的调用处）

**Interfaces:**
- Consumes: 无（纯前端）
- Produces: `updatePingykjCredsUI(configured, detail)`、`setDashSessionStatus(status, text, detail)`

- [ ] **Step 1: 凭据按钮改为「短标签 + title 明细」**

把 `updatePingykjCredsUI` 改成：

```javascript
    function updatePingykjCredsUI(hasCreds, detail) {
      var label = document.getElementById('pingykjCredsLabel');
      var btn = document.getElementById('btnPingykjCreds');
      label.textContent = hasCreds ? '书城凭据 ✓' : '书城凭据';
      label.style.whiteSpace = 'nowrap';
      if (btn) {
        btn.title = hasCreds
          ? ('已配置站点：' + (detail || '（未知）') + '　点击可修改')
          : '设置书城登录凭据';
      }
    }
```

- [ ] **Step 2: 状态文字改为「短文案 + title 明细」**

```javascript
    function setDashSessionStatus(status, text, detail) {
      var dot = document.getElementById('dashSessionDot');
      var txt = document.getElementById('dashSessionText');
      var colors = {
        online:  { dot: 'bg-emerald-400', txt: 'text-emerald-600' },
        offline: { dot: 'bg-red-400',     txt: 'text-red-500' },
        noconfig:{ dot: 'bg-amber-400',   txt: 'text-amber-600' },
        checking:{ dot: 'bg-slate-300',   txt: 'text-slate-500' },
      };
      var c = colors[status] || colors.checking;
      dot.className = 'w-2.5 h-2.5 rounded-full ' + c.dot;
      txt.textContent = text;
      txt.className = 'text-xs whitespace-nowrap ' + c.txt;
      txt.title = detail || text;      // 明细放悬停提示，可见文字保持短
    }
```

- [ ] **Step 3: 调用处改传「短文案 + 明细」**

`checkDashboardSession` 里（`static/index.html` 约 `:5048-5081`）改为：

```javascript
        var cfgNames = cfgKeys.map(function(k){ return sites[k].name || siteDisplayName(k); }).join('、');
        var detail = '已配置站点：' + (cfgNames || u.pingykj_username || '（未知）');
        if (cfgKeys.length || u.pingykj_username) {
          fetch('/api/scraper/session-status').then(function(r) { return r.json(); }).then(function(s) {
            if (s.valid) {
              setDashSessionStatus('online', '书城在线', detail + '；默认站点会话有效');
            } else {
              setDashSessionStatus('offline', '书城掉线', '点击「书城凭据」重新登录；' + detail);
            }
          }).catch(function() {
            setDashSessionStatus('online', '书城已配置', detail);
          });
          updatePingykjCredsUI(true, cfgNames || u.pingykj_username || '');
        } else {
          setDashSessionStatus('noconfig', '未配置书城凭据', '点击「书城凭据」设置登录账号');
          updatePingykjCredsUI(false);
        }
```

`markScraperOffline()` 同样改为短文案 + 明细：
`setDashSessionStatus('offline', '书城掉线', '点击「书城凭据」重新登录')`。

**全仓 grep** `setDashSessionStatus(` 与 `updatePingykjCredsUI(` 的所有调用点，逐个确认改后不丢信息
（短文案 + `title` 明细），且**没有**别处仍传长文案。

- [ ] **Step 4: 手工验证**

浏览器打开看板，确认：
1. 凭据按钮显示「书城凭据 ✓」（**不再**出现 `书城: A站(pingykj)/B站(relishnovel)`）；
2. 状态文字为「书城在线」这类**短文案**；鼠标悬停在按钮与状态上能看到站点明细；
3. **顶栏不再换行**（把窗口调窄到 1280px 观察，按钮与状态文字保持单行）；
4. 未配置凭据的用户显示「书城凭据」/「未配置书城凭据」，与改前一致。

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "fix(dashboard): 顶部凭据按钮与状态文案瘦身，站点明细移入悬停提示，不再换行"
```

---

## Self-Review

**需求覆盖**

| 需求 | 对应任务 |
|---|---|
| 通用模式下两个书城的验证码都展示、分别输入 | Task 1 Step 1–3 |
| 一次把两站都登录上，逐站显示成败 | Task 1 Step 4 |
| 一站失败不阻断另一站 | Task 1 Step 3/4（`Promise.all` + 逐站错误行） |
| 选具体站点时保持单站流程 | Task 1 Step 2（`_credsTargetSites`） |
| 看板顶部不再换行 | Task 2 Step 1–3 |

**占位符扫描**：无 TBD；每处改动均给出可落地代码。

**类型一致性**：`window._pendingCaptchas` 在 Step 2 定义、Step 3 写入、Step 4 读取，结构一致
（`{check_key, image}`）；`_credsTargetSites()` 在 Step 2 定义、Step 3/4 共用。

**已知需在执行时确认的点**：
- `/api/scraper/login` 的返回字段是 `status` 还是 `success`，须按 `main.py` 实际实现落笔（Step 4 已注明）。
- 旧单行取码代码里的变量（如 `_pingykjCheckKey`）需全仓 grep 后清理干净，避免留下死引用。
- Task 2 的调用点可能不止 `checkDashboardSession` 与 `markScraperOffline` 两处，须 grep 全量。
