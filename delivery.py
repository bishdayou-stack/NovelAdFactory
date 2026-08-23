"""投放引擎：素材审核队列 → 批量创建 Meta 广告"""
import json
import time
import threading
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
import meta_api


# 投放进度事件队列（用于 SSE 推送）
_delivery_queues: Dict[str, list] = {}
_delivery_events: Dict[str, threading.Event] = {}


def _get_token(act_id: str, user_id: int = None) -> Optional[str]:
    """从数据库获取账户 token。优先级: BM system_token → 账户 token → 全局默认"""
    account = database.get_meta_account(act_id, user_id)
    if not account:
        return None
    bm_id = account.get("bm_id", "")
    token = (database.get_bm_token(bm_id) if bm_id else "") or account.get("access_token")
    if not token:
        try:
            cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
            token = cfg.get("meta", {}).get("default_access_token", "")
        except Exception:
            token = ""
    return token or None


def _push_event(batch_id: str, event_type: str, data: dict = None):
    """向投放批次的事件队列推送事件"""
    if batch_id in _delivery_queues:
        _delivery_queues[batch_id].append({
            "type": event_type,
            "data": data or {},
            "timestamp": time.time()
        })


def _deliver_one(queue_item: dict, template: dict, user_id: int = None) -> dict:
    """执行单条广告的投放：上传创意 → 创建 Campaign → 创建 AdSet → 创建 Ad"""
    result = {
        "queue_id": queue_item["id"],
        "status": "failed",
    }
    image_path = queue_item.get("image_path", "")
    image_type = queue_item.get("image_type", "")
    overlay_text = queue_item.get("overlay_text", "")
    batch_id = queue_item.get("batch_id", "")

    act_id = template.get("ad_account_id", "")
    if not act_id:
        result["error"] = "模板未绑定广告账户"
        return result

    token = _get_token(act_id, user_id)
    if not token:
        result["error"] = f"未找到账户 {act_id} 的 token"
        return result

    targeting = json.loads(template.get("targeting_json", "{}")) if template.get("targeting_json") else {}
    budget_value = template.get("budget_value", 0)
    budget_type = template.get("budget_type", "daily_budget")
    bid_strategy = template.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")
    optimization_goal = template.get("optimization_goal", "OFFSITE_CONVERSIONS")
    billing_event = template.get("billing_event", "IMPRESSIONS")
    conversion_event = template.get("conversion_event", "")

    # 1. 上传创意（图片）
    creative_hash, err = meta_api.upload_ad_image(act_id, token, image_path)
    if err:
        result["error"] = f"上传图片失败: {err}"
        return result
    result["creative_hash"] = creative_hash

    # 2. 创建 Campaign
    today = time.strftime("%Y-%m-%d")
    campaign_name = f"{batch_id}_{today}_{act_id}"
    campaign_id, err = meta_api.create_campaign(
        act_id, token, campaign_name,
        objective="OUTCOME_TRAFFIC",
        status="PAUSED",
        special_ad_categories=[]
    )
    if err:
        result["error"] = f"创建 Campaign 失败: {err}"
        return result
    result["fb_campaign_id"] = campaign_id

    # 3. 创建 AdSet
    adset_name = f"{image_type}_{today}"
    daily_budget = budget_value if budget_type == "daily_budget" else None
    lifetime_budget = budget_value if budget_type == "lifetime_budget" else None

    promoted_object = None
    if conversion_event:
        promoted_object = {"custom_event_type": conversion_event}

    adset_id, err = meta_api.create_adset(
        act_id, token, adset_name, campaign_id,
        targeting=targeting, daily_budget=daily_budget,
        lifetime_budget=lifetime_budget, bid_strategy=bid_strategy,
        billing_event=billing_event, optimization_goal=optimization_goal,
        promoted_object=promoted_object, status="PAUSED"
    )
    if err:
        result["error"] = f"创建 AdSet 失败: {err}"
        return result
    result["fb_adset_id"] = adset_id

    # 4. 创建 Ad
    ad_name = f"{image_type}_{queue_item['id']}"
    page_id = targeting.get("page_id", "")
    link_url = f"https://novel.example.com/{batch_id}"

    ad_id, err = meta_api.create_ad(
        act_id, token, ad_name, adset_id,
        creative_name=ad_name, page_id=page_id,
        image_hash=creative_hash,
        message=overlay_text or "", link_url=link_url,
        status="PAUSED"
    )
    if err:
        result["error"] = f"创建 Ad 失败: {err}"
        return result
    result["fb_ad_id"] = ad_id
    result["fb_creative_id"] = creative_hash

    result["status"] = "delivered"
    return result


