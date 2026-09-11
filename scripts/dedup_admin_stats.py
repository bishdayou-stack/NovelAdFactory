# -*- coding: utf-8 -*-
"""清理因「同一书城账号被 admin 与其他用户同时同步」产生的重复行。

规则：删除 user_id=1(admin) 名下、source!='meta'、且**其他用户也有同 (date, ad_account)** 的行。
      admin 独有的行保留。默认 dry-run，加 --apply 才真删。

用法：
    python scripts/dedup_admin_stats.py            # 只看将删多少
    python scripts/dedup_admin_stats.py --apply    # 真删（拒绝 apply 前未 dry-run 的规则不需要，直接执行）
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

ADMIN_UID = 1

DUP_WHERE = """
    user_id = ? AND source != 'meta' AND EXISTS (
        SELECT 1 FROM ad_daily_stats b
        WHERE b.date = ad_daily_stats.date
          AND b.ad_account = ad_daily_stats.ad_account
          AND b.user_id != ?
          AND b.source != 'meta')
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只预览）")
    args = ap.parse_args()

    import database
    db_path = database._get_db_path()

    with database.get_conn() as conn:
        total_admin = conn.execute(
            "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
            (ADMIN_UID,)).fetchone()[0]
        dup = conn.execute(
            f"SELECT COUNT(*) FROM ad_daily_stats WHERE {DUP_WHERE}",
            (ADMIN_UID, ADMIN_UID)).fetchone()[0]
        keep = total_admin - dup
        sample = conn.execute(
            f"SELECT date, ad_account, total_spend FROM ad_daily_stats WHERE {DUP_WHERE} LIMIT 5",
            (ADMIN_UID, ADMIN_UID)).fetchall()

        print(f"admin(uid={ADMIN_UID}) pingykj 总行数: {total_admin}")
        print(f"将删除（其他用户也有同日期+同账户）: {dup}")
        print(f"将保留（admin 独有）: {keep}")
        print("样例:")
        for r in sample:
            print("  ", dict(r))

        if not args.apply:
            print("\n（dry-run，未做任何修改。加 --apply 才真删）")
            return

        # 备份。WAL 模式下只复制主库文件会漏掉尚未 checkpoint 的近期写入 → 先 checkpoint 再复制，
        # 若 checkpoint 被在跑的实例挡住，则连带把 -wal 一起复制，保证备份可完整还原。
        bak = db_path.with_name(db_path.name + f".bak-dedup-{datetime.now():%Y%m%d%H%M%S}")
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            print(f"[warn] WAL checkpoint 失败（不影响删除，但备份会连带复制 -wal）: {e}")
        shutil.copy(db_path, bak)
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists():
                shutil.copy(side, Path(str(bak) + suffix))
        print(f"\n已备份到: {bak}")

        cur = conn.execute(f"DELETE FROM ad_daily_stats WHERE {DUP_WHERE}", (ADMIN_UID, ADMIN_UID))
        print(f"已删除 {cur.rowcount} 行")

    # 删后核对
    with database.get_conn() as conn:
        left = conn.execute(
            "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
            (ADMIN_UID,)).fetchone()[0]
        dup_left = conn.execute(
            f"SELECT COUNT(*) FROM ad_daily_stats WHERE {DUP_WHERE}",
            (ADMIN_UID, ADMIN_UID)).fetchone()[0]
    print(f"删后 admin 行数: {left}（应等于保留数 {keep}）")
    print(f"删后残留重复: {dup_left}（应为 0）")
    assert left == keep and dup_left == 0, "清理结果不符合预期，请用备份恢复后排查"


if __name__ == "__main__":
    main()
