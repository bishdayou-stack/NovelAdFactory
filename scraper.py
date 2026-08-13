"""数据采集模块 — 支持多用户独立 session"""
import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import database
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_PATH = Path(__file__).parent.resolve()
BASE_URL = "https://hw.manage.pingykj.com"

# ====== 代理支持 ======

def _get_proxy_url() -> Optional[str]:
    """从 config.json 读取代理地址，与 meta_api.py 保持一致"""
    try:
        config = json.loads((BASE_PATH / "config.json").read_text(encoding="utf-8"))
        proxy_url = config.get("meta", {}).get("proxy", "")
        if proxy_url:
            return proxy_url
    except Exception:
        pass
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
           os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or None


def _curl_get(url: str, headers: dict = None, cookies: dict = None,
              timeout: int = 30) -> Tuple[Optional[Dict], int, Optional[str]]:
    """用 curl 发送 GET 请求（解决 Python SSL 与代理兼容问题），返回 (json_body, http_code, error)"""
    import subprocess
    import tempfile
    cmd = ["curl", "-s", "-X", "GET", "--connect-timeout", str(timeout), "-w", "\n%{http_code}"]
    proxy = _get_proxy_url()
    if proxy:
        cmd.extend(["-x", proxy])
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        cmd.extend(["-H", f"Cookie: {cookie_str}"])
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        output = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        if not output:
            return None, 0, "curl 返回空"
        lines = output.rsplit("\n", 1)
        body = lines[0] if len(lines) == 2 else output
        code = int(lines[1]) if len(lines) == 2 else 0
        try:
            return json.loads(body) if body else {}, code, None
        except (json.JSONDecodeError, ValueError):
            return body, code, None
    except Exception as e:
        return None, 0, str(e)


def _curl_post(url: str, body: dict = None, headers: dict = None,
               cookies: dict = None, timeout: int = 30) -> Tuple[Optional[Dict], int, Optional[str]]:
    """用 curl 发送 POST 请求"""
    import subprocess
    cmd = ["curl", "-s", "-X", "POST", "--connect-timeout", str(timeout), "-w", "\n%{http_code}"]
    proxy = _get_proxy_url()
    if proxy:
        cmd.extend(["-x", proxy])
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        cmd.extend(["-H", f"Cookie: {cookie_str}"])
    if body:
        cmd.extend(["-H", "Content-Type: application/json"])
        cmd.extend(["-d", json.dumps(body)])
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        output = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        if not output:
            return None, 0, "curl 返回空"
        lines = output.rsplit("\n", 1)
        resp_body = lines[0] if len(lines) == 2 else output
        code = int(lines[1]) if len(lines) == 2 else 0
        try:
            return json.loads(resp_body) if resp_body else {}, code, None
        except (json.JSONDecodeError, ValueError):
            return resp_body, code, None
    except Exception as e:
        return None, 0, str(e)
LOGIN_API_PATH = "/jeecgboot/sys/login"
CAPTCHA_API_PATH = "/jeecgboot/sys/randomImage"

_AD_API_PATH = "/jeecgboot/report/adAttributionReportDaily/list"
_AD_API_PARAMS = "adName="
_ORDER_API_PATH = "/jeecgboot/wallet/financeOrder/list"
_ORDER_API_PARAMS = "column=createTime&order=desc"
_NOVEL_BOOK_PATH = "/jeecgboot/novel/novel/list"
_NOVEL_BOOK_PATH_ALT = "/jeecgboot/novel/bookList"
_PROMOTION_LINK_PATH = "/jeecgboot/ad/campaignLink/list"

# ====== 多用户 session 缓存 ======
_user_sessions: Dict[int, "ScraperSession"] = {}
_pending_logins: Dict[str, "ScraperSession"] = {}  # check_key → 临时 session（验证码用）


# ====== ScraperSession ======

