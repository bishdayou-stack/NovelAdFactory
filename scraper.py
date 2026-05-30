import base64
import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import requests as http_requests

import database

BASE_PATH = Path(__file__).parent.resolve()
BASE_URL = "https://hw.manage.pingykj.com"
LOGIN_API_PATH = "/jeecgboot/sys/login"
CAPTCHA_API_PATH = "/jeecgboot/sys/randomImage"
AUTH_TOKEN_PATH = BASE_PATH / "data" / "auth_token.json"

# 已验证的 API 端点
_AD_API_PATH = "/jeecgboot/report/adAttributionReportDaily/list"
_AD_API_PARAMS = "adName="
_ORDER_API_PATH = "/jeecgboot/wallet/financeOrder/list"
_ORDER_API_PARAMS = "column=createTime&order=desc"
_NOVEL_BOOK_PATH = "/jeecgboot/novel/novel/list"
_NOVEL_BOOK_PATH_ALT = "/jeecgboot/novel/bookList"

# Token 缓存
_api_token: Optional[str] = None
# 验证码 session cookies（关联 captcha 和 login）
_captcha_cookies: Optional[Dict[str, str]] = None


# ---- Token 管理 ----

def _save_auth_token(token: str) -> None:
    global _api_token
    _api_token = token
    AUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_TOKEN_PATH.write_text(json.dumps({
        "token": token, "saved_at": time.time()
    }, ensure_ascii=False), encoding="utf-8")


def _load_auth_token() -> Optional[str]:
    global _api_token
    if _api_token:
        return _api_token
    if AUTH_TOKEN_PATH.exists():
        try:
            data = json.loads(AUTH_TOKEN_PATH.read_text(encoding="utf-8"))
            if time.time() - data.get("saved_at", 0) < 7200:
                _api_token = data["token"]
                return _api_token
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _clear_auth_token() -> None:
    global _api_token
    _api_token = None
    AUTH_TOKEN_PATH.unlink(missing_ok=True)


def _has_api_token() -> bool:
    return _load_auth_token() is not None


# ---- 登录管理 ----

def fetch_captcha() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """获取登录验证码。返回 (data_uri, check_key, error_message)"""
    global _captcha_cookies
    check_key = uuid.uuid4().hex
    try:
        session = http_requests.Session()
        resp = session.get(
            f"{BASE_URL}{CAPTCHA_API_PATH}/{check_key}",
            timeout=10
        )
        if resp.status_code != 200:
            return None, None, f"验证码请求失败 (status={resp.status_code})"

        _captcha_cookies = dict(session.cookies.get_dict())
        content_type = resp.headers.get("Content-Type", "")

        # JeecgBoot 可能直接返回图片字节，也可能包在 JSON 里
        if "json" in content_type or resp.content[:1] == b'{':
            # JSON 格式：{"success":true, "result":"data:image/jpg;base64,..."}
            try:
                data = resp.json()
                if data.get("success"):
                    result = data.get("result", "")
                    if isinstance(result, dict):
                        # 有些版本 result 是对象，内含 image 字段
                        img_b64 = result.get("image", "")
                        if img_b64:
                            data_uri = f"data:image/png;base64,{img_b64}"
                        else:
                            return None, None, "验证码 result 中无 image"
                    elif isinstance(result, str) and result.startswith("data:"):
                        # result 直接就是 data URI
                        data_uri = result
                    else:
                        return None, None, f"验证码 result 格式未知: {str(result)[:100]}"
                    print(f"[Scraper] 验证码(JSON)获取成功, check_key={check_key[:8]}...")
                    return data_uri, check_key, None
                return None, None, f"验证码接口异常: {data.get('message', '')}"
            except (json.JSONDecodeError, ValueError):
                return None, None, "验证码响应解析失败"
        else:
            # 直接返回图片字节
            img_b64 = base64.b64encode(resp.content).decode("ascii")
            mime = "image/png"
            if resp.content[:2] == b'\xff\xd8':
                mime = "image/jpeg"
            elif resp.content[:3] == b'GIF':
                mime = "image/gif"
            data_uri = f"data:{mime};base64,{img_b64}"
            print(f"[Scraper] 验证码(RAW)获取成功, check_key={check_key[:8]}..., size={len(resp.content)}bytes")
            return data_uri, check_key, None

    except http_requests.RequestException as e:
        return None, None, f"请求失败: {e}"


