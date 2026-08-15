"""Meta (Facebook) Graph API 客户端封装 — 认证、速率限制、请求重试"""
import os
import sys
import time
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlencode

GRAPH_API_BASE = "https://graph.facebook.com"
API_VERSION = "v25.0"

# 速率控制：每账户每秒最多 4 次调用
_RATE_LIMITS: Dict[str, Tuple[float, int]] = {}  # act_id -> (last_reset_time, remaining)


def _get_proxy() -> Optional[str]:
    """获取 Meta API 代理 URL。
    优先读 config.json 的 meta.proxy，否则读取环境变量。
    meta.proxy_enabled 为 false 时明确禁用代理（直连）。"""
    try:
        config_path = Path(__file__).parent / "config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            meta = config.get("meta", {})
            if meta.get("proxy_enabled") is False:
                return None  # 明确禁用代理
            proxy_url = meta.get("proxy", "")
            if proxy_url:
                return proxy_url
    except Exception:
        pass
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
           os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or None


def _http_request(method: str, url: str, params: dict = None, data: dict = None,
                  timeout: int = 30) -> Tuple[Optional[Dict], Optional[str]]:
    """统一的 HTTP 请求（使用 curl，可靠支持代理和远程 DNS 解析）"""
    cmd = ["curl", "-s", "-X", method, "--connect-timeout", str(timeout),
           "--max-time", str(timeout + 15),
           "-w", "\n%{http_code}"]

    proxy = _get_proxy()
    if proxy:
        cmd.extend(["-x", proxy])

    # 添加 query 参数
    if params:
            url = url + "?" + urlencode(params)

    cmd.append(url)

    # POST body
    if data:
        # 如果是 JSON body
        has_files = any(isinstance(v, tuple) for v in data.values())
        if has_files:
            # 文件上传
            for key, (filename, filebytes) in data.items():
                # 用临时文件
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename)
                tmp.write(filebytes if isinstance(filebytes, bytes) else filebytes.encode())
                tmp.close()
                cmd.extend(["-F", f"{key}=@{tmp.name};filename={filename}"])
        else:
            cmd.extend(["-d", urlencode(data)])

    for attempt in range(3):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
            output = result.stdout.strip()
            if not output:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"curl 返回空响应"

            # 分离 body 和 status code（最后一行是 HTTP 状态码）
            lines = output.rsplit("\n", 1)
            if len(lines) == 2:
                body_text, status_code = lines
            else:
                body_text = output
                status_code = "0"

            try:
                code = int(status_code)
            except ValueError:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"无法解析 HTTP 状态码: {status_code}"

            if code >= 500:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"服务器错误 [{code}]"

            try:
                parsed = json.loads(body_text) if body_text.strip() else {}
            except json.JSONDecodeError:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"JSON 解析失败: {body_text[:200]}"

            if "error" in parsed:
                err = parsed["error"]
                err_code = err.get("code", 0)
                err_msg = err.get("message", "")
                if err_code == 190:
                    return None, f"Token 已过期或无效: {err_msg}"
                if err_code in (1, 2, 4, 17, 80000, 80001, 80002, 80004) and attempt < 4:
                    # 错误码 2 (Service temporarily unavailable) 等更久，最多重试 5 次
                    time.sleep(5 if err_code == 2 else (2 ** attempt))
                    continue
                return None, f"API 错误 [{err_code}]: {err_msg}"

            return parsed, None

        except subprocess.TimeoutExpired:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, "请求超时"
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None, f"请求失败: {e}"

    return None, "重试耗尽"


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


# ---- 投放相关 API ----

def get_ad_account_info(act_id: str, access_token: str) -> Tuple[Optional[Dict], Optional[str]]:
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}"
    return _http_request("GET", url, params={
        "access_token": access_token,
        "fields": "id,name,account_status,currency,timezone_name"
    })

