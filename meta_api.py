"""Meta (Facebook) Graph API 客户端封装 — 认证、速率限制、请求重试"""
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests as http_requests

GRAPH_API_BASE = "https://graph.facebook.com"
API_VERSION = "v25.0"

# 速率控制：每账户每秒最多 4 次调用
_RATE_LIMITS: Dict[str, Tuple[float, int]] = {}  # act_id -> (last_reset_time, remaining)


def _check_rate(act_id: str) -> None:
    """检查并等待速率限制恢复"""
    now = time.time()
    if act_id in _RATE_LIMITS:
        last_reset, remaining = _RATE_LIMITS[act_id]
        if now - last_reset >= 1.0:
            _RATE_LIMITS[act_id] = (now, 3)
            return
        if remaining <= 0:
            sleep_time = 1.0 - (now - last_reset)
            if sleep_time > 0:
                time.sleep(sleep_time)
            _RATE_LIMITS[act_id] = (time.time(), 3)
            return
        _RATE_LIMITS[act_id] = (last_reset, remaining - 1)
    else:
        _RATE_LIMITS[act_id] = (now, 3)


def _api_get(act_id: str, access_token: str, endpoint: str, params: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """GET 请求封装，含速率限制和重试逻辑"""
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if params:
        all_params.update(params)

    for attempt in range(3):
        try:
            resp = http_requests.get(url, params=all_params, timeout=30)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)
                if code == 190:
                    return None, f"Token 已过期: {err.get('message', '')}"
                if code in (4, 17, 80000, 80001, 80002, 80004, 80005):
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                return None, f"API 错误 [{code}]: {err.get('message', '')}"
            return data, None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"
    return None, "重试耗尽"


def _api_post(act_id: str, access_token: str, endpoint: str, body: dict = None,
              files: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """POST 请求封装"""
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if body:
        all_params.update(body)

    for attempt in range(3):
        try:
            if files:
                resp = http_requests.post(url, data=all_params, files=files, timeout=60)
            else:
                resp = http_requests.post(url, data=all_params, timeout=30)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)
                if code == 190:
                    return None, f"Token 已过期: {err.get('message', '')}"
                if code in (4, 17, 80000, 80001) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"API 错误 [{code}]: {err.get('message', '')}"
            return data, None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"
    return None, "重试耗尽"


# ---- 投放相关 API ----

def get_ad_account_info(act_id: str, access_token: str) -> Tuple[Optional[Dict], Optional[str]]:
    return _api_get(act_id, access_token, f"/{act_id}",
                    {"fields": "id,name,account_status,currency,timezone_name"})

def get_adsets(act_id: str, access_token: str,
               limit: int = 100) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """拉取一个广告账户下的所有 AdSet 配置"""
    data, err = _api_get(act_id, access_token, f"/{act_id}/adsets", {
        "fields": "id,name,campaign_id,daily_budget,lifetime_budget,bid_strategy,"
                  "billing_event,optimization_goal,targeting,promoted_object,"
                  "start_time,end_time,status,created_time",
        "limit": str(limit)
    })
    if err:
        return None, err
    return data.get("data", []), None

