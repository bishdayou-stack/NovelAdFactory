# -*- coding: utf-8 -*-
"""验证：素材画廊缩略图缓存的保留策略（默认 60 天，管理员可改）。

覆盖：
  1. 默认 60 天；改成别的天数后生效
  2. 只清理「最近 N 天没有统计数据」的图；有近期投放数据的一张都不碰
  3. 同一张图被多个用户共用时只算一次、只删一次
  4. **只删文件 + 清空 local_path，数据库记录保留**（画廊统计不受影响）
  5. 安全：local_path 指向缓存目录之外时绝不删
  6. 预览（dry_run）不删任何东西
  7. 自动清理每天最多跑一次（靠 app_settings 时间戳节流）
  8. 接口：改天数/预览/清理都限管理员
"""
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def add_creative(conn, ad_id, user_id, local_path, cached_days_ago):
    conn.execute(
        "INSERT INTO meta_ad_creatives (ad_id, ad_account, local_path, user_id, synced_at) "
        "VALUES (?, 'act_1', ?, ?, datetime('now', ?))",
        (ad_id, local_path, user_id, f"-{cached_days_ago} days"))


def add_stat(conn, ad_id, user_id, days_ago):
    conn.execute(
        "INSERT INTO meta_ad_stats (date, ad_account, ad_id, spend, user_id) "
        "VALUES (date('now', ?), 'act_1', ?, 10, ?)",
        (f"-{days_ago} days", ad_id, user_id))