def get_adsets(act_id: str, access_token: str,
               limit: int = 100) -> Tuple[Optional[List[Dict]], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/adsets"
    data, err = _http_request("GET", url, params={
        "access_token": access_token,
        "fields": "id,name,campaign_id,daily_budget,lifetime_budget,bid_strategy,"
                  "billing_event,optimization_goal,targeting,promoted_object,"
                  "start_time,end_time,status,created_time",
        "limit": str(limit)
    })
    if err:
        return None, err
    return data.get("data", []), None

def get_ads_with_creative(act_id: str, access_token: str,
                          limit: int = 200) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """获取账户下所有广告及其素材（缩略图/图片/视频）。返回广告列表。"""
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/ads"
    data, err = _http_request("GET", url, params={
        "access_token": access_token,
        "fields": "id,name,adset_id,campaign_id,"
                  "creative{id,thumbnail_url.width(600).height(600),image_url,video_id}",
        "limit": str(limit),
    })
    if err:
        return None, err
    all_data = list(data.get("data", []))
    next_url = data.get("paging", {}).get("next")
    while next_url:
        _check_rate(act_id)
        d, e = _http_request("GET", next_url)
        if e:
            break
        all_data.extend(d.get("data", []))
        next_url = d.get("paging", {}).get("next")
    return all_data, None

def get_entity_statuses(act_id: str, access_token: str,
                        level: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """拉取某层级所有实体的投放状态。level: campaign/adset/ad。
    返回 [{entity_id, effective_status, status, parent_id}]。
    parent_id: campaign 为空，adset 为所属 campaign_id，ad 为所属 adset_id。"""
    edge = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}.get(level)
    if not edge:
        return None, f"未知层级: {level}"
    # 按层级决定请求字段和 parent_id 映射
    if level == "campaign":
        fields = "id,effective_status,status,created_time"
        parent_from = lambda x: ""
    elif level == "adset":
        fields = "id,effective_status,status,campaign_id"
        parent_from = lambda x: x.get("campaign_id", "")
    else:  # ad
        fields = "id,effective_status,status,campaign_id,adset_id"
        parent_from = lambda x: x.get("adset_id", "")
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/{edge}"
    data, err = _http_request("GET", url, params={
        "access_token": access_token,
        "fields": fields,
        "limit": "500",
    })
    if err:
        return None, err
    all_data = list(data.get("data", []))
    next_url = data.get("paging", {}).get("next")
    while next_url:
        _check_rate(act_id)
        d, e = _http_request("GET", next_url)
        if e:
            break
        all_data.extend(d.get("data", []))
        next_url = d.get("paging", {}).get("next")
    out = [{"entity_id": x.get("id", ""),
            "effective_status": x.get("effective_status", ""),
            "status": x.get("status", ""),
            "parent_id": parent_from(x),
            "created_time": x.get("created_time", "")} for x in all_data]
    return out, None

def download_file(url: str, dest_path: str, timeout: int = 30) -> Tuple[bool, Optional[str]]:
    """用 curl 下载文件到本地（代理感知）。返回 (成功, 错误)。"""
    if not url:
        return False, "空 URL"
    cmd = ["curl", "-s", "-L", "--connect-timeout", str(timeout)]
    proxy = _get_proxy()
    if proxy:
        cmd.extend(["-x", proxy])
    cmd.extend(["-o", dest_path, url])
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            return True, None
        return False, "下载后文件为空"
    except subprocess.TimeoutExpired:
        return False, "下载超时"
    except Exception as e:
        return False, f"下载失败: {e}"

def upload_ad_image(act_id: str, access_token: str,
                    image_path: str) -> Tuple[Optional[str], Optional[str]]:
    _check_rate(act_id)
    filename = os.path.basename(image_path)
    with open(image_path, "rb") as f:
        img_data = f.read()
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/adimages"
    data, err = _http_request("POST", url, data={
        "access_token": access_token,
        "filename": filename,
        "file": (filename, img_data),
    })
    if err:
        return None, err
    images = data.get("images", {})
    for k in images:
        return images[k].get("hash", ""), None
    return None, "上传成功但未返回 hash"

def upload_ad_video(act_id: str, access_token: str,
                    video_path: str) -> Tuple[Optional[str], Optional[str]]:
    _check_rate(act_id)
    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        video_data = f.read()
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/advideos"
    data, err = _http_request("POST", url, data={
        "access_token": access_token,
        "title": filename,
        "source": (filename, video_data),
    })
    if err:
        return None, err
    return data.get("id", ""), None

def get_pixels(act_id: str, access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/adspixels"
    data, err = _http_request("GET", url, params={
        "fields": "id,name",
        "access_token": access_token,
    })
    if err:
        return None, err
    return data.get("data", []), None


def get_saved_audiences(act_id: str, access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/saved_audiences"
    data, err = _http_request("GET", url, params={
        "fields": "id,name,targeting",
        "access_token": access_token,
    })
    if err:
        return None, err
    return data.get("data", []), None


def create_campaign(act_id: str, access_token: str,
                    name: str, objective: str = "OUTCOME_TRAFFIC",
                    status: str = "PAUSED",
                    special_ad_categories: list = None,
                    is_adset_budget_sharing_enabled=None) -> Tuple[Optional[str], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/campaigns"
    body = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": json.dumps(special_ad_categories or []),
        "access_token": access_token,
    }
    if is_adset_budget_sharing_enabled is not None:
        body["is_adset_budget_sharing_enabled"] = "true" if is_adset_budget_sharing_enabled else "false"
    data, err = _http_request("POST", url, data=body)
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
                 destination_type: str = "WEBSITE",
                 attribution_spec: dict = None,
                 bid_amount: int = None,
                 status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/adsets"
    body = {
        "name": name,
        "campaign_id": campaign_id,
        "targeting": json.dumps(targeting),
        "bid_strategy": bid_strategy,
        "billing_event": billing_event,
        "optimization_goal": optimization_goal,
        "status": status,
        "access_token": access_token,
    }
    if daily_budget:
        body["daily_budget"] = str(daily_budget)
    if lifetime_budget:
        body["lifetime_budget"] = str(lifetime_budget)
    if start_time:
        body["start_time"] = start_time
    if end_time:
        body["end_time"] = end_time
    if promoted_object:
        body["promoted_object"] = json.dumps(promoted_object)
    body["destination_type"] = destination_type
    if attribution_spec:
        body["attribution_spec"] = json.dumps(attribution_spec)
    if bid_amount:
        body["bid_amount"] = str(bid_amount)

    data, err = _http_request("POST", url, data=body)
    if err:
        return None, err
    return data.get("id", ""), None

def create_ad(act_id: str, access_token: str,
              name: str, adset_id: str,
              creative_name: str, page_id: str,
              image_hash: str = None, video_id: str = None,
              message: str = "", link_url: str = "",
              call_to_action_type: str = "LEARN_MORE",
              status: str = "PAUSED") -> Tuple[Optional[str], Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/ads"
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
    if call_to_action_type:
        object_story_spec["link_data"]["call_to_action"] = {
            "type": call_to_action_type,
            "value": {"link": link_url},
        }

    body = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({
            "name": creative_name,
            "object_story_spec": object_story_spec,
        }),
        "status": status,
        "access_token": access_token,
    }
    data, err = _http_request("POST", url, data=body)
    if err:
        return None, err
    return data.get("id", ""), None

def update_ad_status(act_id: str, access_token: str,
                     ad_id: str, status: str) -> Tuple[bool, Optional[str]]:
    _check_rate(act_id)
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{ad_id}"
    _, err = _http_request("POST", url, data={
        "status": status,
        "access_token": access_token,
    })
    if err:
        return False, err
    return True, None


# ---- Insights API ----

def get_insights(act_id: str, access_token: str,
                 date_start: str, date_end: str,
                 level: str = "ad",
                 time_increment: int = 1) -> Tuple[Optional[List[Dict]], Optional[str]]:
    _check_rate(act_id)
    fields = (
        "spend,impressions,clicks,ctr,cpm,inline_link_clicks,inline_link_click_ctr,"
        "cost_per_inline_link_click,actions,cost_per_action_type,action_values,"
        "date_start"
    )
    # 系列/广告组/广告级需要额外的标识字段用于分组
    if level == "adset":
        fields += ",campaign_id,campaign_name,adset_id,adset_name"
    elif level == "campaign":
        fields += ",campaign_id,campaign_name"
    elif level == "ad":
        fields += ",campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name"
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{act_id}/insights"
    params = {
        "access_token": access_token,
        "fields": fields,
        "time_range": json.dumps({"since": date_start, "until": date_end}),
        "time_increment": str(time_increment),
        "level": level,
        "limit": "500",
    }

    all_data = []
    data, err = _http_request("GET", url, params=params)
    if err:
        return None, err

    all_data.extend(data.get("data", []))
    paging = data.get("paging", {})
    next_url = paging.get("next")

    # 处理分页
    while next_url:
        _check_rate(act_id)
        # 分页 URL 返回的是完整 URL
        data, err = _http_request("GET", next_url)
        if err:
            break
        all_data.extend(data.get("data", []))
        paging = data.get("paging", {})
        next_url = paging.get("next")

    return all_data, None


# ---- 账户发现 API ----

def _simple_get(access_token: str, endpoint: str, params: dict = None) -> Tuple[Optional[Dict], Optional[str]]:
    """无需 act_id 的简单 GET 请求（用于 /me/* 端点）"""
    url = f"{GRAPH_API_BASE}/{API_VERSION}/{endpoint}"
    all_params = {"access_token": access_token}
    if params:
        all_params.update(params)
    return _http_request("GET", url, params=all_params)


def discover_ad_accounts(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    data, err = _simple_get(access_token, "/me/adaccounts", {
        "fields": "id,name,account_id,account_status,currency,business_name,"
                  "amount_spent,balance,timezone_name,"
                  "disable_reason",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_businesses(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    data, err = _simple_get(access_token, "/me/businesses", {
        "fields": "id,name,verification_status,created_time",
        "limit": "200"
    })
    if err:
        return None, err
    return data.get("data", []), None


def discover_bm_ad_accounts(access_token: str, business_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    all_accounts = []
    seen = set()

    for endpoint in [f"/{business_id}/owned_ad_accounts", f"/{business_id}/client_ad_accounts"]:
        data, err = _simple_get(access_token, endpoint, {
            "fields": "id,name,account_id,account_status,currency,amount_spent,balance",
            "limit": "200"
        })
        if err is None and data:
            for acct in data.get("data", []):
                aid = acct.get("id", "")
                if aid and aid not in seen:
                    seen.add(aid)
                    all_accounts.append(acct)

    return all_accounts, None


def discover_pages(access_token: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
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
        "bm_ad_accounts": {},
        "errors": [],
    }

    accounts, err = discover_ad_accounts(access_token)
    if err:
        result["errors"].append(f"广告账户: {err}")
    elif accounts is not None:
        result["ad_accounts"] = accounts

    pages, err = discover_pages(access_token)
    if err:
        result["errors"].append(f"主页: {err}")
    elif pages is not None:
        result["pages"] = pages

    businesses, err = discover_businesses(access_token)
    if err:
        result["errors"].append(f"BM: {err}")
    elif businesses is not None:
        result["businesses"] = businesses
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