class ScraperSession:
    """每个用户独立的书城登录 session"""

    def __init__(self, user_id: int, pingykj_user: str, pingykj_pass: str):
        self.user_id = user_id
        self.pingykj_username = pingykj_user
        self.pingykj_password = pingykj_pass
        self._token: Optional[str] = None
        self._captcha_cookies: Optional[Dict[str, str]] = None
        self._last_validated: float = 0.0  # token 最后验证时间戳
        self._renew_cooldown: float = 0.0  # 自动续期冷却时间（避免频繁重试）

    # ---- Token 内部管理 ----

    def _has_token(self) -> bool:
        return self._token is not None

    def _clear_token(self) -> None:
        self._token = None
        self._captcha_cookies = None
        self._last_validated = 0.0
        self._renew_cooldown = 0.0

    # ---- 验证码 ----

    def _curl_get_raw(self, url: str, timeout: int = 30) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """用 curl 发送 GET 请求，返回 (raw_bytes, cookies_str, error)"""
        import subprocess, tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.dat')
        tmp.close()
        cmd = ["curl", "-s", "-X", "GET", "--connect-timeout", str(timeout),
               "-o", tmp.name, "-D", "-", "-w", "\n%{http_code}"]
        proxy = _get_proxy_url()
        if proxy:
            cmd.extend(["-x", proxy])
        cmd.append(url)
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
            headers_output = (result.stdout or b"").decode("utf-8", errors="replace")
            raw_bytes = Path(tmp.name).read_bytes()
            Path(tmp.name).unlink(missing_ok=True)
            # extract cookies from headers
            cookies_str = ""
            for line in headers_output.split("\n"):
                if line.lower().startswith("set-cookie:"):
                    parts = line.split(":", 1)[1].strip().split(";")[0]
                    if cookies_str:
                        cookies_str += "; "
                    cookies_str += parts
            return raw_bytes, cookies_str, None
        except Exception as e:
            Path(tmp.name).unlink(missing_ok=True)
            return None, None, str(e)

    def fetch_captcha(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """获取登录验证码。返回 (data_uri, check_key, error_message)"""
        check_key = uuid.uuid4().hex
        url = f"{BASE_URL}{CAPTCHA_API_PATH}/{check_key}"
        raw, cookies_str, err = self._curl_get_raw(url, timeout=15)
        if err:
            return None, None, f"请求失败: {err}"
        if not raw:
            return None, None, "验证码请求返回空"

        self._captcha_cookies = {}
        if cookies_str:
            for part in cookies_str.split("; "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    self._captcha_cookies[k.strip()] = v.strip()

        # 尝试 JSON 格式
        if raw[:1] == b'{':
            try:
                data = json.loads(raw)
                if data.get("success"):
                    result = data.get("result", "")
                    if isinstance(result, dict):
                        img_b64 = result.get("image", "")
                        if img_b64:
                            return f"data:image/png;base64,{img_b64}", check_key, None
                        return None, None, "验证码 result 中无 image"
                    elif isinstance(result, str) and result.startswith("data:"):
                        return result, check_key, None
                    else:
                        return None, None, f"验证码 result 格式未知: {str(result)[:100]}"
                return None, None, f"验证码接口异常: {data.get('message', '')}"
            except (json.JSONDecodeError, ValueError):
                return None, None, "验证码响应解析失败"

        # 原始图片字节
        img_b64 = base64.b64encode(raw).decode("ascii")
        mime = "image/png"
        if raw[:2] == b'\xff\xd8':
            mime = "image/jpeg"
        elif raw[:3] == b'GIF':
            mime = "image/gif"
        data_uri = f"data:{mime};base64,{img_b64}"
        print(f"[Scraper] 验证码获取成功, check_key={check_key[:8]}..., size={len(raw)}bytes")
        return data_uri, check_key, None

    # ---- 登录 ----

    def login(self, captcha: str = "", check_key: str = "") -> Tuple[bool, str]:
        """通过 API 登录，获取 token"""
        body = {"username": self.pingykj_username, "password": self.pingykj_password,
                "captcha": captcha}
        if check_key:
            body["checkKey"] = check_key

        data, code, err = _curl_post(
            f"{BASE_URL}{LOGIN_API_PATH}",
            body=body,
            cookies=self._captcha_cookies if captcha else None,
            timeout=15
        )
        if err:
            return False, f"请求失败: {err}"
        if not isinstance(data, dict):
            return False, "登录响应格式异常"

        if data.get("success"):
            token = data.get("result", {}).get("token", "")
            if token:
                self._token = token
                self._captcha_cookies = None
                print(f"[Scraper] 用户 {self.user_id} 登录成功")
                return True, "登录成功"
            return False, "登录成功但未获取到 token"
        return False, data.get("message", "登录失败")

    def check_valid(self) -> bool:
        """实际验证 token 是否有效（120秒内已验证过则缓存结果，避免频繁请求）"""
        if not self._token:
            return False
        if time.time() - self._last_validated < 120:
            return True  # 120秒内验证过，信任缓存
        ok = self._ping_token()
        self._last_validated = time.time()
        if not ok:
            print(f"[Scraper] 用户 {self.user_id} token 已过期，将清除")
            self._clear_token()
        return ok

    def _ping_token(self) -> bool:
        """用最小开销的 API 调用验证 token 是否仍然有效（pageSize=1）。
        网络临时故障时保持乐观（不因网络抖动就清除 token）。"""
        headers = {"X-Access-Token": self._token}
        url = f"{BASE_URL}{_AD_API_PATH}?pageNo=1&pageSize=1&{_AD_API_PARAMS}"
        data, code, err = _curl_get(url, headers=headers, timeout=10)
        if err:
            # 网络故障，保持乐观（不因临时网络问题清除 token）
            print(f"[Scraper] token 验证网络异常，暂保持: {err}")
            return True
        if not isinstance(data, dict):
            return True  # 非标准响应也保持乐观
        if data.get("success"):
            return True
        if data.get("code") in (401, 403) or "登录" in str(data.get("message", "")) \
                or "token" in str(data.get("message", "")).lower():
            return False  # 确凿的认证失败
        # 其他错误（如无数据、参数错误等），token 本身有效
        return True

    def _renew_token(self) -> bool:
        """尝试无验证码自动重新登录（带冷却，300秒内不重复尝试）"""
        if time.time() - self._renew_cooldown < 300:
            print(f"[Scraper] 用户 {self.user_id} 自动续期冷却中，跳过")
            return False
        self._renew_cooldown = time.time()
        self._clear_token()
        ok, msg = self.login()  # 不带验证码尝试登录
        if ok:
            self._last_validated = time.time()
            print(f"[Scraper] 用户 {self.user_id} token 自动续期成功")
            return True
        print(f"[Scraper] 用户 {self.user_id} 自动续期失败: {msg}")
        return False

    def _ensure_valid_token(self) -> bool:
        """确保 token 有效。先验证，无效则尝试自动续期。返回是否最终有效"""
        if self._token and time.time() - self._last_validated < 120:
            return True  # 缓存有效
        if self.check_valid():
            return True
        # token 无效或不存在，尝试自动重新登录
        if self._renew_token():
            return True
        return False

    def logout(self) -> None:
        self._clear_token()
        print(f"[Scraper] 用户 {self.user_id} 已登出")

    # ---- 数据获取 ----

    def _fetch_with_token(self, api_path: str, api_params: str,
                          page_size: int = 500,
                          date_start: str = None, date_end: str = None,
                          _retry_on_auth: bool = True) -> Tuple[List[Dict], str]:
        """使用 API token 分页获取数据（token 过期时自动续期并重试一次）"""
        if not self._token:
            return [], "未找到登录 token，请先登录"

        headers = {"X-Access-Token": self._token}

        date_filter = ""
        if date_start and date_end:
            date_filter = f"&{date_start}&{date_end}"
        elif date_start:
            date_filter = f"&{date_start}"

        all_records = []
        page_no = 1

        while True:
            url = f"{BASE_URL}{api_path}?pageNo={page_no}&pageSize={page_size}&{api_params}{date_filter}"
            data, code, err = _curl_get(url, headers=headers, timeout=30)
            if err:
                if all_records:
                    print(f"[Scraper] 第{page_no}页请求异常: {err}")
                    break
                return [], f"请求失败: {err}"

            if not isinstance(data, dict):
                if all_records:
                    break
                return [], "API 响应格式异常"

            if not data.get("success"):
                msg = data.get("message", "未知错误")
                # 检测到 token 过期 → 尝试自动续期并重试
                if data.get("code") in (401, 403) or "登录" in str(msg) or "token" in str(msg).lower():
                    if not _retry_on_auth:
                        self._clear_token()
                        return all_records if all_records else [], f"登录已失效: {msg}"
                    # 清除旧 token，尝试重新登录
                    self._clear_token()
                    print(f"[Scraper] 用户 {self.user_id} token 失效，尝试自动续期...")
                    if self._renew_token():
                        # 续期成功，重试当前请求（不再递归重试）
                        print(f"[Scraper] 用户 {self.user_id} 续期成功，重试数据请求...")
                        renewed_records, renewed_err = self._fetch_with_token(
                            api_path, api_params, page_size=page_size,
                            date_start=date_start, date_end=date_end,
                            _retry_on_auth=False
                        )
                        if renewed_err:
                            return all_records if all_records else [], f"续期后重试失败: {renewed_err}"
                        return renewed_records, ""
                    self._clear_token()
                    return all_records if all_records else [], f"登录已失效，自动续期失败: {msg}"
                if all_records:
                    print(f"[Scraper] 第{page_no}页API异常: {msg}")
                    break
                return [], f"API 返回失败: {msg}"

            # 请求成功 → 更新验证时间戳（token 有效）
            self._last_validated = time.time()

            res = data.get("result", {})
            records = res.get("records", [])
            total = res.get("total", 0)
            all_records.extend(records)

            if page_no == 1:
                print(f"[Scraper] 第1页: {len(records)} 条 (总计 {total})")

            total_pages = (total + page_size - 1) // page_size
            if page_no >= total_pages:
                break

            page_no += 1
            if page_no % 10 == 0 or page_no == total_pages:
                print(f"[Scraper] 第{page_no}/{total_pages}页 (累计 {len(all_records)})")

        return all_records, ""


# ====== 用户 session 管理 ======

def _get_or_create_session(user_id: int) -> Tuple[Optional[ScraperSession], str]:
    """获取用户的 ScraperSession。优先用缓存，验证 token 有效性；过期则自动重新登录。"""
    if user_id in _user_sessions:
        session = _user_sessions[user_id]
        if session._ensure_valid_token():
            return session, ""
        # 缓存过期且自动续期失败，清除
        print(f"[Scraper] 用户 {user_id} session 无效，清除缓存")
        database.set_pingykj_offline(user_id)
        del _user_sessions[user_id]

    creds = database.get_user_pingykj_credentials(user_id)
    if not creds or not creds.get("username"):
        return None, "未配置书城凭据，请在数据看板页面设置"

    # 尝试自动重新登录（不带验证码，大多数情况下可直接登录）
    session = ScraperSession(user_id, creds["username"], creds["password"])
    ok, msg = session.login()
    if ok:
        _user_sessions[user_id] = session
        print(f"[Scraper] 用户 {user_id} 自动重新登录成功")
        return session, ""

    # 自动登录失败（可能需要验证码），提示用户手动操作
    return None, f"书城登录会话已过期: {msg}。请在数据看板页面重新设置凭据（需输入验证码）"


def keepalive_all_sessions() -> Dict[int, bool]:
    """对所有活跃 session 做 token 保活验证（供定时器调用）。
    返回 {user_id: is_valid} 字典。"""
    results = {}
    for user_id, session in list(_user_sessions.items()):
        try:
            valid = session._ensure_valid_token()
            results[user_id] = valid
            if not valid:
                print(f"[Scraper] 保活失败 user={user_id}，将从缓存清除")
                database.set_pingykj_offline(user_id)
                del _user_sessions[user_id]
        except Exception as e:
            print(f"[Scraper] 保活异常 user={user_id}: {e}")
            results[user_id] = False
    return results


def clear_user_session(user_id: int) -> None:
    """清除用户 session（切换凭据时使用）"""
    if user_id in _user_sessions:
        _user_sessions[user_id].logout()
        del _user_sessions[user_id]


# ====== 广告数据采集 ======

def _aggregate_ad_rows(raw_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], Dict] = {}
    for row in raw_rows:
        date = str(row.get("statDate") or row.get("日期") or row.get("date") or row.get("时间") or "")
        account = str(row.get("adAccountId") or row.get("adAccountName") or row.get("广告账户") or row.get("ad_account") or row.get("账户") or "")
        if not date or not account:
            continue

        try:
            spend = float(row.get("spend") or row.get("广告消耗") or row.get("消耗") or 0)
        except (ValueError, TypeError):
            spend = 0.0
        try:
            revenue = float(row.get("purchaseValues") or row.get("revenue") or row.get("收入金额") or row.get("收入") or 0)
        except (ValueError, TypeError):
            revenue = 0.0
        try:
            impressions = int(float(row.get("impressions") or row.get("展示量") or row.get("曝光") or 0))
        except (ValueError, TypeError):
            impressions = 0
        try:
            clicks = int(float(row.get("clicks") or row.get("点击量") or row.get("点击") or 0))
        except (ValueError, TypeError):
            clicks = 0
        try:
            purchases = int(float(row.get("purchase") or row.get("purchases") or row.get("转化数") or 0))
        except (ValueError, TypeError):
            purchases = 0

        key = (date, account)
        if key not in groups:
            groups[key] = {"date": date, "ad_account": account, "total_spend": 0, "total_revenue": 0,
                           "ad_count": 0, "impressions": 0, "clicks": 0, "purchases": 0,
                           "extra_data": {}, "link_ids": set()}
        g = groups[key]
        g["total_spend"] += spend
        g["total_revenue"] += revenue
        g["ad_count"] += 1
        g["impressions"] += impressions
        g["clicks"] += clicks
        g["purchases"] += purchases
        # 收集推广链接 ID
        lid = str(row.get("linkId") or "")
        if lid:
            g["link_ids"].add(lid)
        if not g["extra_data"]:
            g["extra_data"] = {k: v for k, v in row.items()}

    # 把 set 转成逗号分隔字符串存到 extra_data
    for g in groups.values():
        if g["link_ids"]:
            g["extra_data"]["link_ids"] = ",".join(g["link_ids"])
        del g["link_ids"]

    return list(groups.values())


def _aggregate_novel_from_ads(raw_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """按 (日期, linkId) 聚合广告数据，用于书籍维度统计"""
    groups: Dict[Tuple[str, str], Dict] = {}
    for row in raw_rows:
        date = str(row.get("statDate") or row.get("日期") or row.get("date") or row.get("时间") or "")
        link_id = str(row.get("linkId") or "")
        if not date or not link_id:
            continue
        try:
            spend = float(row.get("spend") or row.get("广告消耗") or row.get("消耗") or 0)
        except (ValueError, TypeError):
            spend = 0.0
        try:
            revenue = float(row.get("purchaseValues") or row.get("revenue") or row.get("收入金额") or row.get("收入") or 0)
        except (ValueError, TypeError):
            revenue = 0.0
        try:
            impressions = int(float(row.get("impressions") or row.get("展示量") or row.get("曝光") or 0))
        except (ValueError, TypeError):
            impressions = 0
        try:
            clicks = int(float(row.get("clicks") or row.get("点击量") or row.get("点击") or 0))
        except (ValueError, TypeError):
            clicks = 0
        try:
            purchases = int(float(row.get("purchase") or row.get("purchases") or row.get("转化数") or 0))
        except (ValueError, TypeError):
            purchases = 0
        key = (date, link_id)
        if key not in groups:
            groups[key] = {"date": date, "link_id": link_id, "spend": 0.0, "revenue": 0.0,
                           "impressions": 0, "clicks": 0, "purchases": 0}
        g = groups[key]
        g["spend"] += spend
        g["revenue"] += revenue
        g["impressions"] += impressions
        g["clicks"] += clicks
        g["purchases"] += purchases
    return list(groups.values())


def resolve_promotion_links(session: "ScraperSession", link_ids: set) -> Dict[str, Dict[str, str]]:
    """通过 pingykj API 解析推广链接 → novel_id + novel_name"""
    result = {}
    if not link_ids:
        return result
    for link_id in link_ids:
        if not link_id:
            continue
        # 尝试多种查询参数：campaignLink 实体主键可能是 id，也可能是 linkId
        for param in (f"id={link_id}", f"linkId={link_id}"):
            try:
                records, err = session._fetch_with_token(
                    _PROMOTION_LINK_PATH,
                    param,
                    page_size=5
                )
                if err or not records:
                    continue
                for rec in records:
                    nid = str(rec.get("novelId") or rec.get("novel_id") or rec.get("bookId") or
                              rec.get("subjectId") or rec.get("book_id") or "")
                    name = str(rec.get("novelName") or rec.get("novel_name") or rec.get("bookName") or
                               rec.get("book_name") or rec.get("subjectName") or
                               rec.get("name") or rec.get("title") or "")
                    if nid:
                        result[link_id] = {"novel_id": nid, "novel_name": name}
                        break
                if link_id in result:
                    break
            except Exception:
                continue
    return result


def sync_ads(user_id: int) -> Tuple[int, str]:
    session, err = _get_or_create_session(user_id)
    if not session:
        return 0, err

    try:
        today = time.strftime("%Y-%m-%d")
        last_date = database.get_last_sync_date("ads", user_id)
        date_start = None
        date_end = None

        if last_date:
            from datetime import datetime as dt, timedelta
            overlap = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_start = f"statDate_begin={overlap}"
            date_end = f"statDate_end={today}"
            print(f"[Scraper] 增量同步广告(user={user_id}): {overlap} ~ {today}")
        else:
            print(f"[Scraper] 首次全量同步广告(user={user_id})...")

        records, err = session._fetch_with_token(
            _AD_API_PATH, _AD_API_PARAMS,
            page_size=500, date_start=date_start, date_end=date_end
        )
        if err:
            return 0, err
        if not records:
            return 0, "广告数据为空"

        seen = set()
        unique = []
        for r in records:
            rid = str(r.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                unique.append(r)
        print(f"[Scraper] 广告去重后: {len(unique)} 条")

        raw_count = database.save_raw_ad_stats(unique, user_id)
        print(f"[Scraper] 原始广告数据已保存: {raw_count} 条")

        aggregated = _aggregate_ad_rows(unique)
        count = database.upsert_ad_stats(aggregated, user_id)

        # 按推广链接聚合小说维度统计（不影响主流程）
        try:
            novel_rows = _aggregate_novel_from_ads(unique)
            # 收集未知 linkId，尝试解析
            all_link_ids = set(nr["link_id"] for nr in novel_rows if nr["link_id"])
            unknown_links = {lid for lid in all_link_ids if not database.get_novel_id_by_link(lid)}
            if unknown_links:
                resolved = resolve_promotion_links(session, unknown_links)
                for lid, info in resolved.items():
                    database.upsert_promotion_link_map(lid, info["novel_id"], info["novel_name"], user_id)
                    if resolved:
                        print(f"[Scraper] 解析到 {len(resolved)} 个推广链接→书籍映射")
            novel_count = 0
            for nr in novel_rows:
                nid = nr["link_id"]
                novel_info = database.get_novel_by_link(nid)
                if novel_info:
                    real_nid, real_name = novel_info[0], novel_info[1]
                else:
                    real_nid, real_name = nid, ""
                database.upsert_novel_daily_stats(
                    date=nr["date"], novel_id=real_nid,
                    novel_name=real_name,
                    spend=nr["spend"], revenue=nr["revenue"],
                    impressions=nr["impressions"], clicks=nr["clicks"],
                    purchases=nr["purchases"],
                    order_count=0, order_amount=0,
                    user_id=user_id
                )
                novel_count += 1
            if novel_count:
                print(f"[Scraper] 书籍维度聚合: {novel_count} 条")
        except Exception as _e:
            print(f"[Scraper] 书籍维度聚合失败(不影响主流程): {_e}")

        if count > 0:
            database.set_last_sync_date("ads", today, user_id)
        return count, ""

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0, str(e)


def reset_sync_state(user_id: int) -> None:
    """清除用户的同步状态，下次同步时将全量拉取"""
    for sync_type in ["ads", "orders"]:
        database.delete_sync_state(sync_type, user_id)
    # 也清除 Meta 同步状态
    accounts = database.get_meta_accounts(user_id)
    for a in accounts:
        database.delete_sync_state(f"meta_{a['act_id']}", user_id)


# ====== 订单数据采集 ======

def _parse_order_rows(raw_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    results = []
    for row in raw_rows:
        order_id = str(row.get("orderNo") or row.get("订单ID") or row.get("order_id") or row.get("订单号") or "")
        if not order_id:
            continue
        try:
            amount = float(row.get("amount") or row.get("金额") or row.get("订单金额") or 0)
        except (ValueError, TypeError):
            amount = 0.0

        results.append({
            "order_id": order_id,
            "order_date": str(row.get("zoneTime") or row.get("createTime") or row.get("日期") or row.get("order_date") or row.get("下单时间") or ""),
            "amount": amount,
            "status": str(row.get("status_dictText") or row.get("状态") or row.get("status") or ""),
            "ad_account": str(row.get("campaignName") or row.get("adAccountId") or row.get("广告账户") or row.get("ad_account") or ""),
            "extra_data": {k: v for k, v in row.items() if k not in
                          ("orderNo", "订单ID", "order_id", "订单号", "amount", "金额", "订单金额",
                           "createTime", "zoneTime", "日期", "order_date", "下单时间", "status_dictText", "状态", "status",
                           "campaignName", "adAccountId", "广告账户", "ad_account")},
            "customer_info": json.dumps({"novelName": row.get("novelName", ""), "novelId": row.get("novelId", ""),
                                         "chapterNo": row.get("chapterNo", ""), "adName": row.get("adName", ""),
                                         "campaignName": row.get("campaignName", "")}, ensure_ascii=False)
        })
    return results


def sync_orders(user_id: int) -> Tuple[int, str]:
    session, err = _get_or_create_session(user_id)
    if not session:
        return 0, err

    try:
        today = time.strftime("%Y-%m-%d")
        last_date = database.get_last_sync_date("orders", user_id)
        date_start = None
        date_end = None

        if last_date:
            from datetime import datetime as dt, timedelta
            overlap = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_start = f"createTime_begin={overlap}"
            date_end = f"createTime_end={today}"
            print(f"[Scraper] 增量同步订单(user={user_id}): {overlap} ~ {today}")
        else:
            print(f"[Scraper] 首次全量同步订单(user={user_id})...")

        records, err = session._fetch_with_token(
            _ORDER_API_PATH, _ORDER_API_PARAMS,
            page_size=500, date_start=date_start, date_end=date_end
        )
        if err:
            return 0, err
        if not records:
            return 0, "订单数据为空"

        seen = set()
        unique = []
        for r in records:
            oid = str(r.get("orderNo", ""))
            if oid and oid not in seen:
                seen.add(oid)
                unique.append(r)
        print(f"[Scraper] 订单去重后: {len(unique)} 条")

        raw_count = database.save_raw_orders(unique, user_id)
        print(f"[Scraper] 原始订单数据已保存: {raw_count} 条")

        orders = _parse_order_rows(unique)
        count = database.upsert_orders(orders, user_id)

        if count > 0:
            database.set_last_sync_date("orders", today, user_id)
        return count, ""

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0, str(e)


# ====== 小说爬取 ======

_CONTENT_API = "https://hw.manage.api.pingykj.com"
_CONTENT_PATH = "/novel/novel/getChaptersContent"


def _parse_novel_books(raw_rows: List[Dict]) -> List[Dict[str, Any]]:
    """解析书籍列表 API 返回的原始行"""
    STATUS_MAP = {"1": "连载", "2": "完结", "0": "下架"}
    EXCLUSIVE_MAP = {"0": "非独享", "1": "独享7天"}
    RECOMMEND_MAP = {"0": "否", "1": "是"}
    books = []
    for row in raw_rows:
        novel_id = str(row.get("id") or "")
        if not novel_id:
            continue
        raw_status = str(row.get("status", ""))
        raw_exclusive = str(row.get("exclusive7d", "0"))
        raw_recommend = str(row.get("recommend", "0"))
        books.append({
            "novel_id": novel_id,
            "novel_name": row.get("title") or row.get("novelName") or row.get("name") or "",
            "author": row.get("author") or "",
            "cover_url": row.get("coverUrl") or row.get("cover") or "",
            "status": str(row.get("status_dictText") or STATUS_MAP.get(raw_status, raw_status)),
            "category": row.get("category_dictText") or str(row.get("category") or ""),
            "intro": (row.get("description") or row.get("intro") or ""),
            "total_chapters": row.get("chapterCount") or row.get("totalChapters") or 0,
            "create_time": row.get("createTime") or "",
            "book_ad_spend": row.get("bookAdSpend") or 0,
            "promotion_link_count": row.get("promotionLinkCount") or 0,
            "source": row.get("source_dictText") or str(row.get("source") or ""),
            "region": row.get("regionId_dictText") or str(row.get("regionId") or ""),
            "tags": row.get("tags_dictText") or str(row.get("tags") or ""),
            "recommend": RECOMMEND_MAP.get(raw_recommend, raw_recommend),
            "exclusive_status": EXCLUSIVE_MAP.get(raw_exclusive, raw_exclusive),
            "create_by": row.get("createBy") or "",
            "word_count": row.get("wordCount") or 0,
            "collect_num": row.get("collectNum") or 0,
            "locale_code": row.get("localeCode") or "",
            "raw_json": json.dumps(row, ensure_ascii=False),
        })
    return books


def _parse_chapters_from_html(html_text: str, novel_id: str) -> List[Dict[str, Any]]:
    """从章节内容 HTML 中解析出各章节。"""
    import re
    from html.parser import HTMLParser

    CHAPTER_RE = re.compile(r'(?:chapter|ch\.?|第)\s*\d+', re.IGNORECASE)

    class ChapterParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.chapters = []
            self._current_chapter = None
            self._in_bold_p = False
            self._in_para = False
            self._current_p_text = []
            self._current_p_is_bold = False

        def handle_starttag(self, tag, attrs):
            tag_l = tag.lower()
            if tag_l in ("h1", "h2", "h3", "h4"):
                if self._finalize_current_para():
                    pass
                self._in_heading = True
                self._heading_tag = tag_l
                self._heading_text = []
            elif tag_l == "p":
                self._in_para = True
                self._current_p_text = []
                self._current_p_is_bold = False
                for k, v in attrs:
                    if k == "style" and re.search(r'font-weight\s*:\s*bold', v, re.IGNORECASE):
                        self._current_p_is_bold = True
                        break

        def handle_endtag(self, tag):
            tag_l = tag.lower()
            if tag_l in ("h1", "h2", "h3", "h4"):
                self._in_heading = False
                title = "".join(getattr(self, '_heading_text', [])).strip()
                if title and CHAPTER_RE.search(title):
                    self._start_new_chapter(title)
                self._heading_tag = None
                self._heading_text = []
            elif tag_l == "p":
                self._in_para = False
                self._finalize_current_para()

        def handle_data(self, data):
            if getattr(self, '_in_heading', False):
                self._heading_text.append(data)
            elif self._in_para:
                self._current_p_text.append(data)

        def _finalize_current_para(self):
            text = "".join(self._current_p_text).strip()
            if not text:
                return False
            if self._current_p_is_bold and CHAPTER_RE.search(text):
                self._start_new_chapter(text)
                return True
            if self._current_chapter is not None:
                self._current_chapter.setdefault("_parts", []).append(text)
            return True

        def _start_new_chapter(self, title):
            if self._current_chapter is not None:
                self._finalize_chapter()
            self._current_chapter = {
                "novel_id": novel_id,
                "_title": title,
                "_parts": [],
            }

        def _finalize_chapter(self):
            if self._current_chapter is None:
                return
            content = "\n\n".join(self._current_chapter.get("_parts", []))
            chapter_no = len(self.chapters) + 1
            self.chapters.append({
                "novel_id": novel_id,
                "chapter_no": chapter_no,
                "chapter_name": self._current_chapter.get("_title", f"Chapter {chapter_no}"),
                "content": content,
                "word_count": len(content.split()) if content else 0,
                "raw_json": json.dumps({"title": self._current_chapter.get("_title", ""), "chapter_no": chapter_no}, ensure_ascii=False),
            })
            self._current_chapter = None

        def finalize(self):
            self._finalize_current_para()
            self._finalize_chapter()

    parser = ChapterParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    parser.finalize()

    if not parser.chapters:
        from html.parser import HTMLParser as P
        class FallbackParser(P):
            def __init__(self):
                super().__init__()
                self.texts = []
                self._in_p = False
            def handle_starttag(self, tag, attrs):
                if tag == "p":
                    self._in_p = True
            def handle_endtag(self, tag):
                if tag == "p":
                    self._in_p = False
            def handle_data(self, data):
                if self._in_p:
                    t = data.strip()
                    if t:
                        self.texts.append(t)
        fp = FallbackParser()
        fp.feed(html_text)
        content = "\n\n".join(fp.texts) if fp.texts else html_text
        parser.chapters = [{
            "novel_id": novel_id,
            "chapter_no": 1,
            "chapter_name": "第1章",
            "content": content,
            "word_count": len(content.split()) if content else 0,
            "raw_json": "{}",
        }]

    return parser.chapters


def _fetch_novel_books(api_path: str, session: ScraperSession,
                       date_start: str = None, date_end: str = None) -> Tuple[List[Dict], str]:
    """从书籍列表 API 分页获取记录（支持增量日期过滤）"""
    token = session._token
    headers = {"X-Access-Token": token} if token else {}
    all_raw = []
    page_no = 1
    date_filter = ""
    if date_start and date_end:
        date_filter = f"&updateTime_begin={date_start}&updateTime_end={date_end}"
    elif date_start:
        date_filter = f"&updateTime_begin={date_start}"
    while True:
        url = f"{BASE_URL}{api_path}?pageNo={page_no}&pageSize=500{date_filter}"
        data, code, err = _curl_get(url, headers=headers, timeout=30)
        if err:
            return [], f"请求异常: {err}"
        if code >= 400:
            return [], f"HTTP {code}"
        if not isinstance(data, dict):
            break
        res = data.get("result", {}) if isinstance(data, dict) else {}
        records = res.get("records", []) if isinstance(res, dict) else []
        total = res.get("total", 0) if isinstance(res, dict) else 0
        all_raw.extend(records)
        total_pages = (total + 499) // 500
        if page_no >= total_pages or not records:
            break
        page_no += 1
    if not all_raw:
        return [], "API 返回空数据"
    return all_raw, ""


def sync_novel_books(user_id: int = None, full_sync: bool = False) -> Tuple[int, str]:
    """同步书籍列表。
    full_sync=False: 增量同步（按 updateTime 过滤，仅拉取近期更新的书籍）
    full_sync=True:  全量同步（不过滤日期，拉取全部书籍并更新消耗数据）"""
    session = None
    if user_id:
        session, _ = _get_or_create_session(user_id)

    if not session:
        active_users = database.list_active_users_with_credentials()
        for u in active_users:
            session, _ = _get_or_create_session(u["id"])
            if session:
                break

    if not session:
        return 0, "没有可用的书城登录凭据"

    today = time.strftime("%Y-%m-%d")
    date_start = None
    date_end = None
    if full_sync:
        print(f"[Scraper] 全量同步书籍（更新消耗与订单数据）...")
    else:
        last_date = database.get_last_sync_date("novels")
        if last_date:
            from datetime import datetime as dt, timedelta
            overlap = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_start = overlap
            date_end = today
            print(f"[Scraper] 增量同步书籍: {overlap} ~ {today}")
        else:
            print(f"[Scraper] 首次全量同步书籍...")

    err_msgs = []
    for api_path in (_NOVEL_BOOK_PATH, _NOVEL_BOOK_PATH_ALT):
        all_raw, err = _fetch_novel_books(api_path, session,
                                          date_start=date_start, date_end=date_end)
        if all_raw:
            books = _parse_novel_books(all_raw)
            count = database.upsert_novel_books(books)
            if count > 0 or full_sync:
                database.set_last_sync_date("novels", today)
            # 保存当日消耗快照（用于计算区间消耗增量）
            snap_count = database.save_novel_spend_snapshots(books)
            if snap_count > 0:
                print(f"[Scraper] 小说消耗快照已保存: {snap_count} 本")
            return count, ""
        if err:
            err_msgs.append(f"{api_path}: {err}")

    if not err_msgs:
        return 0, "未能获取书籍列表"
    return 0, "; ".join(err_msgs)


def sync_missing_chapters(user_id: int = None) -> Tuple[int, str]:
    """检查并同步章节缺失的书籍内容"""
    with database.get_conn() as conn:
        # 找出 total_chapters > 已存储章节数的书籍
        rows = conn.execute("""
            SELECT nb.novel_id, nb.novel_name, nb.total_chapters,
                   COALESCE((SELECT COUNT(*) FROM novel_chapters nc WHERE nc.novel_id = nb.novel_id), 0) AS stored_chapters
            FROM novel_books nb
            WHERE nb.total_chapters > COALESCE((SELECT COUNT(*) FROM novel_chapters nc WHERE nc.novel_id = nb.novel_id), 0)
            ORDER BY nb.total_chapters - stored_chapters DESC
        """).fetchall()

    if not rows:
        return 0, ""

    synced = 0
    for r in rows:
        novel_id = r["novel_id"]
        novel_name = r["novel_name"]
        missing = r["total_chapters"] - r["stored_chapters"]
        print(f"[Novel] 章节缺失: {novel_name} ({novel_id}) 需补 {missing} 章")
        try:
            count, err = sync_novel_chapters(novel_id)
            if not err and count > 0:
                synced += 1
                print(f"[Novel] {novel_name} 章节同步完成: +{count} 章")
            elif err:
                print(f"[Novel] {novel_name} 章节同步失败: {err}")
        except Exception as e:
            print(f"[Novel] {novel_name} 章节同步异常: {e}")

    return synced, ""


def sync_novel_chapters(novel_id: str) -> Tuple[int, str]:
    """同步单本书的章节内容"""
    try:
        from urllib.parse import quote
        url = f"{_CONTENT_API}{_CONTENT_PATH}?novelId={quote(novel_id, safe='')}&viewFree=false"
        data, code, err = _curl_get(url, timeout=60)
        if err:
            return 0, err
        if code >= 400:
            return 0, f"HTTP {code}"
        html_text = data if isinstance(data, str) else json.dumps(data)
        chapters = _parse_chapters_from_html(html_text, novel_id)
        count = database.upsert_novel_chapters(chapters)
        return count, ""
    except Exception as e:
        return 0, str(e)


def sync_all_novel_content(novel_id: str = None, concurrency: int = 8) -> Dict[str, Any]:
    """同步章节内容，可指定 novel_id 或全部"""
    if novel_id:
        ids = [novel_id]
    else:
        ids = database.get_all_novel_ids()

    result = {"total": len(ids), "books": {}, "concurrency": concurrency}
    lock = __import__('threading').Lock()
    completed = [0]

    def _sync_one(nid):
        count, err = sync_novel_chapters(nid)
        with lock:
            completed[0] += 1
            if not err and count > 0:
                print(f"[Novel {completed[0]}/{len(ids)}] {nid}: {count} 章已同步")
            elif err:
                print(f"[Novel {completed[0]}/{len(ids)}] {nid}: 失败 - {err}")
        return nid, count, err

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_sync_one, nid): nid for nid in ids}
        for future in as_completed(futures):
            nid, count, err = future.result()
            result["books"][nid] = {"chapters": count, "error": err}

    return result


# ---- 兼容旧版 API（无 user_id 时使用 user_id=1） ----

def fetch_captcha() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """兼容旧版：使用 user_id=1 的 session"""
    return fetch_captcha_for_user(1)


def fetch_captcha_for_user(user_id: int) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """为指定用户获取验证码（使用已保存的凭据）"""
    creds = database.get_user_pingykj_credentials(user_id)
    if not creds or not creds.get("username"):
        return None, None, "请先设置书城凭据"
    return fetch_captcha_with_creds(creds["username"], creds["password"])


def fetch_captcha_with_creds(username: str, password: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """用指定凭据获取验证码，缓存 session 供后续登录复用"""
    session = ScraperSession(0, username, password)
    data_uri, check_key, err = session.fetch_captcha()
    if check_key and session._captcha_cookies:
        _pending_logins[check_key] = session  # 缓存临时 session，登录时复用
    return data_uri, check_key, err


def login_via_api(username: str, password: str,
                  captcha: str = "", check_key: str = "") -> Tuple[bool, str]:
    """兼容旧版"""
    return login_via_api_for_user(1, username, password, captcha, check_key)


def login_via_api_for_user(user_id: int, username: str, password: str,
                           captcha: str = "", check_key: str = "") -> Tuple[bool, str]:
    """用指定凭据登录书城，复用验证码获取时的 session（保持 cookies 关联）"""
    # 优先复用验证码获取时缓存的 session（cookies 关联 checkKey→验证码）
    if check_key and check_key in _pending_logins:
        session = _pending_logins.pop(check_key)
        session.user_id = user_id
        # 注意：session 上已有 _captcha_cookies，login 方法会用到
    else:
        session = ScraperSession(user_id, username, password)

    ok, msg = session.login(captcha, check_key)
    if ok:
        _user_sessions[user_id] = session
    return ok, msg


# ---- 主同步入口 ----

def run_full_sync(user_id: int = None) -> Dict[str, Any]:
    """数据看板同步（仅广告 + 订单，不包含小说和 Meta）"""
    uid = user_id or 1
    print(f"[Scraper] run_full_sync 开始, user_id={uid}")

    session, sess_err = _get_or_create_session(uid)
    if not session:
        return {"success": False, "login_required": True, "message": sess_err}

    result = {"success": True, "login_required": False,
              "ads": {"count": 0, "error": ""},
              "orders": {"count": 0, "error": ""},
              "novels": {"count": 0, "error": ""}}

    ads_count, ads_err = sync_ads(uid)
    result["ads"]["count"] = ads_count
    result["ads"]["error"] = ads_err
    database.log_sync("ads", "success" if not ads_err else "failed", ads_count, ads_err, uid)

    orders_count, orders_err = sync_orders(uid)
    result["orders"]["count"] = orders_count
    result["orders"]["error"] = orders_err
    database.log_sync("orders", "success" if not orders_err else "failed", orders_count, orders_err, uid)

    # 增量同步书籍列表
    novels_count, novels_err = sync_novel_books(uid)
    result["novels"]["count"] = novels_count
    result["novels"]["error"] = novels_err

    login_lost = ("登录已失效" in ads_err or "登录已失效" in orders_err)
    if login_lost:
        clear_user_session(uid)
        result["login_required"] = True

    all_failed = ads_err and orders_err and novels_err
    result["success"] = not all_failed

    failed_parts = []
    if ads_err:
        failed_parts.append(f"广告: {ads_err}")
    if orders_err:
        failed_parts.append(f"订单: {orders_err}")
    if novels_err:
        failed_parts.append(f"小说: {novels_err}")
    if failed_parts:
        result["message"] = "部分同步失败: " + "; ".join(failed_parts)
    else:
        result["message"] = f"同步完成，广告 {ads_count} 条，订单 {orders_count} 条，小说 {novels_count} 本"

    return result


# ---- Meta Ads Insights 数据同步 ----

import json as _json2
import meta_api
from datetime import datetime as dt, timedelta


def _load_default_token() -> Optional[str]:
    """从 config.json 读取 Meta 默认 access token"""
    try:
        config = _json2.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
        return config.get("meta", {}).get("default_access_token", "")
    except Exception:
        return ""


def _sync_one_meta_account_breakdown(act_id: str, access_token: str,
                                     from_date: str, to_date: str,
                                     user_id: int,
                                     rows: list = None) -> int:
    """同步单个账户的「广告级」Insights（含所属系列/广告组信息），
    同时聚合出 广告级(meta_ad_stats) 与 广告组级(meta_adset_stats)。
    系列级由查询时对广告组 GROUP BY 得出。返回广告级写入行数。
    若传入 rows 则复用已有数据，避免重复 API 调用。"""
    if rows is None:
        rows, err = meta_api.get_insights(act_id, access_token, from_date, to_date, level="ad")
        if err or not rows:
            return 0
    elif not rows:
        return 0
    from collections import defaultdict

    def _purchases(r):
        p = 0
        for action in (r.get("actions") or []):
            if action.get("action_type") == "purchase":
                p += int(float(action.get("value", 0) or 0))
        return p

    def _purchase_value(r):
        v = 0.0
        for av in (r.get("action_values") or []):
            if av.get("action_type") == "purchase":
                v += float(av.get("value", 0) or 0)
        return v

    def _count_action(r, action_types):
        """统计指定 action_type 的 actions 总数"""
        c = 0
        for action in (r.get("actions") or []):
            if action.get("action_type") in action_types:
                c += int(float(action.get("value", 0) or 0))
        return c

    # 加入购物车：只用 omni_add_to_cart（Meta 去重后的值），避免同一事件多名称重复计数
    _ATC_TYPES = {"omni_add_to_cart"}
    # 订阅
    _SUB_TYPES = {"subscribe"}

    adset_agg = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0,
                                      "purchases": 0, "purchase_value": 0.0,
                                      "add_to_cart": 0, "subscribe_count": 0})
    ad_agg = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0,
                                   "purchases": 0, "purchase_value": 0.0,
                                   "add_to_cart": 0, "subscribe_count": 0})
    for r in rows:
        d = r.get("date_start", "")
        if not d:
            continue
        spend = float(r.get("spend", 0) or 0)
        impr = int(float(r.get("impressions", 0) or 0))
        clk = int(float(r.get("clicks", 0) or 0))
        pur = _purchases(r)
        pv = _purchase_value(r)
        atc = _count_action(r, _ATC_TYPES)
        sub = _count_action(r, _SUB_TYPES)
        adset_id = r.get("adset_id", "")
        if adset_id:
            a = adset_agg[(d, adset_id)]
            a["date_start"] = d; a["adset_id"] = adset_id
            a["adset_name"] = r.get("adset_name", "")
            a["campaign_id"] = r.get("campaign_id", ""); a["campaign_name"] = r.get("campaign_name", "")
            a["spend"] += spend; a["impressions"] += impr; a["clicks"] += clk
            a["purchases"] += pur; a["purchase_value"] += pv
            a["add_to_cart"] += atc; a["subscribe_count"] += sub
        ad_id = r.get("ad_id", "")
        if not ad_id:
            ad_id = f"_missing_ad_{adset_id}_{d}"  # 兜底：无 ad_id 也保留，确保数据完整
        b = ad_agg[(d, ad_id)]
        b["date_start"] = d; b["ad_id"] = ad_id; b["ad_name"] = r.get("ad_name", "") or ad_id
        b["adset_id"] = adset_id; b["adset_name"] = r.get("adset_name", "")
        b["campaign_id"] = r.get("campaign_id", ""); b["campaign_name"] = r.get("campaign_name", "")
        b["spend"] += spend; b["impressions"] += impr; b["clicks"] += clk
        b["purchases"] += pur; b["purchase_value"] += pv
        b["add_to_cart"] += atc; b["subscribe_count"] += sub

    database.upsert_meta_adset_stats(act_id, [dict(v) for v in adset_agg.values()], user_id)
    return database.upsert_meta_ad_stats(act_id, [dict(v) for v in ad_agg.values()], user_id)


