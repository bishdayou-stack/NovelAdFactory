/*
 * 验证：数据看板是首页（启动落地页），并且侧边栏里排在最前面。
 *
 * 为什么要有这个测试：这是个纯前端、纯「加载顺序」的 bug —— 后端和接口全都正常，
 * 页面上什么都不报，只是停在错的页签。没有浏览器就没法测，所以这里用 stub DOM 把
 * index.html 里的**两段真实内联脚本**按浏览器的顺序跑一遍（不是抄一份代码：
 * 抄一份的话测试绿了、页面照样错）。
 *
 * 背景（改之前的实测结果）：
 *   第一段 <script> 末尾写着 switchTab('dashboard')，而 switchTab 定义在第二段 <script> 里。
 *   跨 script 不提升 → ReferenceError: switchTab is not defined → 首页一直停在标记里
 *   唯一没有 hidden 的 tab-generate（生产中心）。「已登录直接进看板」从来没生效过。
 *
 * 覆盖：
 *   1. 两段脚本都不抛错（这条直接抓上面那个 ReferenceError，改前必红）
 *   2. 启动后**只有** tab-dashboard 可见（stub 的 hidden 初值从真实标记读，所以
 *      同时验了「标记默认态」和「switchTab 的结果」两条路）
 *   3. 有且只有 nav-dashboard 带活跃样式（classList 也从真实标记读）
 *   4. 标记里 nav-dashboard 出现在 nav-generate 之前（侧边栏顺序）
 *   5. 未登录时不抛错（这时落地块跳过，页面靠标记默认态显示看板）
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// ---- 从真实标记里读出每个 id 元素的初始 class / hidden ----
const initial = {};
for (const m of SRC.matchAll(/<[a-zA-Z][^>]*\bid="([^"]+)"[^>]*>/g)) {
  const tag = m[0], id = m[1];
  const cls = (/\bclass="([^"]*)"/.exec(tag) || [, ''])[1];
  const hidden = /\shidden(?=[\s>])/.test(tag);
  initial[id] = { cls, hidden };   // 同 id 出现多次时以最后一个为准（页面上的那个）
}

function mkEl(id) {
  const init = initial[id] || { cls: '', hidden: false };
  const el = {
    id, hidden: init.hidden, textContent: '', innerHTML: '', value: '', checked: false,
    style: {}, dataset: {}, disabled: false, tagName: 'DIV', children: [],
    parentElement: null, firstChild: null, className: init.cls, title: '',
    classList: {
      _s: new Set(init.cls.split(/\s+/).filter(Boolean)),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
    },
    addEventListener() {}, removeEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    insertAdjacentHTML() {}, remove() {}, setAttribute() {}, getAttribute() { return null; },
    querySelector: () => mkEl(id + ':q'), querySelectorAll: () => arr([]),
    focus() {}, click() {}, closest() { return null; },
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }),
    scrollTo() {}, insertBefore() {}, cloneNode: () => mkEl(id + ':clone'),
  };
  return el;
}
const els = {};
const getEl = id => (els[id] = els[id] || mkEl(id));
const arr = a => { a.forEach = Array.prototype.forEach.bind(a); return a; };

const blocks = [...SRC.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

function runStartup(loggedIn) {
  const store = {};
  if (loggedIn) {
    store['auth_token'] = 'TOK';
    store['current_user'] = JSON.stringify({ id: 1, role: 'admin', username: 'admin' });
  }
  Object.keys(els).forEach(k => delete els[k]);   // 每次跑都从干净的 DOM 开始

  const thenable = { then: () => thenable, catch: () => thenable, finally: () => thenable };
  const ctx = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      getElementById: getEl,
      querySelector: () => mkEl('q'),
      querySelectorAll: () => arr([]),
      createElement: () => mkEl('new'),
      body: mkEl('body'), documentElement: mkEl('html'),
      addEventListener() {}, removeEventListener() {},
    },
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
    },
    location: { href: 'http://x/', search: '', reload() {} },
    fetch: () => thenable,
    EventSource: function () { return { close() {}, addEventListener() {} }; },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    alert() {}, confirm: () => true, prompt: () => null,
    Chart: function () { return { destroy() {} }; },
    URL, Date, JSON, Math, String, Number, Array, Object, Boolean, RegExp, Error,
    parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
    btoa: s => s, atob: s => s, FormData: function () {},
    navigator: { userAgent: 'node' }, requestAnimationFrame: () => 0,
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  ['HTMLButtonElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLTextAreaElement', 'HTMLElement',
   'Element', 'Node', 'Event', 'CustomEvent', 'MutationObserver', 'IntersectionObserver', 'ResizeObserver',
   'Image', 'Audio', 'FileReader', 'Blob', 'Headers', 'Request', 'Response', 'AbortController',
   'SVGElement', 'HTMLDivElement', 'HTMLTableElement', 'HTMLFormElement', 'XMLHttpRequest', 'WebSocket']
    .forEach(n => {
      ctx[n] = function () { return mkEl(n); };
      const proto = {};
      ['disabled', 'value', 'checked', 'title', 'className', 'innerHTML', 'textContent'].forEach(prop => {
        Object.defineProperty(proto, prop, {
          configurable: true,
          get() { return this['__' + prop]; },
          set(v) { this['__' + prop] = v; },
        });
      });
      ctx[n].prototype = proto;
    });
  ctx.window = ctx; ctx.globalThis = ctx; ctx.self = ctx;
  ctx.addEventListener = () => {}; ctx.removeEventListener = () => {};

  vm.createContext(ctx);
  const errors = [];
  blocks.forEach((code, i) => {
    try {
      new vm.Script(code, { filename: `index.html#script${i + 1}` }).runInContext(ctx);
    } catch (e) {
      errors.push(`script${i + 1}: ${e.name}: ${e.message}`);
    }
  });
  const visibleTabs = Object.keys(els).filter(k => /^tab-/.test(k) && !els[k].hidden);
  const activeNavs = Object.keys(els).filter(k => /^nav-/.test(k) && els[k].classList.contains('text-white'));
  return { errors, visibleTabs, activeNavs };
}

// ---- 1~4) 已登录（常规启动） ----
const on = runStartup(true);
assert(on.errors.length === 0,
  '启动时脚本抛错了（首页落地逻辑又写到了 switchTab 定义之前？）：' + on.errors.join(' / '));
assert(on.visibleTabs.join(',') === 'tab-dashboard',
  `启动后可见的页签应该是且只有 tab-dashboard，实际：[${on.visibleTabs.join(', ')}]`
  + `（空 = 落地逻辑压根没跑；出现 tab-generate = 落地到了错的页签）`);
assert(on.activeNavs.join(',') === 'nav-dashboard',
  `侧边栏应该只有「数据看板」高亮，实际：[${on.activeNavs.join(', ')}]`);

// ---- 5) 未登录：不抛错（落地块跳过，靠标记默认态） ----
const off = runStartup(false);
assert(off.errors.length === 0, '未登录启动时脚本抛错：' + off.errors.join(' / '));

// ---- 4) 侧边栏顺序：数据看板在最前 ----
const iDash = SRC.indexOf('id="nav-dashboard"');
const iGen = SRC.indexOf('id="nav-generate"');
assert(iDash >= 0 && iGen >= 0, '侧边栏里找不到 nav-dashboard / nav-generate');
assert(iDash < iGen, '「数据看板」没排在「生产中心」前面');

// ---- 静态回归：落地页逻辑必须在第二段 <script> 里 ----
// 注意**不能**直接断言「第一段里没有 switchTab(」：第一段里本来就有几处
// `addEventListener('click', function(){ switchTab('users') })` —— 那些跑在点击时，
// 那时两段脚本都加载完了，完全合法。真正要防的是「第一段**求值时**调用 switchTab」，
// 那个由上面第 1 条断言（script1 不抛错）行为性地抓住了 —— 一旦挪回去就必红。
assert(/switchTab\('dashboard'\)/.test(blocks[1] || ''),
  '第二段 <script> 里找不到 switchTab(\'dashboard\')，首页落地逻辑丢了');

// ---- 静态回归：标记的默认态必须是「看板可见、生产中心隐藏」----
// 这条只有静态查得出来：switchTab 跑完会把两条路收敛成同一个状态，所以上面那些行为断言
// 看不出标记默认态。它的作用是**JS 起来之前那一瞬间**显示谁 —— 反了就会先闪一下生产中心。
const tabGenTag = /<div[^>]*id="tab-generate"[^>]*>/.exec(SRC)[0];
const tabDashTag = /<div[^>]*id="tab-dashboard"[^>]*>/.exec(SRC)[0];
assert(/\shidden/.test(tabGenTag), 'tab-generate 的标记默认态没隐藏 —— 刷新时会先闪一下生产中心');
assert(!/\shidden/.test(tabDashTag), 'tab-dashboard 的标记默认态被隐藏了 —— JS 起来之前那个页签是空的');

console.log('OK: 数据看板是首页（两段脚本零报错 / 启动只显示 tab-dashboard / '
          + '只有 nav-dashboard 高亮 / 侧边栏排在生产中心之前 / 未登录也不抛错）');
