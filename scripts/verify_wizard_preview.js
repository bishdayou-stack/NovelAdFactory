/*
 * 验证：投放向导第 4 步预览「每个系列分到哪几个素材」。
 *
 * 为什么要有：用户选 9 个素材（3 个视频）想让每个系列分到一个视频，而素材是按**块**
 * 分配的（前 n2×n3 个给系列1，再给系列2…）。发布前**唯一**能发现「视频全挤在一个系列」
 * 的机会就是这个预览 —— 预览错了等于没有。
 *
 * 所以这里抠出 index.html 里的**真函数**（不是抄一份）喂桩数据跑，并且用和
 * scripts/verify_delivery_assets.py **同一组例子**（点选顺序 6,0,1,7,2,3,8,4,5）钉死，
 * 两边必须给出同一个分配 —— 前端显示的和实际投出去的对不上是最坏的情况。
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

// ---- 桩 ----
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: '1', innerHTML: '', textContent: '' };
  return els[id];
}
let _dwAssets = [], _dwSelected = new Set();

const ctx = {
  document: { getElementById: el, querySelector: () => null, querySelectorAll: () => [] },
  absUrl: u => 'http://x' + u,
  get _dwAssets() { return _dwAssets; }, set _dwAssets(v) { _dwAssets = v; },
  get _dwSelected() { return _dwSelected; }, set _dwSelected(v) { _dwSelected = v; },
};

const fns = new Function('ctx', `with (ctx) {
  ${grab('dwThumbHtml')}
  ${grab('dwRenderPreview')}
  return { dwRenderPreview, dwThumbHtml };
}`)(ctx);

// ---- 例子：9 个素材，3 个视频（下标 6/7/8），按「视频,图,图,视频,图,图,视频,图,图」点选 ----
// 和 scripts/verify_delivery_assets.py 里的 CLICK_ORDER 必须一致
const CLICK_ORDER = [6, 0, 1, 7, 2, 3, 8, 4, 5];
const VIDEO_IDX = new Set([6, 7, 8]);

_dwAssets = [];
for (let i = 0; i < 9; i++) {
  _dwAssets.push({ image_url: '/static/output/1/a' + i + (VIDEO_IDX.has(i) ? '.mp4' : '.png'),
                   image_type: VIDEO_IDX.has(i) ? 'scroll' : 'text_single', overlay_text: '' });
}
// Set 的迭代顺序 = 插入顺序 = 点选顺序
_dwSelected = new Set(CLICK_ORDER);

el('dwN1').value = '3';
el('dwN2').value = '1';
el('dwN3').value = '3';
fns.dwRenderPreview();
const html = el('dwPreview').innerHTML;

// 1) 三组缩略图，按点选顺序切块
const srcs = [...html.matchAll(/<img[^>]*src="([^"]*)"/g)].map(m => m[1]);
assert(srcs.length === 9, `预览里应该有 9 张缩略图，实际 ${srcs.length} 张`);

function assetOf(src) {
  const m = /a(\d+)(_thumb\.jpg|\.mp4|$)/.exec(src);
  return m ? Number(m[1]) : null;
}
const shown = srcs.map(assetOf);
assert(shown.every(x => x !== null), '有缩略图的地址认不出对应素材：' + JSON.stringify(srcs));
const per = 3;
for (let g = 0; g < 3; g++) {
  const want = CLICK_ORDER.slice(g * per, (g + 1) * per);
  const got = shown.slice(g * per, (g + 1) * per);
  assert(got.join(',') === want.join(','),
    `系列${g + 1} 预览显示的顺序是 [${got}]，按点选顺序应该是 [${want}]`);
}

// 2) 每组各自的「视频 N 个」计数 —— 用户最关心的就是这个数。
//    只认分组标题里那个 span（末尾提示语里也有「系列 1」这样的字样，不能用 split('系列 ')）
const groups2 = [...html.matchAll(/text-slate-200">系列 (\d+)<\/span>[\s\S]*?视频 (\d+) 个/g)];
assert(groups2.map(m => m[1]).join(',') === '1,2,3',
  `预览里应依次列出系列 1/2/3，实际 [${groups2.map(m => m[1])}]`);
const videoCounts = groups2.map(m => Number(m[2]));
assert(videoCounts.join(',') === '1,1,1',
  `每个系列应该各显示「视频 1 个」，实际 [${videoCounts}] ` +
  `（这个数字不对，用户就会误判，投出去才发现视频全挤在一个系列）`);

// 3) 视频缩略图走封面接口（.mp4 不能直接当 img src）
const videoThumbs = srcs.filter(s => s.indexOf('video-poster') >= 0);
assert(videoThumbs.length === 3, `视频缩略图应该走 video-poster，实际 ${videoThumbs.length} 张`);

// 4) 素材数不对时给警告，别让用户以为能发
_dwSelected = new Set(CLICK_ORDER.slice(0, 8));
fns.dwRenderPreview();
const warn = el('dwPreview').innerHTML;
assert(warn.indexOf('素材数不对') >= 0 && warn.indexOf('还差 1') >= 0,
  '素材没选满时预览没给出明确警告');
assert(warn.indexOf('系列 1') < 0, '素材没选满时不该还画分配表（会误导）');

// 5) 换了 (系列,组,广告) 组合，分块要跟着变
_dwSelected = new Set([8, 7, 6, 5]);       // 倒着点：视频在最后两个
el('dwN1').value = '2'; el('dwN2').value = '1'; el('dwN3').value = '2';
fns.dwRenderPreview();
const shown2 = [...el('dwPreview').innerHTML.matchAll(/<img[^>]*src="([^"]*)"/g)].map(m => assetOf(m[1]));
assert(shown2.join(',') === '8,7,6,5',
  `2 系列 × 2 广告时应按 [8,7][6,5] 分块，实际 [${shown2}]`);

console.log('OK: 投放向导第 4 步预览（按点选顺序分块、每组视频计数正确、'
  + '视频缩略图走封面接口、素材不足给警告、换组合分块跟着变；'
  + '与 verify_delivery_assets.py 用同一组例子，前后端分配一致）');
