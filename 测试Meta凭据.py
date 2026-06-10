"""Meta 凭据验证工具 — 测试 App ID / Secret / Token / 权限 / 可访问账户"""
import json, requests, sys

proxies = None
config = json.loads(open('config.json', encoding='utf-8').read())
meta = config.get('meta', {})

# 代理
proxy_url = meta.get('proxy', '').strip()
if proxy_url:
    proxies = {'http': proxy_url, 'https': proxy_url}
    print(f'[INFO] 使用代理: {proxy_url}')

app_id = meta.get('app_id', '').strip()
app_secret = meta.get('app_secret', '').strip()
token = meta.get('default_access_token', '').strip()

print()
print('=== Meta 凭据验证 ===')
print()

if not app_id:
    print('[FAIL] App ID 未填写 -- 请在 Meta 数据 Tab 填写后保存')
    input('按回车退出...')
    sys.exit(1)
if not app_secret:
    print('[FAIL] App Secret 未填写 -- 请在 Meta 数据 Tab 填写后保存')
    input('按回车退出...')
    sys.exit(1)

print(f'App ID: {app_id[:8]}... (已填写)')
print(f'App Secret: {"*" * len(app_secret)} (已填写)')
print()

# 1. 验证 App ID + Secret
print('>>> 1/4 验证 App ID + App Secret...')
try:
    resp = requests.get('https://graph.facebook.com/v25.0/oauth/access_token', params={
        'client_id': app_id, 'client_secret': app_secret, 'grant_type': 'client_credentials'
    }, proxies=proxies, timeout=20)
    data = resp.json()
    if 'access_token' in data:
        print('[OK] 凭据正确')
        app_token = data['access_token']
    else:
        err = data.get('error', {})
        print(f'[FAIL] 验证失败: {err.get("message", err)}')
        print('建议: 检查 App ID 和 Secret 是否复制完整、无多余空格')
        input('按回车退出...')
        sys.exit(1)
except Exception as e:
    print(f'[FAIL] 连接失败: {e}')
    print('建议: 检查网络/代理设置，确保能访问 graph.facebook.com')
    input('按回车退出...')
    sys.exit(1)

# 2. 查应用名称
try:
    resp = requests.get(f'https://graph.facebook.com/v25.0/{app_id}', params={
        'access_token': app_token, 'fields': 'id,name'
    }, proxies=proxies, timeout=15)
    d = resp.json()
    print(f'[OK] 应用名称: {d.get("name", "未知")}')
except Exception:
    pass

print()

# 3. 验证 User Token
print('>>> 2/4 验证 Access Token...')
if not token:
    print('[WARN] 未填写 Access Token')
    print('请在 Meta 数据 Tab 填写后保存，再运行本工具')
    input('按回车退出...')
    sys.exit(0)

try:
    resp = requests.get('https://graph.facebook.com/v25.0/debug_token', params={
        'input_token': token, 'access_token': app_token
    }, proxies=proxies, timeout=15)
    debug = resp.json().get('data', {})

    if debug.get('is_valid'):
        scopes = debug.get('scopes', [])
        print(f'[OK] Token 有效')
        print(f'  权限列表: {", ".join(scopes) if scopes else "(无)"}')

        missing = [p for p in ['ads_read', 'ads_management', 'business_management'] if p not in scopes]
        if missing:
            print(f'')
            print(f'[WARN] 缺少权限: {", ".join(missing)}')
            print(f'')
            print(f'修复方法:')
            print(f'  1. 打开 https://developers.facebook.com/tools/explorer')
            print(f'  2. 选择你的应用')
            print(f'  3. 点击 Add Permission -> 勾选 {", ".join(missing)}')
            print(f'  4. Generate Access Token')
            print(f'  5. 把新 Token 贴到系统的 Meta 数据 Tab')
        else:
            print(f'  [OK] 三项必需权限齐全')
    else:
        err = debug.get('error', {})
        print(f'[FAIL] Token 无效: {err.get("message", debug)}')
        print('建议: 重新生成 Token')
        input('按回车退出...')
        sys.exit(1)
except Exception as e:
    print(f'[FAIL] Token 验证失败: {e}')
    input('按回车退出...')
    sys.exit(1)

print()

# 4. 拉取账户
print('>>> 3/4 拉取广告账户...')
try:
    resp = requests.get('https://graph.facebook.com/v25.0/me/adaccounts', params={
        'access_token': token, 'fields': 'id,name,account_status', 'limit': '50'
    }, proxies=proxies, timeout=15)
    d = resp.json()
    if 'error' in d:
        print(f'[FAIL] {d["error"].get("message", d)}')
    else:
        accts = d.get('data', [])
        print(f'[OK] 直接可访问: {len(accts)} 个广告账户')
        for a in accts:
            status = 'active' if a.get('account_status') == 1 else 'disabled'
            print(f'  {a.get("id")} - {a.get("name")} [{status}]')
except Exception as e:
    print(f'[FAIL] {e}')

print()

# 5. 拉取 BM
print('>>> 4/4 拉取 BM 及旗下账户...')
try:
    resp = requests.get('https://graph.facebook.com/v25.0/me/businesses', params={
        'access_token': token, 'fields': 'id,name', 'limit': '20'
    }, proxies=proxies, timeout=15)
    d = resp.json()
    if 'error' in d:
        print(f'[FAIL] 拉取 BM 失败: {d["error"].get("message", d)}')
        print(f'  可能缺少 business_management 权限')
    else:
        bms = d.get('data', [])
        print(f'[OK] 可访问: {len(bms)} 个 BM')
        total_bm_accts = 0
        for b in bms:
            bm_name = b.get('name', '')
            bm_id = b.get('id', '')
            owned_count = 0
            client_count = 0
            # 自有账户
            try:
                r = requests.get(f'https://graph.facebook.com/v25.0/{bm_id}/owned_ad_accounts', params={
                    'access_token': token, 'fields': 'id,name', 'limit': '50'
                }, proxies=proxies, timeout=15)
                oa = r.json().get('data', [])
                owned_count = len(oa)
                for a in oa:
                    print(f'  [{bm_name}] 自有: {a.get("id")} - {a.get("name")}')
            except Exception:
                pass
            # 代投账户
            try:
                r = requests.get(f'https://graph.facebook.com/v25.0/{bm_id}/client_ad_accounts', params={
                    'access_token': token, 'fields': 'id,name', 'limit': '50'
                }, proxies=proxies, timeout=15)
                ca = r.json().get('data', [])
                client_count = len(ca)
                for a in ca:
                    print(f'  [{bm_name}] 代投: {a.get("id")} - {a.get("name")}')
            except Exception:
                pass
            if owned_count == 0 and client_count == 0:
                print(f'  [{bm_name}] 无账户')
            total_bm_accts += owned_count + client_count
        print(f'[OK] BM 下共 {total_bm_accts} 个账户')
        print(f'[OK] 总可发现: {len(accts) + total_bm_accts} 个账户 (直连 {len(accts)} + BM {total_bm_accts})')
except Exception as e:
    print(f'[FAIL] {e}')

print()
print('==========================================')
print('验证完成！按回车退出...')
input()
