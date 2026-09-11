# -*- coding: utf-8 -*-
"""清理管理员账号误同步的书城数据：删掉重复的，余下的改挂到真正的用户名下。

背景：管理员不该持有书城凭据（登录/同步均已加护栏），但历史上误绑过。用别人的书城账号
同步会出现两种行：
  ① 其他用户也有同 (date, ad_account, site) 的行 —— 数据重复，删掉；
  ② 只有管理员有的行 —— 其实是被借用凭据那个用户的数据，`--to-user` 转给他。
另：orders / raw_orders / raw_ad_stats 的唯一键**不含 user_id**（(site, order_id) /
(site, record_id)），先同步的人永久占坑 —— 管理员先跑就"抢"走了别人的订单，也一并转。

**先校验 user_id=1 确实是管理员**，否则中止 —— 免得在生产库删错用户的数据。
默认 dry-run，加 --apply 才真动。

用法：
    python scripts/dedup_admin_stats.py                          # 只看将删/将转多少
    python scripts/dedup_admin_stats.py --apply                  # 只删重复（旧行为）
    python scripts/dedup_admin_stats.py --to-user 刘国荣 --apply  # 删重复 + 余下的转给他
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
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只预览）")
    ap.add_argument("--to-user", default="", metavar="用户名",
                    help="把删重复后剩下的行转给这个用户（按 username 精确匹配，须是普通用户）")
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
        print(f"来源用户: id={target['id']} username={target['username']} role={target['role']}")

        # 转入方：不接受另一个 admin —— 那只是把同一个问题挪个位置
        to_uid = None
        if args.to_user:
            row = conn.execute("SELECT id, username, role FROM users WHERE username = ?",
                               (args.to_user,)).fetchone()
            if row is None:
                sys.exit(f"[中止] 找不到用户 {args.to_user!r}")
            if row["role"] == "admin":
                sys.exit(f"[中止] {args.to_user!r} 是管理员，不能把书城数据转给管理员")
            to_uid = row["id"]
            print(f"转入用户: id={to_uid} username={row['username']} role={row['role']}")
        else:
            print("未指定 --to-user：本次只删重复，剩余行仍挂在管理员名下")

        # 生产库可能不止一个管理员。清理只作用于 ADMIN_UID，把其余管理员的行数也列出来，
        # 操作者才能判断是否还需要对别的 admin 再跑一次。
        print("全部管理员及其书城行数（本次只处理上述来源用户）:")
        for a in conn.execute("SELECT id, username FROM users WHERE role = 'admin' ORDER BY id"):
            n = conn.execute(
                "SELECT COUNT(*) FROM ad_daily_stats WHERE user_id = ? AND source != 'meta'",
                (a["id"],)).fetchone()[0]
            mark = " ← 来源" if a["id"] == ADMIN_UID else ""
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

        # 订单/原始档案：唯一键不含 user_id，只能整行改归属
        side = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?",
                                (ADMIN_UID,)).fetchone()[0]
                for t in ("orders", "raw_orders", "raw_ad_stats")}

        print(f"ad_daily_stats: 总 {total_admin} 行 = 删重复 {dup} + 转出 {keep}")
        print("订单/原始档案（整行改归属）: " +
              "  ".join(f"{t}={n}" for t, n in side.items()))
        print(f"将清空的 admin 通用凭据列（users.pingykj_username/password_encrypted）: {admin_creds} 行")
        print("待删样例:")
        for r in sample:
            print("  ", dict(r))

        if to_uid:
            # 先把转入方当前的合计记下来，apply 后好核对增量
            before = conn.execute(
                "SELECT COUNT(*), ROUND(COALESCE(SUM(total_spend),0),2) FROM ad_daily_stats "
                "WHERE user_id = ? AND source != 'meta'", (to_uid,)).fetchone()
            print(f"转入方当前: {before[0]} 行 / 消耗 {before[1]}")

        if not args.apply:
            print("\n（dry-run，未做任何修改。加 --apply 才真执行）")
            return

        # 备份用 sqlite3 在线备份 API 取一致快照：与并发写者共存也行，且不像
        # `PRAGMA wal_checkpoint(TRUNCATE)` + 多次 shutil.copy 那样会静默失败 ——
        # 有并发读者时 checkpoint 返回 (1,1,1) 却不抛异常（原 except 是死代码），
        # 主库缺帧 + 「旧 .db 配空 -wal」两次拷贝非原子，恰好会丢掉本次要动的近期行。
        bak = db_path.with_name(db_path.name + f".bak-adminfix-{datetime.now():%Y%m%d%H%M%S%f}")
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(bak))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print(f"\n已备份到: {bak}")

        cur = conn.execute(f"DELETE FROM ad_daily_stats WHERE {DUP_WHERE}", (ADMIN_UID, ADMIN_UID))
        print(f"已删除重复行 {cur.rowcount} 行")

        if to_uid:
            # 先删后转，所以此刻不可能撞唯一键：凡转入方已有同键的行，上面已按重复删掉了。
            cur = conn.execute(
                "UPDATE ad_daily_stats SET user_id = ? WHERE user_id = ? AND source != 'meta'",
                (to_uid, ADMIN_UID))
            print(f"已转出 ad_daily_stats {cur.rowcount} 行 → {args.to_user}")
            for t in ("orders", "raw_orders", "raw_ad_stats"):
                cur = conn.execute(f"UPDATE {t} SET user_id = ? WHERE user_id = ?",
                                   (to_uid, ADMIN_UID))
                print(f"已转出 {t} {cur.rowcount} 行 → {args.to_user}")

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
    expect = 0 if to_uid else keep
    print(f"处理后 admin 行数: {left}（应为 {expect}）")
    print(f"处理后残留重复: {dup_left}（应为 0）")
    assert left == expect and dup_left == 0, "清理结果不符合预期，请用备份恢复后排查"

    if to_uid:
        with database.get_conn() as conn:
            after = conn.execute(
                "SELECT COUNT(*), ROUND(COALESCE(SUM(total_spend),0),2) FROM ad_daily_stats "
                "WHERE user_id = ? AND source != 'meta'", (to_uid,)).fetchone()
            admin_side = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?",
                                          (ADMIN_UID,)).fetchone()[0]
                          for t in ("orders", "raw_orders", "raw_ad_stats")}
        print(f"转入方现在: {after[0]} 行 / 消耗 {after[1]}"
              f"（较处理前 +{after[0] - before[0]} 行）")
        print("管理员名下残留订单/档案: " +
              "  ".join(f"{t}={n}" for t, n in admin_side.items()) + "（均应为 0）")


if __name__ == "__main__":
    main()