def _sync_meta_creatives(act_id: str, access_token: str, from_date: str, user_id: int) -> int:
    """拉取账户广告素材，仅对近期有投放数据的广告下载缩略图到本地缓存。返回缓存数量。"""
    ad_ids = set(database.get_meta_ad_ids_with_stats(act_id, user_id, since_date=from_date))
    if not ad_ids:
        return 0
    # 检查是否已有缓存，有则跳过 API 调用
    cache_dir = Path(__file__).parent / "static" / "meta_creatives"
    cache_dir.mkdir(parents=True, exist_ok=True)
    missing = [aid for aid in ad_ids if not (cache_dir / f"{aid}.jpg").exists()]
    if not missing:
        return len(ad_ids)
    ads, err = meta_api.get_ads_with_creative(act_id, access_token)
    if err or not ads:
        return 0
    cached = 0
    for ad in ads:
        ad_id = ad.get("id", "")
        if not ad_id or ad_id not in ad_ids:
            continue
        creative = ad.get("creative") or {}
        thumb = creative.get("thumbnail_url") or ""
        image_url = creative.get("image_url") or ""
        video_id = creative.get("video_id") or ""
        # 优先下载高清原图，缺失（如视频广告）则退回放大后的缩略图
        download_url = image_url or thumb
        local_rel = ""
        if download_url:
            fname = f"{ad_id}.jpg"
            dest = cache_dir / fname
            if dest.exists() and dest.stat().st_size > 0:
                local_rel = f"meta_creatives/{fname}"
            else:
                ok, _e = meta_api.download_file(download_url, str(dest))
                if ok:
                    local_rel = f"meta_creatives/{fname}"
                    cached += 1
        database.upsert_meta_ad_creative({
            "ad_id": ad_id, "ad_account": act_id, "ad_name": ad.get("name", ""),
            "adset_id": ad.get("adset_id", ""), "campaign_id": ad.get("campaign_id", ""),
            "thumbnail_url": thumb, "image_url": image_url, "video_id": video_id,
            "local_path": local_rel,
        }, user_id)
    return cached