def submit_delivery_batch(queue_ids: List[int], template_id: int, user_id: int = None):
    """提交投放批次，返回 (batch_id, error)，后台异步执行"""
    batch_id = uuid.uuid4().hex[:12]
    template = database.get_delivery_template(template_id, user_id)

    if not template:
        return "", "模板不存在"

    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        _push_event(batch_id, "start", {"total": len(queue_ids)})

        # Read queue items
        items = []
        with database.get_conn() as conn:
            for qid in queue_ids:
                row = conn.execute("SELECT * FROM delivery_queue WHERE id = ?", (qid,)).fetchone()
                if row:
                    items.append(dict(row))

        completed = 0
        failed = 0
        max_workers = min(4, len(items))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_deliver_one, item, template, user_id): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    r = future.result()
                except Exception as e:
                    r = {"queue_id": item["id"], "status": "failed", "error": str(e)}

                database.update_queue_delivery_result(
                    r["queue_id"], r["status"],
                    fb_campaign_id=r.get("fb_campaign_id"),
                    fb_adset_id=r.get("fb_adset_id"),
                    fb_ad_id=r.get("fb_ad_id"),
                    fb_creative_id=r.get("fb_creative_id"),
                    delivery_params_json=json.dumps(template, ensure_ascii=False),
                    error_message=r.get("error")
                )

                if r["status"] == "delivered":
                    completed += 1
                else:
                    failed += 1

                _push_event(batch_id, "progress", {
                    "completed": completed,
                    "failed": failed,
                    "total": len(items),
                    "current_id": r["queue_id"],
                    "fb_ad_id": r.get("fb_ad_id", "")
                })

        _push_event(batch_id, "complete", {"completed": completed, "failed": failed})
        _delivery_events[batch_id].set()

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_run)
    executor.shutdown(wait=False)

    return batch_id, None


def get_delivery_progress(batch_id: str) -> dict:
    """查询投放批次进度"""
    events = _delivery_queues.get(batch_id, [])
    is_done = _delivery_events.get(batch_id, threading.Event()).is_set()

    last_event = {}
    for e in events:
        if e["type"] == "progress":
            last_event = e

    return {
        "batch_id": batch_id,
        "is_done": is_done,
        "total": last_event.get("data", {}).get("total", 0),
        "completed": last_event.get("data", {}).get("completed", 0),
        "failed": last_event.get("data", {}).get("failed", 0),
    }


def resolve_output_path(url: str, output_root) -> Optional[str]:
    """把 /static/output/{batch_id}/{name} 转成本地路径并校验存在，防路径穿越。"""
    if not url or not url.startswith("/static/output/"):
        return None
    rel = url[len("/static/output/"):]
    try:
        p = (Path(output_root) / rel).resolve()
        root = Path(output_root).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            return None
        return str(p)
    except Exception:
        return None


def _deliver_ad_to_adset(queue_item: dict, fb_adset_id: str, act_id: str, token: str,
                         page_id: str, link_url: str, call_to_action: str,
                         user_id: int = None) -> dict:
    """只上传创意 + 建 Ad（系列/广告组已由上层建好）。返回 result dict。"""
    result = {"queue_id": queue_item["id"], "status": "failed"}
    image_path = queue_item.get("image_path", "")
    is_video = image_path.lower().endswith(".mp4")
    image_hash = None
    video_id = None
    if is_video:
        video_id, err = meta_api.upload_ad_video(act_id, token, image_path)
        result["fb_creative_id"] = video_id
    else:
        image_hash, err = meta_api.upload_ad_image(act_id, token, image_path)
        result["fb_creative_id"] = image_hash
    if err:
        result["error"] = f"上传创意失败: {err}"
        return result
    ad_name = (queue_item.get("overlay_text", "") or str(queue_item["id"]))[:60]
    ad_id, err = meta_api.create_ad(
        act_id, token, ad_name, fb_adset_id,
        creative_name=str(queue_item["id"]), page_id=page_id,
        image_hash=image_hash, video_id=video_id,
        message=queue_item.get("overlay_text", ""), link_url=link_url,
        call_to_action_type=call_to_action, status="PAUSED")
    if err:
        result["error"] = f"建广告失败: {err}"
        return result
    result["fb_ad_id"] = ad_id
    result["status"] = "delivered"
    return result


