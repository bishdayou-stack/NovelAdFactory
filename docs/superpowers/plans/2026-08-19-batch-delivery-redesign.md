# 批量投放流程重设计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把投放向导从「三步手动」改成「选账户 → 填数量 → 选素材 → 三层各一套设置 → 一键批量 N1×N2×N3 个广告」，全部 PAUSED 创建，每广告一素材、标题随机、广告名统一。

**Architecture:** 后端新增 `POST /api/delivery/batch-publish` 接口（校验 + 批量落库 + 触发投放），投放循环在 `delivery.py` 新增 `submit_delivery_batch` 函数里做；前端投放向导重写为 4 步。复用现有 `delivery_campaigns` / `delivery_adsets` / `delivery_queue` 三张表，不新增表。

**Tech Stack:** FastAPI + 原生 JS/Tailwind + SQLite（无 ORM、无测试框架）。

**Spec:** [docs/superpowers/specs/2026-08-19-batch-delivery-redesign-design.md](../specs/2026-08-19-batch-delivery-redesign-design.md)

## Global Constraints

- 所有创建的 campaign/adset/ad 均为 `status="PAUSED"`
- `objective` 固定 `OUTCOME_SALES`，`billing_event` 固定 `IMPRESSIONS`，`destination_type` 固定 `WEBSITE`
- 数量模型：系列数 N1 × 每系列广告组数 N2 × 每广告组广告数 N3 = 总广告数，必须 == 素材数
- ABO 模式（`is_adset_budget_sharing_enabled=0`）广告组日预算必须 > 0
- `optimization_goal=OFFSITE_CONVERSIONS` 时必须选 Pixel（`pixel_id` 非空）
- 广告名：一个字符串，所有广告同名；广告标题：5-10 个，每个广告随机选一个；CTA：一个，所有广告统一
- 素材按选择顺序对应广告序号（第 idx 个素材 → 第 (i,j,k) 个广告）
- 验证方式：`python -m py_compile` + `python -c` 直接调函数 + `curl` 打 API + 前端手动走流程（无 pytest）

---

## 文件结构

| 文件 | 改动 | 职责 |
|---|---|---|
| `meta_api.py` | Modify | `create_ad` 加 `headline` 参数 |
| `delivery.py` | Modify | 新增 `submit_delivery_batch`：落库 + 循环投放 |
| `main.py` | Modify | 新增 `POST /api/delivery/batch-publish` 接口 + 校验 |
| `static/index.html` | Modify | 投放向导 UI 重写为 4 步 |

---

### Task 1: `meta_api.create_ad` 支持 headline

**Files:**
- Modify: `meta_api.py:415-454`（`create_ad` 函数）

**Interfaces:**
- Consumes: 无（现有函数）
- Produces: `create_ad(act_id, access_token, name, adset_id, creative_name, page_id, image_hash=None, video_id=None, message="", link_url="", call_to_action_type="LEARN_MORE", headline="", status="PAUSED") -> Tuple[Optional[str], Optional[str]]` —— 新增 `headline` 参数，写入 `object_story_spec.link_data.name`

- [ ] **Step 1: 修改 `create_ad` 函数签名，加 `headline` 参数**