def main():
    import database
    import scraper
    import main
    from fastapi.testclient import TestClient
    from main import get_current_user

    tmp = Path(tempfile.mkdtemp(prefix="gal_retention_"))
    database.DB_PATH = tmp / "dashboard.db"
    database.init_db()

    cache = tmp / "static" / "meta_creatives"
    cache.mkdir(parents=True)
    scraper._CREATIVE_CACHE_DIR = cache

    def img(ad_id, kb=100):
        p = cache / f"{ad_id}.jpg"
        p.write_bytes(b"x" * kb * 1024)
        return f"meta_creatives/{ad_id}.jpg"

    outside = tmp / "outside.jpg"          # 缓存目录之外的文件，绝不能删
    outside.write_bytes(b"y" * 1024)

    with database.get_conn() as conn:
        # 老广告：最后统计 100 天前 → 该清
        add_creative(conn, "old_a", 1, img("old_a"), 100)
        add_stat(conn, "old_a", 1, 100)
        # 同一张图被另一个用户也记着 → 只算一次
        add_creative(conn, "old_a", 5, "meta_creatives/old_a.jpg", 100)
        add_stat(conn, "old_a", 5, 100)
        # 从没有过统计数据、缓存也 90 天了 → 该清
        add_creative(conn, "never_ran", 1, img("never_ran"), 90)
        # 老广告但最近还在投（5 天前有数据）→ 保留
        add_creative(conn, "active", 1, img("active"), 200)
        add_stat(conn, "active", 1, 5)
        # 老广告、统计也老，但文件早就不在了 → 只算 missing，不该报错
        add_creative(conn, "gone", 1, "meta_creatives/gone.jpg", 100)
        add_stat(conn, "gone", 1, 100)
        # local_path 指到缓存目录外 → 绝不删
        add_creative(conn, "weird", 1, "../outside.jpg", 100)
        add_stat(conn, "weird", 1, 100)

    # 1) 默认保留天数
    assert scraper.gallery_retention_days() == 60, scraper.gallery_retention_days()
    database.set_app_setting(scraper.GALLERY_RETENTION_KEY, "120")
    assert scraper.gallery_retention_days() == 120
    database.set_app_setting(scraper.GALLERY_RETENTION_KEY, "垃圾值")
    assert scraper.gallery_retention_days() == 60, "非法值应回落默认 60"

    # 2) 预览：不删任何东西
    before = sorted(p.name for p in cache.iterdir())
    r = scraper.cleanup_meta_creatives(60, dry_run=True)
    # stale_count 是「过期的数据库记录数」，deleted_files 是「去重后的文件数」——两个口径
    assert r["stale_count"] == 5, r            # old_a(user=1) + old_a(user=5) + never_ran + gone + weird
    assert r["deleted_files"] == 2, r          # old_a 那张多用户共用只算一次，加 never_ran
    assert r["missing_files"] == 1, r          # gone 的文件不存在
    assert outside.exists() and sorted(p.name for p in cache.iterdir()) == before, "预览不该删文件"

    # 3) 真删
    r = scraper.cleanup_meta_creatives(60, dry_run=False)
    assert r["deleted_files"] == 2, r
    assert not (cache / "old_a.jpg").exists() and not (cache / "never_ran.jpg").exists()
    assert (cache / "active.jpg").exists(), "还有投放数据的图不能被删"
    assert outside.exists(), "缓存目录之外的文件绝不能被删"

    with database.get_conn() as conn:
        rows = dict((r["ad_id"], r) for r in conn.execute(
            "SELECT ad_id, local_path FROM meta_ad_creatives").fetchall())
        assert conn.execute("SELECT COUNT(*) FROM meta_ad_creatives").fetchone()[0] == 6, \
            "数据库记录必须保留（只清 local_path）"
        assert rows["old_a"]["local_path"] == "", "被清理的应清空 local_path"
        assert rows["active"]["local_path"], "没被清理的 local_path 要留着"

    # 4) 自动清理的节流：跑一次写时间戳，24 小时内不再跑
    database.set_app_setting(scraper._GALLERY_CLEANUP_AT_KEY, "")
    scraper._auto_cleanup_gallery_creatives()
    stamp = database.get_app_setting(scraper._GALLERY_CLEANUP_AT_KEY, "")
    assert stamp, "自动清理应写入时间戳"
    with database.get_conn() as conn:      # 造一个新的过期文件，验证第二次不会再动它
        add_creative(conn, "again", 1, img("again"), 100)
        add_stat(conn, "again", 1, 100)
    scraper._auto_cleanup_gallery_creatives()
    assert (cache / "again.jpg").exists(), "24 小时内不该重复跑自动清理"
    # 把时间戳拨回两天前，就应该再跑
    database.set_app_setting(scraper._GALLERY_CLEANUP_AT_KEY,
                             (database.bj_now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"))
    scraper._auto_cleanup_gallery_creatives()
    assert not (cache / "again.jpg").exists(), "超过 24 小时应重新清理"

    # 5) 接口鉴权
    admin = {"id": 1, "username": "admin", "role": "admin"}
    normal = {"id": 7, "username": "u7", "role": "user"}
    c = TestClient(main.app)
    c.app.dependency_overrides[get_current_user] = lambda: normal
    assert c.put("/api/meta/gallery/retention", json={"days": 30}).status_code == 403
    assert c.get("/api/meta/gallery/cleanup/preview").status_code == 403
    assert c.post("/api/meta/gallery/cleanup", json={}).status_code == 403
    assert c.get("/api/meta/gallery/retention").status_code == 200, "读保留天数普通用户也能看"

    c.app.dependency_overrides[get_current_user] = lambda: admin
    assert c.put("/api/meta/gallery/retention", json={"days": 0}).status_code == 400
    assert c.put("/api/meta/gallery/retention", json={"days": 9999}).status_code == 400
    assert c.put("/api/meta/gallery/retention", json={"days": 45}).json()["days"] == 45
    assert c.get("/api/meta/gallery/retention").json()["days"] == 45
    prev = c.get("/api/meta/gallery/cleanup/preview").json()
    assert prev["days"] == 45 and prev["dry_run"] is True, prev

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 画廊缓存保留策略正确（默认60天/可改/只删文件不动记录/自动清理每天一次/接口限管理员）")


if __name__ == "__main__":
    main()
