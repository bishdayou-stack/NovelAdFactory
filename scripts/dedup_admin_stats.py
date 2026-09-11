# -*- coding: utf-8 -*-
"""清理因「同一书城账号被 admin 与其他用户同时同步」产生的重复行。

规则：删除 user_id=1 名下、source!='meta'、且**其他用户也有同 (date, ad_account, site)** 的行
      （口径与唯一键一致）。admin 独有的行保留。另清空 admin 行的通用凭据列（见 P1）。
      **先校验 user_id=1 确实是管理员**，否则中止 —— 免得在生产库删错用户的数据。
      默认 dry-run，加 --apply 才真删。

用法：
    python scripts/dedup_admin_stats.py            # 只看将删多少
    python scripts/dedup_admin_stats.py --apply    # 真删（拒绝 apply 前未 dry-run 的规则不需要，直接执行）
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))

ADMIN_UID = 1

# 与 ad_daily_stats 的真实唯一键 (date, ad_account, source, user_id, site) 口径一致：
# 少比 site 会把「同日期同账户但不同站点」的行误判成重复（跨站误删）。
DUP_WHERE = """
    user_id = ? AND source != 'meta' AND EXISTS (
        SELECT 1 FROM ad_daily_stats b
        WHERE b.date = ad_daily_stats.date
          AND b.ad_account = ad_daily_stats.ad_account
          AND b.site = ad_daily_stats.site
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
        # I1: 全部动作锚在 user_id 上，必须先确认它是管理员。生产库若 id=1 是普通用户，
        # --apply 会删掉这个普通用户名下所有「别人也有同 (date, account, site)」的行 ——
        # 直接违反「不得碰其他用户数据」，且删后自检必然通过（keep 与 dup 同源，永远自洽）。
        target = conn.execute("SELECT id, username, role FROM users WHERE id = ?",
                              (ADMIN_UID,)).fetchone()
        if target is None:
            sys.exit(f"[中止] user_id={ADMIN_UID} 不存在，请先确认目标用户")
        if target["role"] != "admin":
            sys.exit(f"[中止] user_id={ADMIN_UID}({target['username']}) 不是管理员，"
                     f"当前角色为 {target['role']!r}，请先确认目标用户")
        print(f"目标用户: id={target['id']} username={target['username']} role={target['role']}")

        # 生产库可能不止一个管理员。清理只作用于 ADMIN_UID，把其余管理员的行数也列出来，
        # 操作者才能判断是否还需要对别的 admin 再跑一次。
        print("全部管理员及其 pingykj 行数（本次只处理上述目标用户）:")
        for a in conn.execute("SELECT id, username FROM users WHERE role = 'admin' ORDER BY id"):
            n = conn.execute(
                "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
                (a["id"],)).fetchone()[0]
            mark = " ← 目标" if a["id"] == ADMIN_UID else ""
            print(f"  id={a['id']} {a['username']}: {n} 行{mark}")

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

        # P1（独立于上面的重复行清理，别塞进 DUP_WHERE）：admin 行的**通用**凭据列。
        # 这两列有值就是取不到的死密钥（取凭据处对 admin 一律返回 None），且用户管理页
        # 保存 admin 资料时前端回填 → 非空 → 护栏 400。此前只清了「每站凭据」，这里兜底。
        admin_creds = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' "
            "AND (pingykj_username != '' OR pingykj_password_encrypted != '')").fetchone()[0]

        print(f"admin(uid={ADMIN_UID}) pingykj 总行数: {total_admin}")
        print(f"将删除（其他用户也有同日期+同账户+同站点）: {dup}")
        print(f"将保留（admin 独有）: {keep}")
        print(f"将清空的 admin 通用凭据列（users.pingykj_username/password_encrypted）: {admin_creds} 行")
        print("样例:")
        for r in sample:
            print("  ", dict(r))

        if not args.apply:
            print("\n（dry-run，未做任何修改。加 --apply 才真删）")
            return

        # 备份用 sqlite3 在线备份 API 取一致快照：与并发写者共存也行，且不像
        # `PRAGMA wal_checkpoint(TRUNCATE)` + 多次 shutil.copy 那样会静默失败 ——
        # 有并发读者时 checkpoint 返回 (1,1,1) 却不抛异常（原 except 是死代码），
        # 主库缺帧 + 「旧 .db 配空 -wal」两次拷贝非原子，恰好会丢掉本次要删的近期行。
        bak = db_path.with_name(db_path.name + f".bak-dedup-{datetime.now():%Y%m%d%H%M%S%f}")
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(bak))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print(f"\n已备份到: {bak}")

        cur = conn.execute(f"DELETE FROM ad_daily_stats WHERE {DUP_WHERE}", (ADMIN_UID, ADMIN_UID))
        print(f"已删除 {cur.rowcount} 行")

        # 显式、独立的一条：清空 admin 的通用凭据列（干跑时已报将影响行数）
        cur_creds = conn.execute(
            "UPDATE users SET pingykj_username = '', pingykj_password_encrypted = '' "
            "WHERE role = 'admin' AND (pingykj_username != '' OR pingykj_password_encrypted != '')")
        print(f"已清空 admin 通用凭据列: {cur_creds.rowcount} 行")

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