在 [meta_api.py:415-421](meta_api.py#L415-L421) 的签名处加 `headline: str = ""`：

```python
def create_ad(act_id: str, access_token: str,
              name: str, adset_id: str,
              creative_name: str, page_id: str,
              image_hash: str = None, video_id: str = None,
              message: str = "", link_url: str = "",
              call_to_action_type: str = "LEARN_MORE",
              headline: str = "",
              status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
```

- [ ] **Step 2: 在 `object_story_spec["link_data"]` 里写入 headline**

在 [meta_api.py:424-430](meta_api.py#L424-L430) 的 `object_story_spec` 构造后，加 headline 写入：

```python
    if image_hash:
        object_story_spec["link_data"]["image_hash"] = image_hash
    if video_id:
        object_story_spec["link_data"]["video_id"] = video_id
    if headline:
        object_story_spec["link_data"]["name"] = headline
```

（放在 `if video_id` 之后、`if call_to_action_type` 之前）

- [ ] **Step 3: 验证编译 + headline 写入正确**

Run:
```bash
python -m py_compile meta_api.py && python -c "
import meta_api
# 检查函数签名接受了 headline，并确认构造 body 时 link_data 有 name
import inspect
sig = inspect.signature(meta_api.create_ad)
print('headline' in sig.parameters)
"
```
Expected: `PY OK` 无输出（编译通过），`True`

- [ ] **Step 4: Commit**

```bash
git add meta_api.py
git commit -m "feat(meta_api): create_ad 支持 headline（link_data.name）"
```

---

### Task 2: `delivery.submit_delivery_batch` 批量落库 + 循环投放

**Files:**
- Modify: `delivery.py`（在 `submit_delivery_campaign` 之后新增函数）

**Interfaces:**
- Consumes: `database.create_delivery_campaign(...)`、`database.create_delivery_adset(...)`、`database.add_to_delivery_queue(items, uid)`、`meta_api.create_campaign/create_adset/create_ad`、`meta_api.upload_ad_image/upload_ad_video`、本文件的 `_get_token`、`_delivery_events`、`_delivery_queues`、`_push_event`
- Produces: `submit_delivery_batch(params: dict, user_id: int = None) -> Tuple[str, Optional[str]]`，返回 `(batch_id, error)`。`params` 结构见 Task 3 的 `BatchPublishBody`。

- [ ] **Step 1: 写批量落库 + 投放函数骨架**

在 `delivery.py` 末尾新增（参考 `submit_delivery_campaign` 的结构，但落库在函数内完成）：

```python
def submit_delivery_batch(params: dict, user_id: int = None) -> tuple:
    """批量投放：N1 系列 × N2 广告组 × N3 广告，全部 PAUSED。返回 (batch_id, error)。"""
    import random as _random
    act_id = params.get("ad_account_id", "")
    token = _get_token(act_id, user_id)
    if not act_id or not token:
        return "", "广告账户未配置或无有效 token"

    n1 = int(params.get("n_campaigns") or 1)
    n2 = int(params.get("n_adsets") or 1)
    n3 = int(params.get("n_ads") or 1)
    assets = params.get("assets") or []
    total = n1 * n2 * n3
    if len(assets) != total:
        return "", f"素材数 {len(assets)} 不等于总广告数 {total}（{n1}×{n2}×{n3}）"

    headlines = [h for h in (params.get("headlines") or []) if h]
    ad_name = params.get("ad_name", "")
    if not ad_name:
        return "", "广告名不能为空"

    batch_id = uuid.uuid4().hex[:12]
    uid = user_id or 1

    # 1. 批量落库：N1 系列 + N1×N2 广告组 + N1×N2×N3 队列
    campaign_ids = []
    adset_ids = []
    with database.get_conn() as conn:
        pass  # 用 database 层函数逐条插入（见 Step 2）
    # 2. 后台投放（见 Step 3）

    return batch_id, None
```

- [ ] **Step 2: 完成落库循环（替换骨架里的 pass）**

用 `database` 层函数逐条插入（复用一个 `with database.get_conn()` 之外的独立调用即可，`create_*` 内部自带连接）：

```python
    queue_items = []
    for i in range(n1):
        cid = database.create_delivery_campaign(
            f"{params.get('campaign_name_prefix', 'Campaign')}-{i+1}",
            objective=params.get("objective", "OUTCOME_SALES"),
            budget_strategy=params.get("budget_strategy", "adset"),
            is_adset_budget_sharing_enabled=params.get("is_adset_budget_sharing_enabled", 0),
            daily_budget=params.get("campaign_daily_budget", 0),
            ad_account_id=act_id,
            page_id=params.get("page_id", ""),
            link_url=params.get("link_url", ""),
            call_to_action=params.get("call_to_action", "LEARN_MORE"),
            user_id=uid)
        campaign_ids.append(cid)
        for j in range(n2):
            aid = database.create_delivery_adset(
                cid, f"{params.get('adset_name_prefix', 'Adset')}-{i+1}-{j+1}",
                ad_account_id=act_id,
                pixel_id=params.get("pixel_id", ""),
                audience_id=params.get("audience_id", ""),
                daily_budget=params.get("adset_daily_budget", 0),
                bid_strategy=params.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
                bid_amount=params.get("bid_amount", 0),
                optimization_goal=params.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                billing_event=params.get("billing_event", "IMPRESSIONS"),
                destination_type=params.get("destination_type", "WEBSITE"),
                custom_event_type=params.get("custom_event_type", "PURCHASE"),
                attribution_spec_json=params.get("attribution_spec_json", ""),
                targeting_json=params.get("targeting_json", "{}"),
                user_id=uid)
            adset_ids.append(aid)
    # 素材按顺序分配到 (i,j,k)
    for idx, a in enumerate(assets):
        adset_id = adset_ids[idx // n3]
        path = a.get("_resolved_path") or ""
        queue_items.append({
            "batch_id": batch_id, "image_type": a.get("image_type", ""),
            "image_path": path, "image_prompt": "",
            "overlay_text": a.get("overlay_text", ""),
            "adset_id": adset_id,
        })
    database.add_to_delivery_queue(queue_items, uid)
```

- [ ] **Step 3: 完成后台投放循环（新增 `_run` 闭包）**

紧接落库之后，复用 `_delivery_events` / `_delivery_queues` / `_push_event`，后台线程循环创建：

```python
    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        completed = 0
        failed = 0
        try:
            _push_event(batch_id, "start", {"total": total})
            is_sharing = (params.get("is_adset_budget_sharing_enabled") == 1)
            campaign_daily = params.get("campaign_daily_budget") or None
            for i, cid in enumerate(campaign_ids):
                fb_cid, err = meta_api.create_campaign(
                    act_id, token, f"{params.get('campaign_name_prefix','Campaign')}-{i+1}",
                    objective=params.get("objective", "OUTCOME_SALES"),
                    status="PAUSED", special_ad_categories=[],
                    is_adset_budget_sharing_enabled=is_sharing, daily_budget=campaign_daily)
                if err:
                    failed += 1
                    _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建系列失败: {err}"})
                    continue
                database.update_delivery_campaign_fb_id(cid, fb_cid)
                for j in range(n2):
                    adset_id = adset_ids[i * n2 + j]
                    fb_adset_id, err = meta_api.create_adset(
                        act_id, token, f"{params.get('adset_name_prefix','Adset')}-{i+1}-{j+1}", fb_cid,
                        targeting=json.loads(params.get("targeting_json") or "{}"),
                        daily_budget=(params.get("adset_daily_budget") or None) if not campaign_daily else None,
                        bid_strategy=params.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
                        billing_event=params.get("billing_event", "IMPRESSIONS"),
                        optimization_goal=params.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                        promoted_object={"pixel_id": params.get("pixel_id"), "custom_event_type": params.get("custom_event_type", "PURCHASE")} if params.get("pixel_id") else None,
                        destination_type=params.get("destination_type", "WEBSITE"),
                        attribution_spec=json.loads(params.get("attribution_spec_json") or "[]") or None,
                        bid_amount=params.get("bid_amount") or None,
                        status="PAUSED")
                    if err:
                        failed += 1
                        _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告组失败: {err}"})
                        continue
                    database.update_delivery_adset_fb_id(adset_id, fb_adset_id)
                    for k in range(n3):
                        idx = i * n2 * n3 + j * n3 + k
                        asset = assets[idx]
                        headline = _random.choice(headlines) if headlines else ""
                        r = _deliver_ad_to_adset(
                            {"id": 0, "image_path": asset.get("_resolved_path", ""), "overlay_text": asset.get("overlay_text", ""), "adset_id": adset_id},
                            fb_adset_id, act_id, token,
                            params.get("page_id", ""), params.get("link_url", ""),
                            params.get("call_to_action", "LEARN_MORE"), user_id)
                        # 广告名统一、标题随机：重写 create_ad 调用见 Step 4
                        completed += 1 if r["status"] == "delivered" else 0
                        failed += 1 if r["status"] != "delivered" else 0
                        _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "current_id": idx, "fb_ad_id": r.get("fb_ad_id", "")})
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed})
        except Exception as e:
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed, "error": str(e)[:200]})
        finally:
            _delivery_events[batch_id].set()

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_run)
    executor.shutdown(wait=False)
    return batch_id, None
```

- [ ] **Step 4: 广告名统一 + 标题随机的建广告逻辑**

`_deliver_ad_to_adset` 目前用 `overlay_text` 当广告名，不符合「广告名统一」。在 `_run` 里不直接复用 `_deliver_ad_to_adset` 的广告名逻辑，改为新增一个内部建广告片段（上传创意 + `create_ad` 带 `name=ad_name`、`headline`）：

```python
                    for k in range(n3):
                        idx = i * n2 * n3 + j * n3 + k
                        asset = assets[idx]
                        path = asset.get("_resolved_path", "")
                        is_video = path.lower().endswith(".mp4")
                        image_hash = None
                        video_id = None
                        if is_video:
                            video_id, err = meta_api.upload_ad_video(act_id, token, path)
                        else:
                            image_hash, err = meta_api.upload_ad_image(act_id, token, path)
                        if err:
                            failed += 1
                            _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"上传创意失败: {err}"})
                            continue
                        headline = _random.choice(headlines) if headlines else ""
                        fb_ad_id, err = meta_api.create_ad(
                            act_id, token, ad_name, fb_adset_id,
                            creative_name=ad_name, page_id=params.get("page_id", ""),
                            image_hash=image_hash, video_id=video_id,
                            message=params.get("message", ""), link_url=params.get("link_url", ""),
                            call_to_action_type=params.get("call_to_action", "LEARN_MORE"),
                            headline=headline, status="PAUSED")
                        if err:
                            failed += 1
                            _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告失败: {err}"})
                            continue
                        completed += 1
                        _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "current_id": idx, "fb_ad_id": fb_ad_id})
```

（替换 Step 3 里 `for k in range(n3):` 循环体内那一段 `_deliver_ad_to_adset` 调用）

- [ ] **Step 5: 验证编译**

Run:
```bash
python -m py_compile delivery.py
```
Expected: 无输出（编译通过）

- [ ] **Step 6: Commit**

```bash
git add delivery.py
git commit -m "feat(delivery): 新增 submit_delivery_batch 批量落库+循环投放"
```

---

### Task 3: `POST /api/delivery/batch-publish` 接口 + 校验

**Files:**
- Modify: `main.py`（在投放引擎相关路由附近，`/api/delivery/publish` 之后）

**Interfaces:**
- Consumes: `delivery.submit_delivery_batch(params, uid)`、`delivery.resolve_output_path(url, OUTPUT_ROOT)`、`_opt_user_id(user)`
- Produces: `POST /api/delivery/batch-publish`，请求体 `BatchPublishBody`，返回 `{"success": bool, "batch_id"?: str, "message"?: str}`

- [ ] **Step 1: 定义请求体 `BatchPublishBody`**

在 `main.py` 的 `PublishBody` 类之后新增：

```python
class BatchAsset(BaseModel):
    image_url: str
    image_type: str = ""
    overlay_text: str = ""

class BatchPublishBody(BaseModel):
    ad_account_id: str = ""
    n_campaigns: int = 1
    n_adsets: int = 1
    n_ads: int = 1
    # 系列设置
    campaign_name_prefix: str = ""
    objective: str = "OUTCOME_SALES"
    budget_strategy: str = "adset"
    is_adset_budget_sharing_enabled: int = 0
    campaign_daily_budget: int = 0
    page_id: str = ""
    link_url: str = ""
    # 广告组设置
    adset_name_prefix: str = ""
    pixel_id: str = ""
    audience_id: str = ""
    adset_daily_budget: int = 0
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    bid_amount: int = 0
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    billing_event: str = "IMPRESSIONS"
    destination_type: str = "WEBSITE"
    custom_event_type: str = "PURCHASE"
    attribution_spec_json: str = ""
    targeting_json: str = "{}"
    # 广告设置
    ad_name: str = ""
    message: str = ""
    headlines: List[str] = []
    call_to_action: str = "LEARN_MORE"
    # 素材
    assets: List[BatchAsset] = []
```

- [ ] **Step 2: 新增接口 + 校验 + 解析素材路径**

```python
@app.post("/api/delivery/batch-publish")
def _batch_publish(body: BatchPublishBody, user: dict = Depends(get_current_user)):
    uid = _opt_user_id(user)
    n1, n2, n3 = body.n_campaigns, body.n_adsets, body.n_ads
    if n1 < 1 or n2 < 1 or n3 < 1:
        return {"success": False, "message": "系列数/广告组数/广告数都必须 ≥ 1"}
    if len(body.assets) != n1 * n2 * n3:
        return {"success": False, "message": f"素材数 {len(body.assets)} 不等于总广告数 {n1*n2*n3}"}
    if not body.ad_name.strip():
        return {"success": False, "message": "广告名不能为空"}
    if body.optimization_goal == "OFFSITE_CONVERSIONS" and not body.pixel_id:
        return {"success": False, "message": "优化目标为站外转化时必须选择数据集（Pixel）"}
    if body.budget_strategy == "adset" and not body.adset_daily_budget:
        return {"success": False, "message": "广告组预算 (ABO) 模式下必须设置组日预算"}

    # 解析素材本地路径
    resolved = []
    for a in body.assets:
        path = delivery.resolve_output_path(a.image_url, OUTPUT_ROOT)
        if not path:
            return {"success": False, "message": f"无效素材路径: {a.image_url}"}
        resolved.append({"image_type": a.image_type, "overlay_text": a.overlay_text, "_resolved_path": path})

    params = body.model_dump()
    params["assets"] = resolved
    batch_id, err = delivery.submit_delivery_batch(params, uid)
    if err:
        return {"success": False, "message": err}
    return {"success": True, "batch_id": batch_id}
```

- [ ] **Step 3: 验证编译**

Run:
```bash
python -m py_compile main.py
```
Expected: 无输出（编译通过）

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(main): 新增 POST /api/delivery/batch-publish 批量投放接口"
```

---

### Task 4: 前端投放向导重写为 4 步

**Files:**
- Modify: `static/index.html`（替换 `tab-delivery-wizard` 区块 + `dw*` JS 函数）

**Interfaces:**
- Consumes: 现有 `/api/meta/accounts`、`/api/meta/promote-pages?act_id=`、`/api/meta/account-pixels?act_id=`、`/api/meta/account-audiences?act_id=`、`genCollectForDelivery`/`naCollectForDelivery` 的素材收集逻辑、`switchTab`
- Produces: 新 4 步向导 DOM + `dwInitBatch`/`dwGoto`/`dwOnAccountChange`/`dwRenderAssetPicker`/`dwRenderSettings`/`dwPublishBatch` 等函数，最终 `POST /api/delivery/batch-publish`

- [ ] **Step 1: 重写 HTML 结构为 4 步**

替换现有 `id="tab-delivery-wizard"` 的整个 `<div>`（约 [index.html:1091-1198](static/index.html#L1091-L1198)）为 4 个步骤块：

```html
<div id="tab-delivery-wizard" class="flex-1 bg-slate-900 overflow-y-auto px-5 py-4 min-w-0" hidden>
  <div class="max-w-3xl mx-auto">
    <div class="flex items-center gap-3 mb-4" id="dwSteps"></div>
    <!-- Step 1 账户+数量 -->
    <div id="dwStep1" class="card-dark p-6 space-y-4">
      <h2 class="text-sm font-bold text-white">Step 1 · 广告账户 + 数量</h2>
      <div><label class="mb-1 block text-sm font-medium text-slate-400">广告账户</label><select id="dwAccount" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white" onchange="dwOnAccountChange()"></select></div>
      <div class="grid grid-cols-3 gap-3">
        <div><label class="mb-1 block text-sm font-medium text-slate-400">系列数</label><input id="dwN1" type="number" min="1" value="1" onchange="dwCalcTotal()" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></div>
        <div><label class="mb-1 block text-sm font-medium text-slate-400">每系列广告组数</label><input id="dwN2" type="number" min="1" value="1" onchange="dwCalcTotal()" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></div>
        <div><label class="mb-1 block text-sm font-medium text-slate-400">每广告组广告数</label><input id="dwN3" type="number" min="1" value="1" onchange="dwCalcTotal()" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></div>
      </div>
      <div class="text-sm text-slate-300">总广告数：<span id="dwTotal" class="font-bold text-indigo-400">1</span></div>
      <div class="flex justify-end"><button onclick="dwGoto(2)" class="rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white">下一步</button></div>
    </div>
    <!-- Step 2 选素材 -->
    <div id="dwStep2" class="card-dark p-6 space-y-4" hidden>
      <h2 class="text-sm font-bold text-white">Step 2 · 选素材（需 <span id="dwNeedN" class="text-indigo-400"></span> 张）</h2>
      <div id="dwAssetPool" class="grid grid-cols-4 gap-2 max-h-80 overflow-y-auto"></div>
      <div id="dwAssetSelected" class="text-xs text-slate-400"></div>
      <div class="flex justify-between"><button onclick="dwGoto(1)" class="rounded-lg border border-slate-700 px-5 py-2.5 text-sm text-slate-300">上一步</button><button onclick="dwGoto(3)" class="rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white">下一步</button></div>
    </div>
    <!-- Step 3 三层设置 -->
    <div id="dwStep3" class="card-dark p-6 space-y-4" hidden>
      <h2 class="text-sm font-bold text-white">Step 3 · 三层设置</h2>
      <!-- 系列设置 -->
      <div class="rounded-xl bg-slate-800/50 border border-slate-700 p-4 space-y-3">
        <h3 class="text-xs font-semibold text-slate-300">系列设置</h3>
        <input id="dwCampaignPrefix" placeholder="系列名前缀" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
        <select id="dwBudgetStrategy" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"><option value="adset">广告组预算 (ABO)</option><option value="campaign">系列预算 (CBO)</option></select>
        <select id="dwPageId" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></select>
        <input id="dwLinkUrl" placeholder="落地页网址" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
      </div>
      <!-- 广告组设置 -->
      <div class="rounded-xl bg-slate-800/50 border border-slate-700 p-4 space-y-3">
        <h3 class="text-xs font-semibold text-slate-300">广告组设置</h3>
        <input id="dwAdsetPrefix" placeholder="广告组名前缀" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
        <input id="dwAdsetDailyBudget" type="number" min="0" placeholder="组日预算（美元，ABO 必填）" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
        <select id="dwOptimizationGoal" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"><option value="OFFSITE_CONVERSIONS">OFFSITE_CONVERSIONS（站外转化）</option><option value="LINK_CLICKS">LINK_CLICKS（链接点击）</option><option value="LANDING_PAGE_VIEWS">LANDING_PAGE_VIEWS（落地页浏览）</option></select>
        <select id="dwPixelId" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></select>
        <select id="dwAudienceId" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"></select>
      </div>
      <!-- 广告设置 -->
      <div class="rounded-xl bg-slate-800/50 border border-slate-700 p-4 space-y-3">
        <h3 class="text-xs font-semibold text-slate-300">广告设置</h3>
        <input id="dwAdName" placeholder="广告名（所有广告同名）" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
        <input id="dwMessage" placeholder="广告文案" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white">
        <div><label class="mb-1 block text-xs text-slate-400">广告标题（5-10 个，随机分配）</label><div id="dwHeadlines" class="space-y-2"></div><button onclick="dwAddHeadline()" class="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300">+ 添加标题</button></div>
        <select id="dwCTA" class="w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm text-white"><option value="LEARN_MORE">LEARN_MORE</option><option value="SIGN_UP">SIGN_UP</option><option value="SUBSCRIBE">SUBSCRIBE</option><option value="SHOP_NOW">SHOP_NOW</option><option value="DOWNLOAD">DOWNLOAD</option></select>
      </div>
      <div class="flex justify-between"><button onclick="dwGoto(2)" class="rounded-lg border border-slate-700 px-5 py-2.5 text-sm text-slate-300">上一步</button><button onclick="dwGoto(4)" class="rounded-lg bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white">下一步</button></div>
    </div>
    <!-- Step 4 预览+发布 -->
    <div id="dwStep4" class="card-dark p-6 space-y-4" hidden>
      <h2 class="text-sm font-bold text-white">Step 4 · 预览 + 发布</h2>
      <div id="dwPreview" class="text-sm text-slate-300"></div>
      <div id="dwPublishProgress" class="text-xs text-slate-400"></div>
      <div class="flex justify-between"><button onclick="dwGoto(3)" class="rounded-lg border border-slate-700 px-5 py-2.5 text-sm text-slate-300">上一步</button><button onclick="dwPublishBatch()" class="rounded-lg bg-emerald-600 px-6 py-2.5 text-sm font-medium text-white">发布到 Meta</button></div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 重写 `dw*` JS 函数**

替换 [index.html:3686](static/index.html#L3686) 起旧向导的全部 `dw*` 函数（`_dwCampaignId` 到 `dwOpenStream` 之间），保留 `genCollectForDelivery`/`naCollectForDelivery`（它们改为填充 `_dwAssets` 素材池）。新增核心函数：

```javascript
var _dwAssets = [];        // 素材池 [{image_url, image_type, overlay_text}]
var _dwSelected = new Set(); // 已选素材索引
var _dwAudiences = [];
var _dwEs = null;

function dwInitBatch() {
  dwLoadOptions();
  dwRenderHeadlines();
  dwRenderSteps(1);
}
function dwGoto(step) {
  ['dwStep1','dwStep2','dwStep3','dwStep4'].forEach(function(id, i){
    document.getElementById(id).hidden = (i + 1) !== step;
  });
  if (step === 2) dwRenderAssetPool();
  if (step === 3) dwRenderSettings();
  if (step === 4) dwRenderPreview();
}
function dwCalcTotal() {
  var n1 = parseInt(document.getElementById('dwN1').value) || 1;
  var n2 = parseInt(document.getElementById('dwN2').value) || 1;
  var n3 = parseInt(document.getElementById('dwN3').value) || 1;
  document.getElementById('dwTotal').textContent = n1 * n2 * n3;
}
function dwOnAccountChange() {
  var actId = document.getElementById('dwAccount').value;
  dwLoadPages(actId, false);
  dwLoadPixels(actId);
  dwLoadAudiences(actId);
}
function dwRenderHeadlines() {
  var el = document.getElementById('dwHeadlines');
  el.innerHTML = '';
  for (var i = 0; i < 5; i++) el.appendChild(dwHeadlineInput());
}
function dwHeadlineInput() {
  var inp = document.createElement('input');
  inp.className = 'w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm text-white';
  inp.placeholder = '标题';
  return inp;
}
function dwAddHeadline() { document.getElementById('dwHeadlines').appendChild(dwHeadlineInput()); }
function dwRenderAssetPool() {
  // 复用 _dwAssets，渲染可勾选缩略图
  var el = document.getElementById('dwAssetPool');
  var need = parseInt(document.getElementById('dwTotal').textContent) || 0;
  document.getElementById('dwNeedN').textContent = need;
  if (!_dwAssets.length) { el.innerHTML = '<div class="text-xs text-slate-500 col-span-4 py-8 text-center">请先从生产中心或小说分析收集素材</div>'; return; }
  var html = '';
  _dwAssets.forEach(function(a, i){
    html += '<div class="relative cursor-pointer" onclick="dwToggleAsset(' + i + ')"><img src="' + absUrl(a.image_url) + '" class="w-full h-16 object-cover rounded-lg bg-slate-700"><div class="absolute inset-0 rounded-lg ' + (_dwSelected.has(i) ? 'ring-2 ring-indigo-500' : '') + '"></div></div>';
  });
  el.innerHTML = html;
  dwUpdateSelected();
}
function dwToggleAsset(i) {
  if (_dwSelected.has(i)) _dwSelected.delete(i); else _dwSelected.add(i);
  dwRenderAssetPool();
}
function dwUpdateSelected() {
  var need = parseInt(document.getElementById('dwTotal').textContent) || 0;
  document.getElementById('dwAssetSelected').textContent = '已选 ' + _dwSelected.size + ' / ' + need;
}
function dwRenderSettings() {
  // 把已选素材按顺序暂存，供预览
}
function dwRenderPreview() {
  var n1 = parseInt(document.getElementById('dwN1').value) || 1;
  var n2 = parseInt(document.getElementById('dwN2').value) || 1;
  var n3 = parseInt(document.getElementById('dwN3').value) || 1;
  document.getElementById('dwPreview').innerHTML = n1 + ' 系列 × ' + n2 + ' 广告组 × ' + n3 + ' 广告 = <b>' + (n1*n2*n3) + '</b> 个广告，已选素材 ' + _dwSelected.size + ' 个';
}
function dwPublishBatch() {
  var n1 = parseInt(document.getElementById('dwN1').value) || 1;
  var n2 = parseInt(document.getElementById('dwN2').value) || 1;
  var n3 = parseInt(document.getElementById('dwN3').value) || 1;
  var total = n1 * n2 * n3;
  if (_dwSelected.size !== total) { alert('素材数 ' + _dwSelected.size + ' 不等于总广告数 ' + total); return; }
  var sel = Array.from(_dwSelected).sort(function(a,b){return a-b;});
  var assets = sel.map(function(i){ return _dwAssets[i]; });
  var headlines = [];
  document.querySelectorAll('#dwHeadlines input').forEach(function(inp){ var v = inp.value.trim(); if (v) headlines.push(v); });
  var body = {
    ad_account_id: document.getElementById('dwAccount').value,
    n_campaigns: n1, n_adsets: n2, n_ads: n3,
    campaign_name_prefix: document.getElementById('dwCampaignPrefix').value,
    budget_strategy: document.getElementById('dwBudgetStrategy').value,
    is_adset_budget_sharing_enabled: document.getElementById('dwBudgetStrategy').value === 'campaign' ? 1 : 0,
    page_id: document.getElementById('dwPageId').value,
    link_url: document.getElementById('dwLinkUrl').value,
    adset_name_prefix: document.getElementById('dwAdsetPrefix').value,
    adset_daily_budget: Math.round(parseFloat(document.getElementById('dwAdsetDailyBudget').value) * 100) || 0,
    optimization_goal: document.getElementById('dwOptimizationGoal').value,
    pixel_id: document.getElementById('dwPixelId').value,
    audience_id: document.getElementById('dwAudienceId').value,
    ad_name: document.getElementById('dwAdName').value,
    message: document.getElementById('dwMessage').value,
    headlines: headlines,
    call_to_action: document.getElementById('dwCTA').value,
    assets: assets
  };
  var btn = event.target; btn.disabled = true; btn.textContent = '提交中...';
  fetch('/api/delivery/batch-publish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d && d.success) dwOpenStream(d.batch_id);
      else { document.getElementById('dwPublishProgress').innerHTML = '<div class="text-xs text-red-400">发布失败: ' + ((d && d.message) || '未知错误') + '</div>'; btn.disabled = false; btn.textContent = '发布到 Meta'; }
    })
    .catch(function(e){ document.getElementById('dwPublishProgress').innerHTML = '<div class="text-xs text-red-400">发布失败: ' + e + '</div>'; btn.disabled = false; btn.textContent = '发布到 Meta'; });
}
```

保留并复用已有的 `dwLoadOptions`（改成填 `dwAccount`）、`dwLoadPages`/`dwLoadPixels`/`dwLoadAudiences`、`dwFillSelect`、`dwOpenStream`。

- [ ] **Step 3: 修改 `genCollectForDelivery` / `naCollectForDelivery` 填充素材池**

把两个函数里 `_dwAssets = assets` 之后，从 `switchTab("delivery-wizard")` 改为 `switchTab("delivery-wizard"); dwInitBatch();`，并清空 `_dwSelected`：

```javascript
_dwAssets = assets;
_dwSelected = new Set();
switchTab("delivery-wizard");
dwInitBatch();
```

- [ ] **Step 4: 浏览器验证**

启动服务 `python -m uvicorn main:app --port 8000`，浏览器打开 `http://127.0.0.1:8000/static/index.html` → 投放向导，走 4 步：选账户 → 填数量 → 选素材 → 填三层设置 → 发布，确认 SSE 进度正常、Meta 后台能看到 N1×N2×N3 个 PAUSED 广告。

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat(frontend): 投放向导重写为 4 步批量流程"
```

---

## Self-Review 结果

- **Spec 覆盖**：数量模型（Task 2/3 校验）✓；4 步流程（Task 4）✓；广告名统一（Task 2 Step 4）✓；标题 5-10 随机（Task 2 Step 4 + Task 4）✓；CTA 选择（Task 4）✓；Pixel/预算校验（Task 3）✓；全部 PAUSED（Task 2/3 固定值）✓；`fb_campaign_id` 回写（Task 2 Step 3）✓
- **占位符扫描**：无 TBD/TODO
- **类型一致性**：`BatchPublishBody` 字段名与 `submit_delivery_batch` 读取的 `params.get(...)` 键名一致；`create_ad` 的 `headline` 参数 Task 1 定义、Task 2 Step 4 使用，一致