def upload_ad_image(act_id: str, access_token: str,
                    image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """上传图片到 FB 广告账户，返回 image hash"""
    import os
    filename = os.path.basename(image_path)
    with open(image_path, "rb") as f:
        img_data = f.read()
    data, err = _api_post(act_id, access_token, f"/{act_id}/adimages",
                          body={"filename": filename},
                          files={"file": (filename, img_data)})
    if err:
        return None, err
    images = data.get("images", {})
    for k in images:
        return images[k].get("hash", ""), None
    return None, "上传成功但未返回 hash"

def upload_ad_video(act_id: str, access_token: str,
                    video_path: str) -> Tuple[Optional[str], Optional[str]]:
    """上传视频到 FB 广告账户，返回 video ID"""
    import os
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        video_data = f.read()
    data, err = _api_post(act_id, access_token, f"/{act_id}/advideos",
                          body={"title": filename},
                          files={"source": (filename, video_data)})
    if err:
        return None, err
    return data.get("id", ""), None

def create_campaign(act_id: str, access_token: str,
                    name: str, objective: str = "OUTCOME_TRAFFIC",
                    status: str = "PAUSED",
                    special_ad_categories: list = None) -> Tuple[Optional[str], Optional[str]]:
    body = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": special_ad_categories or [],
    }
    data, err = _api_post(act_id, access_token, f"/{act_id}/campaigns", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def create_adset(act_id: str, access_token: str,
                 name: str, campaign_id: str,
                 targeting: dict, daily_budget: int = None,
                 lifetime_budget: int = None,
                 bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
                 billing_event: str = "IMPRESSIONS",
                 optimization_goal: str = "OFFSITE_CONVERSIONS",
                 start_time: str = None, end_time: str = None,
                 promoted_object: dict = None,
                 status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    body = {
        "name": name,
        "campaign_id": campaign_id,
        "targeting": json.dumps(targeting),
        "bid_strategy": bid_strategy,
        "billing_event": billing_event,
        "optimization_goal": optimization_goal,
        "status": status,
    }
    if daily_budget:
        body["daily_budget"] = daily_budget
    if lifetime_budget:
        body["lifetime_budget"] = lifetime_budget
    if start_time:
        body["start_time"] = start_time
    if end_time:
        body["end_time"] = end_time
    if promoted_object:
        body["promoted_object"] = json.dumps(promoted_object)

    data, err = _api_post(act_id, access_token, f"/{act_id}/adsets", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def create_ad(act_id: str, access_token: str,
              name: str, adset_id: str,
              creative_name: str, page_id: str,
              image_hash: str = None, video_id: str = None,
              message: str = "", link_url: str = "",
              status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    """创建广告，含创意"""
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "link": link_url,
            "message": message,
        }
    }
    if image_hash:
        object_story_spec["link_data"]["image_hash"] = image_hash
    if video_id:
        object_story_spec["link_data"]["video_id"] = video_id

    body = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({
            "name": creative_name,
            "object_story_spec": object_story_spec,
        }),
        "status": status,
    }
    data, err = _api_post(act_id, access_token, f"/{act_id}/ads", body=body)
    if err:
        return None, err
    return data.get("id", ""), None

def update_ad_status(act_id: str, access_token: str,
                     ad_id: str, status: str) -> Tuple[bool, Optional[str]]:
    """更新广告状态（ACTIVE / PAUSED）"""
    _, err = _api_post(act_id, access_token, f"/{ad_id}", body={"status": status})
    if err:
        return False, err
    return True, None


# ---- Insights API ----

def get_insights(act_id: str, access_token: str,
                 date_start: str, date_end: str,
                 level: str = "ad",
                 time_increment: int = 1) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """拉取广告投放数据（按日聚合）"""
    fields = (
        "spend,impressions,clicks,ctr,cpm,inline_link_clicks,inline_link_click_ctr,"
        "cost_per_inline_link_click,actions,cost_per_action_type,action_values,"
        "date_start"
    )
    params = {
        "fields": fields,
        "time_range": json.dumps({"since": date_start, "until": date_end}),
        "time_increment": str(time_increment),
        "level": level,
        "limit": "500",
    }
    all_data = []
    url = f"/{act_id}/insights"

    while True:
        data, err = _api_get(act_id, access_token, url, params)
        if err:
            return None, err
        all_data.extend(data.get("data", []))
        paging = data.get("paging", {})
        next_url = paging.get("next")
        if not next_url:
            break
        # 后续分页使用完整 URL
        full_next = f"{next_url}&access_token={access_token}"
        for attempt in range(3):
            try:
                _check_rate(act_id)
                resp = http_requests.get(full_next, timeout=30)
                data = resp.json()
                all_data.extend(data.get("data", []))
                paging = data.get("paging", {})
                next_url = paging.get("next")
                break
            except Exception:
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if not next_url:
            break
        full_next = f"{next_url}&access_token={access_token}"

    return all_data, None


# ---- 账户发现 API（基于 token 自动拉取有权访问的资产） ----

def _simple_get(access_token: str, endpoint: str, params: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """无需 act_id 的简单 GET 请求（用于 /me/* 端点），含重试"""
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if params:
        all_params.update(params)
    for attempt in range(3):
        try:
            resp = http_requests.get(url, params=all_params, timeout=30)
            data = resp.json()
            if "error" in data:
                err = data["error"]
                code = err.get("code", 0)
                if code == 190:
                    return None, f"Token 已过期: {err.get('message', '')}"
                return None, f"API 错误 [{code}]: {err.get('message', '')}"
            return data, None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"
    return None, "重试耗尽"


def discover_ad_accounts(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """获取 token 有权访问的所有广告账户。
    返回 [{id, name, account_status, currency, business_name, ...}]"""
    data, err = _simple_get(access_token, "/me/adaccounts", {
        "fields": "id,name,account_id,account_status,currency,business_name,"
                  "amount_spent,balance,timezone_name,age,"
                  "owner,owner_business,disable_reason",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_businesses(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """获取 token 有权访问的所有商务管理平台 (BM)。
    返回 [{id, name, ...}]"""
    data, err = _simple_get(access_token, "/me/businesses", {
        "fields": "id,name,verification_status,created_time",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_bm_ad_accounts(access_token: str, business_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """获取 BM 下所有客户端广告账户。
    返回 [{id, name, account_id, account_status, ...}]"""
    data, err = _simple_get(access_token, f"/{business_id}/client_ad_accounts", {
        "fields": "id,name,account_id,account_status,currency,amount_spent,balance",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_pages(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """获取 token 有权访问的所有 Facebook 主页。
    返回 [{id, name, category, ...}]"""
    data, err = _simple_get(access_token, "/me/accounts", {
        "fields": "id,name,category,access_token,tasks",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_all_assets(access_token: str) -> Dict[str, Any]:
    """一键发现所有资产：广告账户 + BM + 主页 + BM 下账户"""
    result = {
        "ad_accounts": [],
        "businesses": [],
        "pages": [],
        "bm_ad_accounts": {},  # {business_id: [accounts]}
    }

    # 1. 个人直连的广告账户
    accounts, err = discover_ad_accounts(access_token)
    if accounts is not None:
        result["ad_accounts"] = accounts

    # 2. 主页
    pages, err = discover_pages(access_token)
    if pages is not None:
        result["pages"] = pages

    # 3. BM
    businesses, err = discover_businesses(access_token)
    if businesses is not None:
        result["businesses"] = businesses
        # 4. 每个 BM 下的广告账户
        for bm in businesses:
            bm_id = bm.get("id", "")
            if bm_id:
                bm_accounts, _ = discover_bm_ad_accounts(access_token, bm_id)
                if bm_accounts:
                    result["bm_ad_accounts"][bm_id] = {
                        "name": bm.get("name", ""),
                        "accounts": bm_accounts
                    }

    return result