def submit_delivery_campaign(campaign_id: int, user_id: int = None):
    """按 1 系列 → N 广告组 → 组内 n 广告 分层创建，全部 PAUSED。返回 (batch_id, error)。"""
    batch_id = uuid.uuid4().hex[:12]
    with database.get_conn() as conn:
        camp = conn.execute("SELECT * FROM delivery_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        adsets = conn.execute("SELECT * FROM delivery_adsets WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        adset_ids = [r["id"] for r in adsets]
        queue = []
        if adset_ids:
            ph = ",".join("?" * len(adset_ids))
            queue = conn.execute(f"SELECT * FROM delivery_queue WHERE adset_id IN ({ph}) AND status='pending'", adset_ids).fetchall()
    if not camp:
        return "", "系列不存在"
    camp = dict(camp)
    adsets = [dict(a) for a in adsets]
    queue = [dict(q) for q in queue]
    act_id = camp.get("ad_account_id", "")
    if not act_id:
        # 兼容旧数据：系列层无账户时回退到广告组层
        act_ids = {a.get("ad_account_id") for a in adsets if a.get("ad_account_id")}
        if len(act_ids) > 1:
            return "", "同一系列的广告组必须使用同一个广告账户"
        act_id = adsets[0].get("ad_account_id", "") if adsets else ""
    token = _get_token(act_id, user_id)
    if not act_id or not token:
        return "", "广告账户未配置或无有效 token"
    total = len(queue)
    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        completed = 0
        failed = 0
        try:
            _push_event(batch_id, "start", {"total": total})
            campaign_daily = camp.get("daily_budget") or None
            # Meta v25：ABO 必须 is_adset_budget_sharing_enabled=True，CBO 必须 False（同 batch-publish）
            is_sharing = (campaign_daily is None)
            fb_campaign_id, err = meta_api.create_campaign(
                act_id, token, camp.get("name", ""), objective=camp.get("objective", "OUTCOME_SALES"),
                status="PAUSED", special_ad_categories=[],
                is_adset_budget_sharing_enabled=is_sharing, daily_budget=campaign_daily)
            if err:
                _push_event(batch_id, "complete", {"completed": 0, "failed": total, "error": f"建系列失败: {err}"})
                return
            if fb_campaign_id:
                database.update_delivery_campaign_fb_id(campaign_id, fb_campaign_id)
            for adset in adsets:
                targeting = json.loads(adset.get("targeting_json") or "{}")
                attribution = json.loads(adset.get("attribution_spec_json") or "[]") or None
                promoted = {"pixel_id": adset["pixel_id"], "custom_event_type": adset.get("custom_event_type", "PURCHASE")} if adset.get("pixel_id") else None
                adset_daily = adset.get("daily_budget") or None
                if campaign_daily:
                    adset_daily = None  # CBO：预算在系列层，组层不给预算
                if not campaign_daily and not adset_daily:
                    failed += 1
                    _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total,
                        "error": f"广告组 {adset.get('name','')} 未设置预算（ABO 模式需设置组日预算）"})
                    continue
                fb_adset_id, err = meta_api.create_adset(
                    act_id, token, adset.get("name", ""), fb_campaign_id,
                    targeting=targeting, daily_budget=adset_daily,
                    bid_strategy=adset.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP"),
                    billing_event=adset.get("billing_event", "IMPRESSIONS"),
                    optimization_goal=adset.get("optimization_goal", "OFFSITE_CONVERSIONS"),
                    promoted_object=promoted, destination_type=adset.get("destination_type", "WEBSITE"),
                    attribution_spec=attribution, bid_amount=adset.get("bid_amount") or None,
                    status="PAUSED")
                if err:
                    failed += 1
                    _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告组失败: {err}"})
                    continue
                database.update_delivery_adset_fb_id(adset["id"], fb_adset_id)
                for item in queue:
                    if item.get("adset_id") != adset["id"]:
                        continue
                    r = _deliver_ad_to_adset(item, fb_adset_id, act_id, token,
                                             camp.get("page_id", ""), camp.get("link_url", ""),
                                             camp.get("call_to_action", "LEARN_MORE"), user_id)
                    database.update_queue_delivery_result(
                        r["queue_id"], r["status"],
                        fb_campaign_id=fb_campaign_id, fb_adset_id=fb_adset_id,
                        fb_ad_id=r.get("fb_ad_id"), fb_creative_id=r.get("fb_creative_id"),
                        delivery_params_json=json.dumps(camp, ensure_ascii=False),
                        error_message=r.get("error"))
                    if r["status"] == "delivered":
                        completed += 1
                    else:
                        failed += 1
                    _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "current_id": r["queue_id"], "fb_ad_id": r.get("fb_ad_id", "")})
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed})
        except Exception as e:
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed, "error": str(e)[:200]})
        finally:
            _delivery_events[batch_id].set()

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_run)
    executor.shutdown(wait=False)
    return batch_id, None