def do_logout() -> None:
    global _captcha_cookies
    _clear_auth_token()
    _captcha_cookies = None
    print("[Scraper] 已登出，所有凭据已清除")


def check_session_valid() -> bool:
    return _has_api_token()


def login_via_api(username: str, password: str,
                  captcha: str = "", check_key: str = "") -> Tuple[bool, str]:
    """通过 API 直接登录，获取 token。captcha/check_key 可选，不需要验证码时留空即可"""
    global _captcha_cookies
    try:
        body = {"username": username, "password": password, "captcha": captcha}
        if check_key:
            body["checkKey"] = check_key

        # 携带验证码的 session cookies（服务器用它关联 checkKey → 验证码）
        cookies = _captcha_cookies if captcha else None
        resp = http_requests.post(
            f"{BASE_URL}{LOGIN_API_PATH}",
            json=body,
            timeout=15,
            headers={"Content-Type": "application/json"},
            cookies=cookies
        )
        data = resp.json()
        if data.get("success"):
            token = data.get("result", {}).get("token", "")
            if token:
                _save_auth_token(token)
                _captcha_cookies = None  # 登录成功，清除验证码 session
                database.save_session_cookies(
                    json.dumps({"token": token, "login_method": "api"}, ensure_ascii=False)
                )
                return True, "登录成功，token 已保存"
            return False, "登录成功但未获取到 token"
        return False, data.get("message", "登录失败")
    except http_requests.RequestException as e:
        return False, f"请求失败: {e}"
    except Exception as e:
        return False, str(e)


# ---- 数据获取 ----

def _fetch_with_token(api_path: str, api_params: str, page_size: int = 500,
                      date_start: str = None, date_end: str = None) -> Tuple[List[Dict], str]:
    """使用 API token 通过 requests 直接调用后端 API 分页获取数据"""
    token = _load_auth_token()
    if not token:
        return [], "未找到登录 token，请先登录"

    headers = {
        "X-Access-Token": token,
        "Content-Type": "application/json",
    }

    date_filter = ""
    if date_start and date_end:
        date_filter = f"&{date_start}&{date_end}"
    elif date_start:
        date_filter = f"&{date_start}"

    all_records = []
    page_no = 1

    while True:
        url = f"{BASE_URL}{api_path}?pageNo={page_no}&pageSize={page_size}&{api_params}{date_filter}"
        try:
            resp = http_requests.get(url, headers=headers, timeout=30)
            data = resp.json()
        except http_requests.RequestException as e:
            if all_records:
                print(f"[Scraper] 第{page_no}页请求异常: {e}")
                break
            return [], f"请求失败: {e}"

        if not data.get("success"):
            msg = data.get("message", "未知错误")
            if data.get("code") == 401 or "登录" in str(msg) or "token" in str(msg).lower():
                _clear_auth_token()
                return all_records if all_records else [], "登录已失效，请重新登录"
            if all_records:
                print(f"[Scraper] 第{page_no}页API异常: {msg}")
                break
            return [], f"API 返回失败: {msg}"

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


# ---- 广告数据采集 ----

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

        key = (date, account)
        if key not in groups:
            groups[key] = {"date": date, "ad_account": account, "total_spend": 0, "total_revenue": 0,
                           "ad_count": 0, "impressions": 0, "clicks": 0, "extra_data": {}}
        g = groups[key]
        g["total_spend"] += spend
        g["total_revenue"] += revenue
        g["ad_count"] += 1
        g["impressions"] += impressions
        g["clicks"] += clicks
        if not g["extra_data"]:
            g["extra_data"] = {k: v for k, v in row.items()}

    return list(groups.values())