def _sync_meta_statuses(act_id: str, access_token: str, user_id: int) -> int:
    """拉取该账户 系列/广告组/广告 三层的投放状态，写入 meta_entity_status。返回写入总数。
    若最近 1 小时内已同步过该账户状态则跳过。"""
    # 检查上次同步时间
    last_sync = database.get_meta_status_last_sync(act_id, user_id)
    if last_sync:
        try:
            last_dt = dt.strptime(last_sync, "%Y-%m-%d %H:%M:%S")
            if (dt.utcnow() - last_dt).total_seconds() < 3600:
                return 0  # 1小时内同步过，跳过
        except Exception:
            pass
    total = 0
    for level in ("campaign", "adset", "ad"):
        rows, err = meta_api.get_entity_statuses(act_id, access_token, level)
        if err or not rows:
            continue
        for r in rows:
            r["ad_account"] = act_id
        total += database.upsert_meta_entity_statuses(level, rows, user_id)
    return total


def _sync_one_meta_account(act_id: str, access_token: str,
                           user_id: int,
                           scope: str = "all") -> Tuple[str, int, str]:
    """同步单个 Meta 账户的 Insights 数据，返回 (act_id, count, error)。
    scope: all(全部), meta(仅看板insights), campaign(仅广告系列breakdown+statuses)"""
    t_start = time.time()
    last_date = database.get_meta_sync_state(act_id, user_id)
    today = dt.utcnow().strftime("%Y-%m-%d")

    if last_date:
        from_date = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
        # 确保不早于 90 天前
        min_date = (dt.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
        if from_date < min_date:
            from_date = min_date
        if from_date > today:
            return act_id, 0, ""
        # 增量同步：广告系列侧从上次同步日拉取
        campaign_from = from_date
    else:
        from_date = (dt.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        # 首次同步：广告系列侧只拉近7天
        campaign_from = (dt.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    # 先检查账户是否已被 Meta 停用
    info, info_err = meta_api.get_ad_account_info(act_id, access_token)
    if info and not info_err:
        raw_status = info.get("account_status", 0)
        # 存储 Meta 真实状态
        status_label = {1: "活跃", 2: "已停用", 100: "待关闭", 101: "已关闭"}.get(raw_status, str(raw_status))
        database.update_meta_account_meta_status(act_id, status_label, user_id)
        if raw_status in (2, 100, 101):
            database.update_meta_account_status(act_id, "paused", user_id)
            return act_id, 0, f"Meta端已停用(状态{raw_status})，跳过同步"
    elif info_err and ("expired" in str(info_err).lower() or "invalid" in str(info_err).lower()):
        pass

    t0 = time.time()
    rows = None; err = None
    if scope in ("all", "meta"):
        rows, err = meta_api.get_insights(act_id, access_token, from_date, today)
        print(f"  [meta] {act_id} insights({from_date}~{today}): {len(rows) if rows else 0}行, {time.time()-t0:.1f}s")
    else:
        print(f"  [meta] {act_id} insights: 跳过(campaign模式)")
    if err:
        return act_id, 0, err
    if scope in ("all", "meta") and not rows:
        database.set_meta_sync_state(act_id, today, user_id)
        if scope == "meta":
            return act_id, 0, ""

    # 按日期聚合 + 写入（仅 all/meta 模式）
    count = 0
    if scope in ("all", "meta") and rows:
        from collections import defaultdict
        aggregated = defaultdict(lambda: {
            "spend": 0.0, "impressions": 0, "clicks": 0,
            "ctr": 0.0, "cpm": 0.0, "cpc": 0.0,
            "inline_link_clicks": 0, "inline_link_click_ctr": 0.0,
            "cost_per_inline_link_click": 0.0,
            "actions": [], "cost_per_action_type": [],
            "action_values": [], "purchase_value": 0.0,
        })
        for r in rows:
            d = r.get("date_start", "")
            if not d:
                continue
            agg = aggregated[d]
            agg["date_start"] = d
            agg["spend"] += float(r.get("spend", 0) or 0)
            agg["impressions"] += int(float(r.get("impressions", 0) or 0))
            agg["clicks"] += int(float(r.get("clicks", 0) or 0))
            agg["inline_link_clicks"] += int(float(r.get("inline_link_clicks", 0) or 0))
            for action in (r.get("actions") or []):
                action_type = action.get("action_type", "")
                value = float(action.get("value", 0) or 0)
                found = False
                for a in agg["actions"]:
                    if a.get("action_type") == action_type:
                        a["value"] = str(float(a.get("value", 0) or 0) + value)
                        found = True
                        break
                if not found:
                    agg["actions"].append({"action_type": action_type, "value": str(value)})
            for cpa in (r.get("cost_per_action_type") or []):
                at = cpa.get("action_type", "")
                v = float(cpa.get("value", 0) or 0)
                found = False
                for a in agg["cost_per_action_type"]:
                    if a.get("action_type") == at:
                        a["value"] = str(float(a.get("value", 0) or 0) + v)
                        found = True
                        break
                if not found:
                    agg["cost_per_action_type"].append({"action_type": at, "value": str(v)})
            for av in (r.get("action_values") or []):
                at = av.get("action_type", "")
                v = float(av.get("value", 0) or 0)
                found = False
                for a in agg["action_values"]:
                    if a.get("action_type") == at:
                        a["value"] = str(float(a.get("value", 0) or 0) + v)
                        found = True
                        break
                if not found:
                    agg["action_values"].append({"action_type": at, "value": str(v)})

        # 重新计算派生指标
        agg_rows = []
        for d, a in aggregated.items():
            if a["impressions"] > 0:
                a["ctr"] = round(a["clicks"] / a["impressions"] * 100, 4) if a["clicks"] > 0 else 0.0
                a["cpm"] = round(a["spend"] / a["impressions"] * 1000, 2)
            if a["clicks"] > 0:
                a["cpc"] = round(a["spend"] / a["clicks"], 4)
            if a["inline_link_clicks"] > 0:
                a["inline_link_click_ctr"] = round(a["inline_link_clicks"] / a["impressions"] * 100, 4) if a["impressions"] > 0 else 0.0
                a["cost_per_inline_link_click"] = round(a["spend"] / a["inline_link_clicks"], 4)
            agg_rows.append(dict(a))

        count = database.upsert_meta_insights(act_id, agg_rows, user_id)
    database.set_meta_sync_state(act_id, today, user_id)
    # 广告系列侧：breakdown + 素材 + 状态（all/campaign 模式）
    if scope in ("all", "campaign"):
        t0 = time.time()
        try:
            _sync_one_meta_account_breakdown(act_id, access_token, campaign_from, today, user_id, rows)
        except Exception as _e:
            print(f"[meta breakdown] {act_id} 明细同步失败: {_e}")
        print(f"  [meta] {act_id} breakdown({campaign_from}~{today}): {time.time()-t0:.1f}s")
        t0 = time.time()
        try:
            _sync_meta_creatives(act_id, access_token, campaign_from, user_id)
        except Exception as _e:
            print(f"[meta creative] {act_id} 素材同步失败: {_e}")
        print(f"  [meta] {act_id} creatives: {time.time()-t0:.1f}s")
        t0 = time.time()
        try:
            _sync_meta_statuses(act_id, access_token, user_id)
        except Exception as _e:
            print(f"[meta status] {act_id} 状态同步失败: {_e}")
        print(f"  [meta] {act_id} statuses: {time.time()-t0:.1f}s")
    print(f"  [meta] {act_id} 总耗时: {time.time()-t_start:.1f}s")
    # 同步完成后保存 KPI 快照，用于阶段统计
    if count > 0:
        try:
            database.save_account_snapshot(act_id, user_id)
        except Exception as _e:
            print(f"[meta snapshot] {act_id} 快照保存失败: {_e}")
        # 广告系列快照
        if scope in ("all", "campaign"):
            try:
                n = database.save_campaign_snapshots(act_id, user_id)
                print(f"  [meta snapshot] {act_id} 系列快照: {n} 条")
            except Exception as _e:
                print(f"[meta snapshot] {act_id} 系列快照保存失败: {_e}")
    return act_id, count, ""


def sync_all_meta_insights(user_id: int = None, concurrency: int = 1) -> Dict[str, Any]:
    """逐个同步所有 active 状态的 Meta 账户数据（串行，避免触发风控）"""
    uid = user_id or 1
    default_token = _load_default_token()
    accounts = database.get_meta_accounts(uid)

    for a in accounts:
        aid = a.get("act_id", "")
        if aid and not aid.startswith("act_"):
            a["act_id"] = "act_" + aid

    active_accounts = []
    for a in accounts:
        if a.get("status") != "active":
            continue
        if a.get("user_id") is None:
            continue  # 未分配用户，跳过
        # Token 优先级: BM level → 账户 level → 全局默认
        bm_id = a.get("bm_id", "")
        token = (database.get_bm_token(bm_id) if bm_id else "") or a.get("access_token") or default_token
        if token:
            active_accounts.append((a["act_id"], token))

    if not active_accounts:
        if default_token:
            return {"success": False, "total": 0, "accounts": {},
                    "message": "系统级 token 已配置但未找到活跃的 Meta 账户。请在「账户配置」Tab 添加广告账户（act_XXXXX）。"}
        return {"success": False, "total": 0, "accounts": {},
                "message": "没有活跃的 Meta 账户，且未配置默认 Access Token。请在「Meta 数据」Tab 填写配置。"}

    result = {"success": True, "total": len(active_accounts), "accounts": {}}
    total_count = 0
    errors = []

    # 并发同步（每个账户内部已有速率限制）
    from concurrent.futures import as_completed
    workers = max(1, min(concurrency, len(active_accounts)))
    print(f"[Meta同步] 并行度 {workers}, 共 {len(active_accounts)} 个账户")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for act_id, token in active_accounts:
            f = pool.submit(_sync_one_meta_account, act_id, token, uid)
            futures[f] = act_id
        for i, f in enumerate(as_completed(futures)):
            act_id = futures[f]
            try:
                a_id, count, err = f.result()
            except Exception as e:
                count, err = 0, str(e)
            result["accounts"][act_id] = {"count": count, "error": err}
            total_count += count
            if err:
                errors.append(f"{act_id}: {err}")
                print(f"[Meta同步] {i+1}/{len(active_accounts)} ✗ {act_id}: {err[:80]}")
            else:
                print(f"[Meta同步] {i+1}/{len(active_accounts)} ✓ {act_id}: {count}条")

    succeeded = len(active_accounts) - len(errors)
    result["total_count"] = total_count
    result["succeeded"] = succeeded
    result["failed"] = len(errors)
    if errors:
        result["message"] = f"{succeeded}/{len(active_accounts)} 个账户成功，共 {total_count} 条。失败: {'; '.join(errors)}"
    else:
        result["message"] = f"全部 {len(active_accounts)} 个账户同步完成，共 {total_count} 条"

    return result
