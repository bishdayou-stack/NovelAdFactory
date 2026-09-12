# -*- coding: utf-8 -*-
"""验证：旧素材清理（仅管理员）+ 删除接口的鉴权。

覆盖：
  1. 预览只列出「早于 N 天」的批次，正在生成和已被引用的批次跳过并给出原因
  2. 执行删除只动可删的那些，跳过的一个都不能少
  3. 非管理员调清理接口 → 403
  4. 普通用户删别人的批次 → 403，删自己的 → 成功
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


def make_batch(root: Path, name: str, days_old: int, user_id: int = 1, files: int = 2) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    created = datetime.now() - timedelta(days=days_old)
    (d / "_meta.json").write_text(json.dumps({
        "batch_id": name, "user_id": user_id, "status": "done",
        "created_at": created.isoformat(),
    }), encoding="utf-8")
    (d / "_progress.json").write_text("{}", encoding="utf-8")
    for i in range(files):
        (d / f"img{i}.png").write_bytes(b"x" * 1024)
    return d


def main():
    import database
    import main
    from fastapi.testclient import TestClient
    from main import get_current_user

    tmp = Path(tempfile.mkdtemp(prefix="cleanup_"))
    database.DB_PATH = tmp / "dashboard.db"
    database.init_db()
    root = tmp / "output"
    root.mkdir()
    main.OUTPUT_ROOT = root

    old = make_batch(root, "1001", 40)                 # 该删
    older = make_batch(root, "1002", 90)               # 该删
    recent = make_batch(root, "1003", 2)               # 太新，不删
    running = make_batch(root, "1004", 40)             # 正在生成，不删
    referenced = make_batch(root, "1005", 40)          # 被投放队列引用，不删
    other_user = make_batch(root, "1006", 40, user_id=2)
    (root / "not-a-batch").mkdir()                     # 非批次目录，不删

    main._register_batch(1004)                         # 标记为运行中

    with database.get_conn() as conn:                  # 让 1005 进投放队列
        conn.execute("INSERT INTO delivery_queue (batch_id, image_type, image_path) "
                     "VALUES (?, ?, ?)", ("1005", "text_single", str(referenced / "img0.png")))

    admin = {"id": 1, "username": "admin", "role": "admin"}
    normal = {"id": 7, "username": "u7", "role": "user"}

    c = TestClient(main.app)

    # 覆盖 get_current_user（而不是 get_current_admin）：后者整个被替换掉就绕过了角色校验，
    # 那样测不出 403。覆盖底层依赖才能让真正的管理员守卫执行。
    # 1) 预览
    c.app.dependency_overrides[get_current_user] = lambda: admin
    r = c.get("/api/history/cleanup/preview?days=30")
    assert r.status_code == 200, r.text
    d = r.json()
    assert sorted(b["batch_id"] for b in d["batches"]) == ["1001", "1002", "1006"], d
    # 每批 2 张图 + _meta.json + _progress.json = 4 个文件
    assert d["batch_count"] == 3 and d["file_count"] == 12, d
    reasons = {s["batch_id"]: s["reason"] for s in d["skipped"]}
    assert reasons.get("1004") == "正在生成中", reasons
    assert reasons.get("1005") == "被投放队列或爆款素材引用", reasons
    assert "1003" not in reasons, f"太新的批次不该出现在跳过里（它根本不在范围内）: {reasons}"

    # 2) 非管理员不给用
    c.app.dependency_overrides[get_current_user] = lambda: normal
    assert c.get("/api/history/cleanup/preview?days=30").status_code == 403
    assert c.post("/api/history/cleanup", json={"days": 30}).status_code == 403

    # 3) 参数校验
    c.app.dependency_overrides[get_current_user] = lambda: admin
    assert c.post("/api/history/cleanup", json={"days": 0}).status_code == 400

    # 4) 真正执行
    r = c.post("/api/history/cleanup", json={"days": 30})
    assert r.status_code == 200, r.text
    res = r.json()
    assert sorted(res["deleted"]) == ["1001", "1002", "1006"], res
    assert not res["failed"], res
    for name in ("1001", "1002", "1006"):
        assert not (root / name).exists(), f"{name} 应该已被删除"
    for name in ("1003", "1004", "1005"):
        assert (root / name).exists(), f"{name} 不该被删除"
    assert (root / "not-a-batch").exists()

    # 5) 删除接口鉴权：普通用户不能删别人的
    c.app.dependency_overrides[get_current_user] = lambda: normal
    assert c.delete("/api/history/1003").status_code == 403, "普通用户不该能删别人的批次"
    mine = make_batch(root, "1007", 1, user_id=7)
    assert c.delete("/api/history/1007").status_code == 200
    assert not mine.exists()

    # 批量删除接口同样要鉴权
    r = c.post("/api/history/batch-delete", json={"batch_ids": ["1003"]})
    assert r.json()["results"][0]["status"] == "forbidden", r.text

    c.app.dependency_overrides.clear()
    main._deregister_batch(1004)
    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: 清理只删该删的（运行中/被引用/太新的都保留），接口限管理员，删除接口已鉴权")


if __name__ == "__main__":
    main()