def sync_ads() -> Tuple[int, str]:
    if not _has_api_token():
        return 0, "未登录，请先登录"

    try:
        today = time.strftime("%Y-%m-%d")
        last_date = database.get_last_sync_date("ads")
        date_start = None
        date_end = None

        if last_date:
            from datetime import datetime as dt, timedelta
            overlap = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_start = f"statDate_begin={overlap}"
            date_end = f"statDate_end={today}"
            print(f"[Scraper] 增量同步广告: {overlap} ~ {today}")
        else:
            print("[Scraper] 首次全量同步广告...")

        records, err = _fetch_with_token(
            _AD_API_PATH, _AD_API_PARAMS,
            page_size=500, date_start=date_start, date_end=date_end
        )
        if err:
            return 0, err
        if not records:
            return 0, "广告数据为空"

        # 去重
        seen = set()
        unique = []
        for r in records:
            rid = str(r.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                unique.append(r)
        print(f"[Scraper] 广告去重后: {len(unique)} 条")

        # 保存原始数据 + 聚合
        raw_count = database.save_raw_ad_stats(unique)
        print(f"[Scraper] 原始广告数据已保存: {raw_count} 条")

        aggregated = _aggregate_ad_rows(unique)
        count = database.upsert_ad_stats(aggregated)

        database.set_last_sync_date("ads", today)
        return count, ""

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0, str(e)


# ---- 订单数据采集 ----

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


def sync_orders() -> Tuple[int, str]:
    if not _has_api_token():
        return 0, "未登录，请先登录"

    try:
        today = time.strftime("%Y-%m-%d")
        last_date = database.get_last_sync_date("orders")
        date_start = None
        date_end = None

        if last_date:
            from datetime import datetime as dt, timedelta
            overlap = (dt.strptime(last_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            date_start = f"createTime_begin={overlap}"
            date_end = f"createTime_end={today}"
            print(f"[Scraper] 增量同步订单: {overlap} ~ {today}")
        else:
            print("[Scraper] 首次全量同步订单...")

        records, err = _fetch_with_token(
            _ORDER_API_PATH, _ORDER_API_PARAMS,
            page_size=500, date_start=date_start, date_end=date_end
        )
        if err:
            return 0, err
        if not records:
            return 0, "订单数据为空"

        # 去重
        seen = set()
        unique = []
        for r in records:
            oid = str(r.get("orderNo", ""))
            if oid and oid not in seen:
                seen.add(oid)
                unique.append(r)
        print(f"[Scraper] 订单去重后: {len(unique)} 条")

        raw_count = database.save_raw_orders(unique)
        print(f"[Scraper] 原始订单数据已保存: {raw_count} 条")

        orders = _parse_order_rows(unique)
        count = database.upsert_orders(orders)

        database.set_last_sync_date("orders", today)
        return count, ""

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0, str(e)


# ---- 小说爬取 ----

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
            "status": STATUS_MAP.get(raw_status, raw_status),
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
    """从章节内容 HTML 中解析出各章节。
    支持两种格式：
    1. <h1>-<h4> 标签标记章节标题
    2. <p style=\"font-weight: bold\"> 标记章节标题（pingykj 平台实际格式）
    """
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
                # heading 标签：视为章节边界
                if self._finalize_current_para():
                    pass
                self._in_heading = True
                self._heading_tag = tag_l
                self._heading_text = []
            elif tag_l == "p":
                self._in_para = True
                self._current_p_text = []
                self._current_p_is_bold = False
                # 检测 <p style="...font-weight:bold..."> 或 <p style="...font-weight: bold...">
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
                # 加粗段落 = 章节标题
                self._start_new_chapter(text)
                return True
            if self._current_chapter is not None:
                self._current_chapter.setdefault("_parts", []).append(text)
            return True

        def _start_new_chapter(self, title):
            # 保存前一章
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

    # 如果没有解析到章节，整体作为一个章节
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


def _fetch_novel_books(api_path: str) -> Tuple[List[Dict], str]:
    """从书籍列表 API 分页获取所有记录，返回 (records, error)"""
    token = _load_auth_token()
    headers = {"X-Access-Token": token, "Content-Type": "application/json"} if token else {}
    all_raw = []
    page_no = 1
    last_status = 0
    last_body = ""
    while True:
        try:
            url = f"{BASE_URL}{api_path}?pageNo={page_no}&pageSize=500"
            resp = http_requests.get(url, headers=headers, timeout=30)
            last_status = resp.status_code
            if resp.status_code >= 400:
                last_body = resp.text[:300]
                break
            data = resp.json()
            res = data.get("result", {}) if isinstance(data, dict) else {}
            records = res.get("records", []) if isinstance(res, dict) else []
            total = res.get("total", 0) if isinstance(res, dict) else 0
            all_raw.extend(records)
            total_pages = (total + 499) // 500
            if page_no >= total_pages or not records:
                break
            page_no += 1
        except Exception as e:
            return [], f"请求异常: {str(e)}"
    if last_status >= 400:
        return [], f"HTTP {last_status}: {last_body[:200]}"
    if not all_raw:
        return [], f"API 返回空数据 (status={last_status})"
    return all_raw, ""


def sync_novel_books() -> Tuple[int, str]:
    """同步书籍列表，返回 (books_count, error_message)"""
    err_msgs = []
    for api_path in (_NOVEL_BOOK_PATH, _NOVEL_BOOK_PATH_ALT):
        all_raw, err = _fetch_novel_books(api_path)
        if all_raw:
            books = _parse_novel_books(all_raw)
            count = database.upsert_novel_books(books)
            return count, ""
        if err:
            err_msgs.append(f"{api_path}: {err}")

    if not err_msgs:
        return 0, "未能获取书籍列表（需先在数据看板登录）"
    return 0, "; ".join(err_msgs)


def sync_novel_chapters(novel_id: str) -> Tuple[int, str]:
    """同步单本书的章节内容，返回 (chapters_count, error_message)"""
    try:
        from urllib.parse import quote
        url = f"{_CONTENT_API}{_CONTENT_PATH}?novelId={quote(novel_id, safe='')}&viewFree=false"
        resp = http_requests.get(url, timeout=60)
        if resp.status_code >= 400:
            return 0, f"HTTP {resp.status_code}"
        chapters = _parse_chapters_from_html(resp.text, novel_id)
        count = database.upsert_novel_chapters(chapters)
        return count, ""
    except Exception as e:
        return 0, str(e)


def sync_all_novel_content(novel_id: str = None, concurrency: int = 8) -> Dict[str, Any]:
    """同步章节内容，可指定 novel_id 或全部，使用并发加速"""
    if novel_id:
        ids = [novel_id]
    else:
        ids = database.get_all_novel_ids()

    result = {"total": len(ids), "books": {}, "concurrency": concurrency}
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    lock = threading.Lock()
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


# ---- 主同步入口 ----

def run_full_sync() -> Dict[str, Any]:
    if not check_session_valid():
        return {"success": False, "login_required": True, "message": "登录会话已失效，请重新登录"}

    result = {"success": True, "login_required": False,
              "ads": {"count": 0, "error": ""},
              "orders": {"count": 0, "error": ""},
              "novels": {"count": 0, "error": ""}}

    ads_count, ads_err = sync_ads()
    result["ads"]["count"] = ads_count
    result["ads"]["error"] = ads_err
    database.log_sync("ads", "success" if not ads_err else "failed", ads_count, ads_err)

    orders_count, orders_err = sync_orders()
    result["orders"]["count"] = orders_count
    result["orders"]["error"] = orders_err
    database.log_sync("orders", "success" if not orders_err else "failed", orders_count, orders_err)

    novels_count, novels_err = sync_novel_books()
    result["novels"]["count"] = novels_count
    result["novels"]["error"] = novels_err
    database.log_sync("novels", "success" if not novels_err else "failed", novels_count, novels_err)

    login_lost = ("登录已失效" in ads_err or "登录已失效" in orders_err or "登录已失效" in novels_err)
    if login_lost:
        _clear_auth_token()
        result["login_required"] = True

    all_failed = ads_err and orders_err
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
