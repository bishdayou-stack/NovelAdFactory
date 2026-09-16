/*
 * 验证：投放向导排期（开始/结束时间）的前端行为。
 *
 * 为什么要单独测前端这一小块：**时区**。datetime-local 的值是「本机本地时间」的裸字符串，
 * 必须用 new Date(...)（按本地时区解释）+ toISOString()（转 UTC）再发；直接发裸串的话，
 * 后端跑在 UTC 服务器上，8 小时的偏差就出来了，而且**哪儿都不会报错**。
 *
 * 原来的 Python 测试里我用 `"toISOString()" in html` 来盯这件事 —— 那是**无效断言**：
 * index.html 里另外还有 4 处 toISOString（别的日期逻辑），把 dwLocalToIso 改成返回裸串，
 * 那条断言照样绿。所以这里抠出真函数**真跑一遍**。
 *
 * 覆盖：
 *   1. dwLocalToIso：本地时间 → 带 Z 的 UTC；空 → ''；非法 → ''
 *   2. dwScheduleIssues：未来+关闭必须警告（否则排了期也不会投）；开了就不警告；
 *      开始时间已过去要提示；结束早于开始要报错
 *   3. dwScheduleText：空 = 立即开始·长期投放；有值 = 起止文案
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

function grab(name) {
  const start = SRC.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('index.html 里找不到函数 ' + name);
  let depth = 0;
  for (let j = SRC.indexOf('{', start); j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}') { depth--; if (depth === 0) return SRC.slice(start, j + 1); }
  }
  throw new Error('函数 ' + name + ' 的大括号不配对');
}

function assert(cond, msg) {
  if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const inputs = {};
function el(id) {
  if (!inputs[id]) inputs[id] = { id, value: '' };
  return inputs[id];
}
let statusValue = 'PAUSED';
const ctx = {
  document: {
    getElementById: el,
    querySelector: sel => (sel.indexOf('dwStatus') >= 0 ? { value: statusValue } : null),
    querySelectorAll: () => [],
  },
};

const fns = new Function('ctx', `with (ctx) {
  ${grab('dwLocalToIso')}
  ${grab('dwScheduleIssues')}
  ${grab('dwScheduleText')}
  return { dwLocalToIso, dwScheduleIssues, dwScheduleText };
}`)(ctx);

// ---- 1) 本地时间 → UTC ----
const V = '2026-09-20T08:00';
const iso = fns.dwLocalToIso(V);
assert(/Z$/.test(iso), `dwLocalToIso 没转成 UTC（应该以 Z 结尾）：${iso}`);
assert(iso !== V, 'dwLocalToIso 把裸的本地时间原样返回了 —— 后端在 UTC 服务器上，会差一个时区');
assert(iso === new Date(V).toISOString(),
  `dwLocalToIso 的换算方式不对：拿到 ${iso}，按「本地时间」解释应当是 ${new Date(V).toISOString()}`);
assert(fns.dwLocalToIso('') === '', '空值应该返回空串（= 不排期）');
assert(fns.dwLocalToIso('乱写的') === '', '非法输入应该返回空串而不是抛错');

// ---- 2) 排期的问题判断 ----
function set(start, end, status) {
  el('dwStartTime').value = start;
  el('dwEndTime').value = end;
  statusValue = status;
}
const FUTURE = '2099-09-20T08:00';
const PAST = '2000-09-20T08:00';

set(FUTURE, '', 'PAUSED');
let iss = fns.dwScheduleIssues();
assert(iss.join('|').indexOf('不会开始投放') >= 0,
  `「排期在未来 + 状态关闭」没给出警告（这是最容易踩的坑：Meta 不报错，只是永远不投）：${iss}`);

set(FUTURE, '', 'ACTIVE');
iss = fns.dwScheduleIssues();
assert(iss.length === 0, `排期在未来 + 状态开启不该有警告：${iss}`);

set(PAST, '', 'PAUSED');
iss = fns.dwScheduleIssues();
assert(iss.join('|').indexOf('立即开始') >= 0, `开始时间已过去应当提示会立即开始：${iss}`);

set(FUTURE, PAST, 'ACTIVE');
iss = fns.dwScheduleIssues();
assert(iss.join('|').indexOf('晚于开始时间') >= 0, `结束早于开始应当报错：${iss}`);

set('', '', 'PAUSED');
iss = fns.dwScheduleIssues();
assert(iss.length === 0, `完全不排期时不该有任何提示：${iss}`);

// ---- 3) 排期文案 ----
set('', '', 'PAUSED');
assert(fns.dwScheduleText().indexOf('立即开始') >= 0 && fns.dwScheduleText().indexOf('长期投放') >= 0,
  `不排期的文案不对：${fns.dwScheduleText()}`);
set('2026-09-20T08:00', '2026-09-30T23:00', 'ACTIVE');
const t = fns.dwScheduleText();
assert(t.indexOf('2026-09-20 08:00') >= 0 && t.indexOf('2026-09-30 23:00') >= 0,
  `排期文案没带上起止时间：${t}`);

console.log('OK: 投放向导排期前端（本地时间转 UTC 不丢时区 / 排期+关闭必须警告 / '
  + '状态开启不误报 / 开始时间已过去有提示 / 结束早于开始报错 / 文案正确）');
