/*
 * 验证：广告系列页的状态筛选（默认「投放中」+ 全被筛掉时的提示）。
 *
 * 两个容易出错的地方：
 *   1. 默认值写在**两个地方** —— 下拉框的 <option selected> 和 JS 变量
 *      _campStatusFilter。只改一个就会出现「框里显示投放中、实际筛的是全部」，
 *      而且不会有任何报错。
 *   2. 默认筛「投放中」之后，一个系列都没匹配上时页面会一片空白，
 *      看起来像系列没建成功 —— 必须有句话说明是被筛掉的。
 *
 * 所以这里抠出 index.html 里的**真函数** applyCampStatusFilter 喂桩 DOM 跑，
 * 不是断言文件里有没有某个字符串。
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

// ---- 桩 DOM ----
function mkRow(eff) { return { dataset: { effStatus: eff }, style: { display: '' } }; }

let rows = [], tip = null, appended = [];
const table = {
  querySelectorAll: () => rows,
  appendChild: n => { appended.push(n); tip = n; },
};
const select = { selectedIndex: 1, options: [{ text: '全部' }, { text: '投放中' }] };

const ctx = {
  document: {
    getElementById: id => {
      if (id === 'campPageTable') return table;
      if (id === 'campStatusSelect') return select;
      if (id === 'campFilterEmpty') return tip;
      return null;
    },
    createElement: () => ({ id: '', className: '', textContent: '', remove() { tip = null; } }),
  },
};

const dm = /var _campStatusFilter = '([^']*)';/.exec(SRC);
assert(dm, 'index.html 里找不到 var _campStatusFilter 的初值');

const fn = new Function('ctx', `with (ctx) {
  var _campStatusFilter = ${JSON.stringify(dm[1])};
  ${grab('applyCampStatusFilter')}
  return { applyCampStatusFilter, getFilter: function() { return _campStatusFilter; } };
}`)(ctx);

// ---- 1) 默认值：JS 变量要和下拉框里 selected 的那一项一致 ----
// 只看「状态」这个下拉框里的内容：整个文件别处还有带 selected 的 option（每页条数）
const selStart = SRC.indexOf('id="campStatusSelect"');
assert(selStart > 0, 'index.html 里找不到状态筛选下拉框 campStatusSelect');
const selHtml = SRC.slice(selStart, SRC.indexOf('</select>', selStart));
const m = /<option value="([^"]*)"\s+selected>([^<]*)<\/option>/.exec(selHtml);
assert(m, '状态下拉框里找不到带 selected 的默认项');
assert(fn.getFilter() === m[1],
  `默认筛选两处对不上：HTML 选中的是「${m[2]}」(${m[1]})，JS 变量是 '${fn.getFilter()}'`);
assert(m[2] === '投放中', `默认项应该是「投放中」，实际是「${m[2]}」`);

// ---- 2) 有匹配：正常显示，不留提示 ----
tip = null; appended = [];
rows = [mkRow('ACTIVE'), mkRow('PAUSED'), mkRow('ACTIVE')];
fn.applyCampStatusFilter();
assert(rows[0].style.display === '' && rows[2].style.display === '', '投放中的该显示');
assert(rows[1].style.display === 'none', '已暂停的该被筛掉');
assert(!appended.length, '有匹配时不该出现提示');

// ---- 3) 全被筛掉：要出提示，并说清是多少个被筛掉 ----
tip = null; appended = [];
rows = [mkRow('PAUSED'), mkRow('CAMPAIGN_PAUSED'), mkRow('ARCHIVED')];
fn.applyCampStatusFilter();
assert(appended.length === 1, '全被筛掉时必须给一句提示（否则页面看着像坏了）');
const t = appended[0];
assert(t.id === 'campFilterEmpty', `提示的 id 不对：${t.id}`);
assert(t.textContent.indexOf('投放中') >= 0 && t.textContent.indexOf('3') >= 0,
  `提示要说清当前筛选和筛掉的个数：${t.textContent}`);
assert(t.textContent.indexOf('全部') >= 0, `提示要告诉用户怎么找回来：${t.textContent}`);

// ---- 4) 空表（真的一个系列都没有）：不该冒出「3 个系列被筛掉」这种瞎话 ----
tip = null; appended = [];
rows = [];
fn.applyCampStatusFilter();
assert(!appended.length, '列表本来就是空的时不该提示「被筛掉 N 个」');

// ---- 5) 重新渲染前要先清掉上一轮的提示，不能越堆越多 ----
let prevRemoved = false;
tip = { id: 'campFilterEmpty', remove() { prevRemoved = true; tip = null; } };
appended = [];
rows = [mkRow('PAUSED')];
fn.applyCampStatusFilter();
assert(prevRemoved, '上一轮的提示没被清掉（每次重画都会再挂一条，越堆越多）');
assert(appended.length === 1, `这一轮应该只挂一条提示，实际 ${appended.length} 条`);

console.log('OK: 广告系列状态筛选（默认值与下拉框一致且为「投放中」/ 有匹配不留提示 / '
  + '全筛掉时给出筛选名与个数并提示切「全部」/ 空表不误报 / 不重复堆提示）');
