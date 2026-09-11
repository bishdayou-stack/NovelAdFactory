# -*- coding: utf-8 -*-
"""验证站点配置读取：默认值、config.json 覆盖、未知站点回落。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))
import scraper

def main():
    sites = scraper.get_sites()
    keys = [s["key"] for s in sites]
    assert keys[:2] == ["a", "b"], f"默认应有 a/b 两个站点，实际 {keys}"
    assert scraper.site_base_url("a") == "https://hw.manage.pingykj.com", scraper.site_base_url("a")
    assert scraper.site_content_url("a") == "https://hw.manage.api.pingykj.com", scraper.site_content_url("a")
    assert scraper.site_base_url("b") == "https://manage.relishnovel.com", scraper.site_base_url("b")
    assert scraper.site_content_url("b") == "https://manage.api.relishnovel.com", scraper.site_content_url("b")
    # 未知站点回落到默认站点，不抛异常
    assert scraper.site_base_url("zzz") == scraper.site_base_url("a")
    assert scraper.site_content_url("") == scraper.site_content_url("a")

    # config.json 漏填 base_url / content_url → 回落 DEFAULT_SITES 同 key 的默认值（不能拼出相对 URL）
    tmp = Path(tempfile.mkdtemp(prefix="sites_cfg_"))
    (tmp / "config.json").write_text(json.dumps({"meta": {"pingykj_sites": [
        {"key": "a", "name": "A站", "base_url": "https://a.example.com"},          # 缺 content_url
        {"key": "b", "name": "B站", "content_url": "https://b.example.com/api"},   # 缺 base_url
        {"key": "c", "name": "C站"},                                               # 都缺，且默认里没有 c
    ]}}), encoding="utf-8")
    orig_base = scraper.BASE_PATH
    scraper.BASE_PATH = tmp
    try:
        ss = {s["key"]: s for s in scraper.get_sites()}
        assert ss["a"]["base_url"] == "https://a.example.com", ss["a"]
        assert ss["a"]["content_url"] == "https://hw.manage.api.pingykj.com", ss["a"]
        assert ss["b"]["content_url"] == "https://b.example.com/api", ss["b"]
        assert ss["b"]["base_url"] == "https://manage.relishnovel.com", ss["b"]
        assert ss["c"]["base_url"] == "" and ss["c"]["content_url"] == "", ss["c"]
    finally:
        scraper.BASE_PATH = orig_base
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 站点配置读取正确（含漏填字段回落默认值）")

if __name__ == "__main__":
    main()
