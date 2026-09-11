# -*- coding: utf-8 -*-
"""验证站点配置读取：默认值、config.json 覆盖、未知站点回落。"""
import json
import sys
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
    print("OK: 站点配置读取正确")

if __name__ == "__main__":
    main()
