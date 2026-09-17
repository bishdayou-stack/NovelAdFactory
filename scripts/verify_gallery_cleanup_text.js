/*
 * 验证：素材画廊「清理」确认框的文案 —— 图片和视频必须**分开**报数。
 *
 * 为什么要测这个：这里的全部价值就是「把话说准」。原来的确认框写的是
 *     「将删除 N 张缓存缩略图，释放约 X MB。……只删图片文件。」
 * 全是错的：N 里混着视频个数，而且视频（含用户在画廊手动「缓存到本地」的那些）
 * 会被一起删掉 —— 实测那 30 个 mp4 就占了 173MB，是清理的大头。用户看着
 * 「只删图片文件」点下去，本地缓存好的视频就没了。
 *
 * 所以这里抠出 index.html 里的**真函数** galCleanupParts 喂桩数据跑，
 * 而不是断言「文件里有没有某个字符串」——那种断言抓不住改坏的拼接逻辑。
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

const parts = new Function(`with ({}) { ${grab('galCleanupParts')} return galCleanupParts; }`)();

// 1) 真实场景：1742 张图 / 30 个视频（照线上实测的量级）
let p = parts({ image_files: 1742, image_mb: 204.3, video_files: 30, video_mb: 173.1 });
assert(p.length === 2, `图片和视频应该各占一行，实际 ${p.length} 行：${JSON.stringify(p)}`);
assert(/图片 1742 张/.test(p[0]) && /204\.3 MB/.test(p[0]),
  `图片那行没说清张数和 MB：${p[0]}`);
assert(/视频 30 个/.test(p[1]) && /173\.1 MB/.test(p[1]),
  `视频那行没说清个数和 MB：${p[1]}`);
assert(p[0].indexOf('图片') === 0 && p[1].indexOf('视频') === 0, `顺序应该是先图片后视频：${p}`);

// 2) 只有图片 / 只有视频时不该冒出「视频 0 个」这种废话
p = parts({ image_files: 5, image_mb: 1.2, video_files: 0, video_mb: 0 });
assert(p.length === 1 && /图片 5 张/.test(p[0]), `只有图片时应只报一行：${JSON.stringify(p)}`);
p = parts({ image_files: 0, image_mb: 0, video_files: 2, video_mb: 9 });
assert(p.length === 1 && /视频 2 个/.test(p[0]), `只有视频时应只报一行：${JSON.stringify(p)}`);

// 3) 类型分开报，不能合成一个数（这正是要修的东西）
p = parts({ image_files: 1742, image_mb: 204.3, video_files: 30, video_mb: 173.1 });
assert(!/(合计|共)\s*1772/.test(p.join(' ')),
  `不许把图片和视频合成「N 个文件」，用户看不出视频被删了：${JSON.stringify(p)}`);

// 4) 后端没给这几个字段时不能抛错（老后端 + 新前端的组合）
assert(parts({}).length === 0, '字段缺失时应返回空数组而不是抛错');
assert(parts({ deleted_files: 3, freed_mb: 0.4 }).length === 0,
  '只有旧的合计字段时不该硬编出「undefined 张」');

// 5) 真实调用点：两处都必须走 galCleanupParts，不能退回旧的合计数写法。
//    前面的 (?<!function ) 是为了排掉函数自己的定义行 `function galCleanupParts(d) {`
const callers = SRC.match(/(?<!function )galCleanupParts\((d|res)\)/g) || [];
assert(callers.length === 2,
  `确认框和结果提示都该用 galCleanupParts(d)/galCleanupParts(res)，实际 ${callers}`);

// 6) 原来那句错的说明必须消失（视频会被一起删，不是「只删图片文件」）。
//    只查「清理」这一段代码、并且剥掉 // 注释 —— 否则解释这个 bug 的注释里
//    就带着这句话，断言会因为一句注释而误报。
const from = SRC.indexOf('function galCleanupParts');
const to = SRC.indexOf('function loadGalleryPage');
assert(from > 0 && to > from, 'index.html 里找不到画廊清理那一段代码');
const region = SRC.slice(from, to).split('\n')
  .map(l => l.replace(/\/\/.*$/, '')).join('\n');
assert(region.indexOf('只删图片文件') < 0,
  '确认框里还留着「只删图片文件」—— 视频缓存会被一起删，这句话是错的');

console.log('OK: 画廊清理文案（图片/视频分开报张数与 MB、单类型时不报 0、'
  + '不合成合计数、缺字段不抛错、确认与结果两处都走同一函数、'
  + '「只删图片文件」这句错误说明已清掉）');
