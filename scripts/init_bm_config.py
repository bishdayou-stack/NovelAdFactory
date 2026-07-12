"""根据现有 meta_accounts 数据初始化 bm_config 表"""
import sqlite3

conn = sqlite3.connect('data/dashboard.db')
conn.row_factory = sqlite3.Row

# 1. 从 meta_accounts 提取 BM 分组（按 pingykj_account + user_id）
rows = conn.execute("""
    SELECT pingykj_account as bm_label,
           user_id,
           COUNT(*) as cnt,
           GROUP_CONCAT(act_id) as account_ids
    FROM meta_accounts
    WHERE pingykj_account IS NOT NULL AND pingykj_account != ''
    GROUP BY pingykj_account, COALESCE(user_id, 0)
    ORDER BY user_id
""").fetchall()

print("===== 初始化 BM 配置 =====")

for r in rows:
    label = r["bm_label"]
    uid = r["user_id"] if r["user_id"] else 1  # 未分配的归 admin
    cnt = r["cnt"]

    # 尝试从标签中提取 BM ID（如 "TL BM 352475" → "352475"）
    bm_id = label
    # 如果标签是已知格式，提取 BM ID
    if "BM" in label or label.replace(" ", "").isdigit():
        # 保留原名作为 bm_id
        pass

    print(f"  BM: {label}  → user_id={uid}  账户数={cnt}")

    # UPSERT bm_config
    conn.execute("""
        INSERT INTO bm_config (bm_id, bm_name, system_token, app_id, user_id)
        VALUES (?, ?, '', '', ?)
        ON CONFLICT(bm_id) DO UPDATE SET
            bm_name = excluded.bm_name,
            user_id = excluded.user_id,
            updated_at = CURRENT_TIMESTAMP
    """, (label, label, uid))

    # 更新该 BM 下所有 meta_accounts 的 bm_id
    conn.execute("""
        UPDATE meta_accounts
        SET bm_id = ?
        WHERE pingykj_account = ?
          AND COALESCE(user_id, 0) = COALESCE(?, 0)
    """, (label, label, r["user_id"] or 0))

conn.commit()

# 2. 验证结果
print("\n===== bm_config 验证 =====")
for r in conn.execute("SELECT * FROM bm_config ORDER BY bm_name"):
    print(dict(r))

print("\n===== meta_accounts bm_id 覆盖验证 =====")
total = conn.execute("SELECT COUNT(*) FROM meta_accounts WHERE status='active'").fetchone()[0]
linked = conn.execute("SELECT COUNT(*) FROM meta_accounts WHERE bm_id IS NOT NULL AND bm_id != ''").fetchone()[0]
print(f"  总活跃账户: {total}, 已关联 BM: {linked}")

# 3. 确保 app_config 有默认 App（从 config.json）
import json
with open('config.json', 'r', encoding='utf-8') as f:
    cfg = json.load(f)

meta_cfg = cfg.get("meta", {})
app_id = meta_cfg.get("app_id", "")
if app_id:
    conn.execute("""
        INSERT INTO app_config (app_name, app_id, app_secret, is_default, user_id)
        VALUES ('默认应用', ?, '', 1, 1)
        ON CONFLICT(app_id) DO UPDATE SET
            app_name = '默认应用',
            is_default = 1,
            updated_at = CURRENT_TIMESTAMP
    """, (app_id,))
    conn.commit()
    print(f"\n===== 默认 App: {app_id} =====")

conn.close()
print("\n初始化完成！")