def submit_batch_publish(params: dict, user_id: int = None) -> tuple:
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
    if not headlines:
        return "", "广告标题至少填写 1 个"

    batch_id = uuid.uuid4().hex[:12]
    uid = user_id or 1

    # 1. 批量落库：N1 系列 + N1×N2 广告组 + N1×N2×N3 队列
    campaign_ids = []
    adset_ids = []
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

    # 2. 后台投放
    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        completed = 0
        failed = 0
        try:
            _push_event(batch_id, "start", {"total": total})
            campaign_daily = params.get("campaign_daily_budget") or None
            # Meta v25：ABO（无系列预算）必须 is_adset_budget_sharing_enabled=True（Advantage 系列预算共享 20%），
            # CBO（有系列预算）必须 False。这里按「是否有系列日预算」推导，不依赖前端的旧字段。
            is_sharing = (campaign_daily is None)
            bid_strategy = params.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")
            ad_bid_amount = params.get("bid_amount") or None
            bid_constraints = None
            optimization_goal = params.get("optimization_goal", "OFFSITE_CONVERSIONS")
            status = params.get("status", "PAUSED")  # 投放状态：PAUSED 草稿 / ACTIVE 开启
            placements = json.loads(params.get("placements_json") or "{}") or None
            if bid_strategy == "LOWEST_COST_WITH_MIN_ROAS":
                # 广告花费回报目标：用 bid_constraints.roas_average_floor（ROAS × 10000）
                # 且必须价值优化（VALUE），不能用站外转化
                optimization_goal = "VALUE"
                roas = params.get("roas") or 0
                if roas:
                    bid_constraints = {"roas_average_floor": int(float(roas) * 10000)}
                ad_bid_amount = None
            elif campaign_daily and not ad_bid_amount and bid_strategy == "LOWEST_COST_WITHOUT_CAP":
                ad_bid_amount = 500  # CBO 模式 Meta v25 要求竞价金额（5 美元）
            # 客户生命周期策略（Advantage+ 受众）：默认开启，自动扩展受众。
            # 注意：版位必须合并进 targeting 对象（publisher_platforms / facebook_positions 等），
            # 独立的 placements 字段会被 Meta v25 忽略（已实测：传非法值都不报错）。
            targeting = json.loads(params.get("targeting_json") or "{}")
            if placements:
                targeting.update(placements)
            if params.get("advantage_audience", 1):
                targeting["targeting_automation"] = {"advantage_audience": 1}
            for i, cid in enumerate(campaign_ids):
                fb_cid, err = meta_api.create_campaign(
                    act_id, token, f"{params.get('campaign_name_prefix','Campaign')}-{i+1}",
                    objective=params.get("objective", "OUTCOME_SALES"),
                    status=status, special_ad_categories=[],
                    is_adset_budget_sharing_enabled=is_sharing, daily_budget=campaign_daily,
                    bid_strategy=bid_strategy)
                if err:
                    failed += n2 * n3
                    _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建系列失败: {err}"})
                    continue
                database.update_delivery_campaign_fb_id(cid, fb_cid)
                for j in range(n2):
                    adset_id = adset_ids[i * n2 + j]
                    fb_adset_id, err = meta_api.create_adset(
                        act_id, token, f"{params.get('adset_name_prefix','Adset')}-{i+1}-{j+1}", fb_cid,
                        targeting=targeting,
                        daily_budget=(params.get("adset_daily_budget") or None) if not campaign_daily else None,
                        bid_strategy=bid_strategy,
                        billing_event=params.get("billing_event", "IMPRESSIONS"),
                        optimization_goal=optimization_goal,
                        promoted_object={"pixel_id": params.get("pixel_id"), "custom_event_type": params.get("custom_event_type", "PURCHASE")} if params.get("pixel_id") else None,
                        destination_type=params.get("destination_type", "WEBSITE"),
                        attribution_spec=json.loads(params.get("attribution_spec_json") or "[]") or None,
                        bid_amount=ad_bid_amount,
                        bid_constraints=bid_constraints,
                        status=status)
                    if err:
                        failed += n3
                        _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告组失败: {err}"})
                        continue
                    database.update_delivery_adset_fb_id(adset_id, fb_adset_id)
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
                            headline=headline, status=status,
                            advantage_creative=params.get("advantage_creative", 1),
                            multi_advertiser=params.get("multi_advertiser", 0))
                        if err:
                            failed += 1
                            _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "error": f"建广告失败: {err}"})
                            continue
                        completed += 1
                        _push_event(batch_id, "progress", {"completed": completed, "failed": failed, "total": total, "current_id": idx, "fb_ad_id": fb_ad_id})
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed})
        except Exception as e:
            _push_event(batch_id, "complete", {"completed": completed, "failed": failed, "error": str(e)[:200]})
        finally:
            _delivery_events[batch_id].set()

    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(_run)
    executor.shutdown(wait=False)
    return batch_id, None
