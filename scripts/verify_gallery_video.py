# -*- coding: utf-8 -*-
"""验证：素材画廊视频下载到本地后本地播放（不再访问 Facebook）。

覆盖：
  1. 同步时从创意里取到 story 视频 id 并落库（creative.video_id 不是可下载的那个）
  2. _cache_story_videos 把视频下到 static/meta_videos/<ad_id>.mp4 并记 video_local_path
  3. 下崩/半个文件（<1KB）不落库、不留残file
  4. 按需接口：管理员可缓存任意素材；普通用户只能缓存自己账号下的；缺 ad_id → 400
  5. 画廊返回 video_local；清理按同一套保留天数把本地视频也删掉、清空 video_local_path
  6. token 顺序：账户自己的 token 优先（实测 BM token 读 advideos 会 100/33）
"""
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def main():
    import database
    import scraper
    import meta_api
    import main
    from fastapi.testclient import TestClient
    from main import get_current_user

    tmp = Path(tempfile.mkdtemp(prefix="gal_video_"))
    database.DB_PATH = tmp / "dashboard.db"
    database.init_db()
    video_dir = tmp / "static" / "meta_videos"
    scraper._CREATIVE_VIDEO_DIR = video_dir
    scraper._CREATIVE_CACHE_DIR = tmp / "static" / "meta_creatives"
    scraper._CREATIVE_CACHE_DIR.mkdir(parents=True)

    ACT = "act_999"
    with database.get_conn() as conn:
        conn.execute("INSERT INTO meta_accounts (act_id, act_name, access_token, bm_id, user_id, status) "
                     "VALUES (?, ?, ?, ?, ?, 'active')", (ACT, "测试账户", "acct-token", "bm-1", 1))
        conn.execute("INSERT INTO meta_accounts (act_id, act_name, access_token, bm_id, user_id, status) "
                     "VALUES (?, ?, ?, ?, ?, 'active')", (ACT + "B", "别人的账户", "other-token", "bm-2", 7))

    # 1) story 视频 id 落库
    database.upsert_meta_ad_creative({
        "ad_id": "ad_v1", "ad_account": ACT, "ad_name": "视频广告",
        "video_id": "page_video_111",          # 页面视频 id：换不到下载地址
        "story_video_id": "story_video_222",   # 账户里的视频 id：能换到
        "image_url": "", "thumbnail_url": "http://x/t.jpg",
    }, 1)
    with database.get_conn() as conn:
        row = conn.execute("SELECT video_id, story_video_id, video_local_path FROM meta_ad_creatives "
                           "WHERE ad_id='ad_v1'").fetchone()
    assert row["story_video_id"] == "story_video_222", dict(row)
    assert row["video_local_path"] == "", "还没下载，本地路径应为空"

    # 2) 自动缓存
    database.upsert_meta_ad_creative({"ad_id": "ad_v2", "ad_account": ACT,
                                      "story_video_id": "story_video_222"}, 1)
    calls = {"sources": 0}

    def fake_sources(act_id, token, limit=200):
        calls["sources"] += 1
        return {"story_video_222": "https://cdn.example/v.mp4"}, None

    def fake_download(url, dest, timeout=30):
        Path(dest).write_bytes(b"x" * 2048)
        return True, None

    meta_api.get_account_video_sources = fake_sources
    meta_api.download_file = fake_download

    n = scraper._cache_story_videos(ACT, "acct-token", ["ad_v1", "ad_v2"], 1)
    assert n == 2, n
    assert (video_dir / "ad_v1.mp4").exists() and (video_dir / "ad_v2.mp4").exists()
    with database.get_conn() as conn:
        paths = [r["video_local_path"] for r in conn.execute(
            "SELECT video_local_path FROM meta_ad_creatives WHERE ad_id IN ('ad_v1','ad_v2')")]
    assert paths == ["meta_videos/ad_v1.mp4", "meta_videos/ad_v2.mp4"], paths

    # 3) 半个文件不落库、不留残file
    database.upsert_meta_ad_creative({"ad_id": "ad_v3", "ad_account": ACT,
                                      "story_video_id": "story_video_222"}, 1)
    meta_api.download_file = lambda url, dest, timeout=30: (Path(dest).write_bytes(b"tiny") and False, "截断")
    n = scraper._cache_story_videos(ACT, "acct-token", ["ad_v3"], 1)
    assert n == 0, n
    assert not (video_dir / "ad_v3.mp4").exists(), "半个文件不能留下"
    with database.get_conn() as conn:
        assert conn.execute("SELECT video_local_path FROM meta_ad_creatives WHERE ad_id='ad_v3'"
                            ).fetchone()["video_local_path"] == ""

    # 4) 按需接口
    meta_api.download_file = fake_download
    admin = {"id": 1, "username": "admin", "role": "admin"}
    normal = {"id": 7, "username": "u7", "role": "user"}
    c = TestClient(main.app)

    c.app.dependency_overrides[get_current_user] = lambda: normal
    database.upsert_meta_ad_creative({"ad_id": "ad_v9", "ad_account": ACT + "B",
                                      "story_video_id": "story_video_222"}, 7)
    r = c.post("/api/meta/gallery/cache-video", json={"ad_id": "ad_v9"})
    assert r.status_code == 200, r.text            # 自己的素材可以缓存
    r = c.post("/api/meta/gallery/cache-video", json={"ad_id": "ad_v3"})
    assert r.status_code == 403, f"不该能缓存别人的素材: {r.text}"

    c.app.dependency_overrides[get_current_user] = lambda: admin
    assert c.post("/api/meta/gallery/cache-video", json={}).status_code == 400
    assert c.post("/api/meta/gallery/cache-video", json={"ad_id": "不存在"}).status_code == 404
    r = c.post("/api/meta/gallery/cache-video", json={"ad_id": "ad_v3"})
    assert r.status_code == 200 and r.json()["video_local"].endswith("ad_v3.mp4"), r.text
    assert (video_dir / "ad_v3.mp4").exists()

    # 4.5) story_video_id 还没补上（同步没轮到这条）时，按需路径要当场去 Meta 取并回填，
    #      不能直接报「没取到可下载的视频」让用户干等
    database.upsert_meta_ad_creative({"ad_id": "ad_v4", "ad_account": ACT,
                                      "video_id": "page_video_333"}, 1)
    asked = []
    meta_api.get_story_video_id = lambda ad_id, tk: (asked.append(ad_id), ("story_video_222", None))[1]
    ok, msg = scraper.cache_story_video_now("ad_v4", 1)
    assert ok, (ok, msg)
    assert asked == ["ad_v4"], f"应就这条广告问一次 Meta: {asked}"
    with database.get_conn() as conn:
        r4 = conn.execute("SELECT story_video_id, video_id FROM meta_ad_creatives WHERE ad_id='ad_v4'").fetchone()
    assert r4["story_video_id"] == "story_video_222", "取到的 id 要回填"
    # 回填只准动那一列：用整条 upsert 会把 video_id 等字段冲成空
    assert r4["video_id"] == "page_video_333", f"回填不该动其它字段: {dict(r4)}"
    # 同步侧的提前返回也要被打破：还有没补的就不该直接 return
    with database.get_conn() as conn:
        conn.execute("UPDATE meta_ad_creatives SET story_video_id='' WHERE ad_id='ad_v4'")
    assert database.count_creatives_missing_story_id(ACT, 1, ["ad_v4"]) == 1
    assert database.count_creatives_missing_story_id(ACT, 1, ["ad_v1"]) == 0, "已补过的不该再算"

    # 5) 画廊返回 video_local
    import analytics
    with database.get_conn() as conn:
        conn.execute("INSERT INTO meta_ad_stats (date, ad_account, ad_id, spend, user_id) "
                     "VALUES (date('now'), ?, 'ad_v3', 10, 1)", (ACT,))
    g = analytics.meta_creative_gallery(user_id=1, page_size=50)
    item = [x for x in g["data"] if x["ad_id"] == "ad_v3"]
    assert item and item[0]["video_local"] == "/static/meta_videos/ad_v3.mp4", item

    # 6) 保留清理把本地视频一起清掉（把状态改成很久没投放）
    with database.get_conn() as conn:
        conn.execute("UPDATE meta_ad_stats SET date = date('now', '-100 days') WHERE ad_id='ad_v3'")
        conn.execute("UPDATE meta_ad_creatives SET synced_at = datetime('now','-100 days') WHERE ad_id='ad_v3'")
    r = scraper.cleanup_meta_creatives(60, dry_run=True)
    assert "ad_v3" in [s["ad_id"] for s in r["samples"]], r["samples"]
    before = r["deleted_files"]
    r = scraper.cleanup_meta_creatives(60, dry_run=False)
    assert r["deleted_files"] >= before
    assert not (video_dir / "ad_v3.mp4").exists(), "过期的本地视频应被清理"
    with database.get_conn() as conn:
        assert conn.execute("SELECT video_local_path FROM meta_ad_creatives WHERE ad_id='ad_v3'"
                            ).fetchone()["video_local_path"] == ""

    # 7) token 顺序：账户自己的 token 排第一（BM 的读 advideos 会 100/33）
    toks = scraper._meta_tokens_for_account(ACT, 1)
    assert toks and toks[0] == "acct-token", toks

    c.app.dependency_overrides.clear()
    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 视频取 story id → 下到本地 → 画廊播本地 → 按保留天数清理，接口权限与半包防护均正确")


if __name__ == "__main__":
    main()
