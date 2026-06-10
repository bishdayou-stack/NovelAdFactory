"""投放引擎：素材审核队列 → 批量创建 Meta 广告"""
import json
import time
import threading
import uuid
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
import meta_api


# 投放进度事件队列（用于 SSE 推送）
_delivery_queues: Dict[str, list] = {}
_delivery_events: Dict[str, threading.Event] = {}


def _get_token(act_id: str) -> Optional[str]:
    """从数据库获取账户 token"""
    account = database.get_meta_account(act_id)
    if not account:
        return None
    return account.get("access_token")


def _push_event(batch_id: str, event_type: str, data: dict = None):
    """向投放批次的事件队列推送事件"""
    if batch_id in _delivery_queues:
        _delivery_queues[batch_id].append({
            "type": event_type,
            "data": data or {},
            "timestamp": time.time()
        })


def _deliver_one(queue_item: dict, template: dict) -> dict:
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

    token = _get_token(act_id)
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


def submit_delivery_batch(queue_ids: List[int], template_id: int):
    """提交投放批次，返回 (batch_id, error)，后台异步执行"""
    batch_id = uuid.uuid4().hex[:12]
    template = database.get_delivery_template(template_id)

    if not template:
        return "", "模板不存在"

    _delivery_events[batch_id] = threading.Event()
    _delivery_queues[batch_id] = []

    def _run():
        _push_event(batch_id, "start", {"total": len(queue_ids)})

        # Read queue items outside the readonly context
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
            futures = {executor.submit(_deliver_one, item, template): item for item in items}
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
