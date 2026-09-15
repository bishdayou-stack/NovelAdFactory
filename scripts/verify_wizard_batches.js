/*
 * 验证：投放向导第 2 步「选素材」的素材池按**批次**展示。
 *
 * 背景：以前 dwLoadFromAssets 把所有批次的素材拉平成一坨，64 张一页翻到底 ——
 * 界面上看不出哪张图是哪本书、哪个批次的。
 *
 * 这里只测纯逻辑（分组 / 下标 / 分页），不测 DOM 渲染 —— 所以用正则从 index.html 里
 * 抠出那几个函数，喂桩数据跑。**抠的是 index.html 里的真代码，不是抄一份**：
 * 抄一份的话测试绿了、页面照样错。
 *
 * 覆盖：
 *   1. 素材按 URL 里的批次目录分组；同一批次的素材必须落在同一组
 *   2. _dwAssets 保持一维且顺序不变 —— _dwSelected 存的就是它里面的下标，
 *      重排会让「已选」错位到别的素材上（这条最要命：发布时会投错图）
 *   3. 每组的 indices 并起来正好是全部下标：一个不丢、一个不重
 *   4. 书籍ID：素材自带就用自带的；没有就回查 allAssetsData
 *   5. 分页是按批次翻：7 个批次 / 每页 6 → 第一页 6 组、第二页 1 组；页码越界夹回来
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

function grab(name) {
  // 取 "function name(...) { ... }" 整块（按行首缩进找配对的结尾大括号）
  const start = SRC.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('index.html 里找不到函数 ' + name);
  let depth = 0, i = SRC.indexOf('{', start);
  for (let j = i; j < SRC.length; j++) {
    if (SRC[j] === '{') depth++;
    else if (SRC[j] === '}') { depth--; if (depth === 0) return SRC.slice(start, j + 1); }
  }
  throw new Error('函数 ' + name + ' 的大括号不配对');
}

const NAMES = ['dwBatchOfUrl', 'dwNovelIdOfBatch', 'dwSetAssetPool', 'dwBatchSlice'];
const code = NAMES.map(grab).join('\n');

// 这些是被抠出来的代码依赖的全局量，喂桩
let _dwAssets = [], _dwBatches = [], _dwSelected = new Set(), _dwAssetPage = 1;
let _dwBatchPageSize = 6, _dwNovelIdCache = {}, allAssetsData = [];

const ctx = {
  get _dwAssets() { return _dwAssets; }, set _dwAssets(v) { _dwAssets = v; },
  get _dwBatches() { return _dwBatches; }, set _dwBatches(v) { _dwBatches = v; },
  get _dwSelected() { return _dwSelected; }, set _dwSelected(v) { _dwSelected = v; },
  get _dwAssetPage() { return _dwAssetPage; }, set _dwAssetPage(v) { _dwAssetPage = v; },
  get _dwBatchPageSize() { return _dwBatchPageSize; }, set _dwBatchPageSize(v) { _dwBatchPageSize = v; },
  get _dwNovelIdCache() { return _dwNovelIdCache; }, set _dwNovelIdCache(v) { _dwNovelIdCache = v; },
  get allAssetsData() { return allAssetsData; }, set allAssetsData(v) { allAssetsData = v; },
};
const fns = new Function(
  'ctx', 'fetch',
  `with (ctx) { ${code}
     return { dwBatchOfUrl, dwNovelIdOfBatch, dwSetAssetPool, dwBatchSlice,
              get batches() { return _dwBatches; },
              get assets() { return _dwAssets; },
              get page() { return _dwAssetPage; } }; }`
)(ctx, () => { const t = { then: () => t, catch: () => t }; return t; });

function assert(cond, msg) {
  if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// 1 + 2 + 3) 分组、下标稳定、不丢不重
const raw = [
  { image_url: '/static/output/7/a.png', image_type: 'text_single' },
  { image_url: '/static/output/9/b.png', image_type: 'text_single', batch_id: '9', novel_id: 'B-9' },
  { image_url: '/static/output/7/c.mp4', image_type: 'scroll' },
  { image_url: '/static/output/9/d.mp4', image_type: 'scroll', batch_id: '9', novel_id: 'B-9' },
  { image_url: '/static/output/7/e.png', image_type: 'text_single' },
];
fns.dwSetAssetPool(raw);
assert(fns.assets.length === 5 && fns.assets === raw, '_dwAssets 被重排/复制了，下标会错位');
assert(fns.batches.length === 2, `应分成 2 个批次，实际 ${fns.batches.length}`);
assert(fns.batches.map(g => g.batch_id).join(',') === '7,9', '批次顺序不对：' + fns.batches.map(g => g.batch_id));
assert(JSON.stringify(fns.batches[0].indices) === '[0,2,4]', '批次 7 的下标不对：' + fns.batches[0].indices);
assert(JSON.stringify(fns.batches[1].indices) === '[1,3]', '批次 9 的下标不对：' + fns.batches[1].indices);
const flat = fns.batches.reduce((a, g) => a.concat(g.indices), []).sort((x, y) => x - y);
assert(JSON.stringify(flat) === '[0,1,2,3,4]', '分组后下标不是全集的排列（丢了/重了）：' + flat);
assert(fns.batches[0].indices.every(i => fns.assets[i].image_url.indexOf('/7/') >= 0),
       '同一批次的素材被分到了别的组');

// 4) 书籍ID：自带优先；没有的批次回查 allAssetsData
const noNovel = [
  { image_url: '/static/output/11/x.png', image_type: 'text_single' },
  { image_url: '/static/output/12/y.png', image_type: 'text_single' },
];
allAssetsData = [{ batch_id: '12', novel_id: 'BOOK-12' }];
fns.dwSetAssetPool(noNovel);
assert(fns.batches[0].novel_id === '', '批次 11 不该凭空有书籍ID');
assert(fns.batches[1].novel_id === 'BOOK-12', '批次 12 没从素材列表回查到书籍ID：' + fns.batches[1].novel_id);
assert(fns.batches[1].batch_id === '12', '批次号应从 URL 里抠出来');

// 5) 分页按批次翻
allAssetsData = [];
const many = [];
for (let b = 1; b <= 7; b++) many.push({ image_url: '/static/output/' + b + '/p.png', image_type: 'text_single' });
fns.dwSetAssetPool(many);
assert(fns.batches.length === 7, '应有 7 个批次');
assert(fns.page === 1, '换池子后应回到第 1 页');
let s1 = fns.dwBatchSlice(1);
assert(s1.pages === 2 && s1.groups.length === 6, `第 1 页应是 2 页中的 6 组，实际 ${s1.groups.length}/${s1.pages}`);
let s2 = fns.dwBatchSlice(2);
assert(s2.groups.length === 1 && s2.groups[0].batch_id === '7', '第 2 页应是剩下那 1 个批次');
assert(fns.dwBatchSlice(99).page === 2, '页码越界没夹回来');
assert(fns.dwBatchSlice(0).page === 1 && fns.dwBatchSlice(-3).page === 1, '页码 <1 没夹回来');

console.log('OK: 投放向导素材池按批次展示（分组正确 / 一维下标不变动 / 不丢不重 / '
          + '书籍ID 自带优先并回查 / 分页按批次翻且越界夹回）');
