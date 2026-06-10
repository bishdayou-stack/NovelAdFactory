"""Meta 凭据验证工具 — 使用 curl 确保代理兼容"""
import json, subprocess, sys

config = json.loads(open('config.json', encoding='utf-8').read())
meta = config.get('meta', {})

proxy = meta.get('proxy', '').strip()
app_id = meta.get('app_id', '').strip()
app_secret = meta.get('app_secret', '').strip()
token = meta.get('default_access_token', '').strip()

def curl_get(url, timeout=15):
    """通过 curl + 代理发送 GET 请求"""
    cmd = ['curl', '-s', '-X', 'GET', '--connect-timeout', str(timeout), '-w', '\n%{http_code}', url]
    if proxy:
        cmd[1:1] = ['-x', proxy]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        output = result.stdout.strip()
        if not output:
            return None, None
        lines = output.rsplit('\n', 1)
        body = lines[0] if len(lines) == 2 else output
        code = int(lines[1]) if len(lines) == 2 else 0
        return json.loads(body) if body.strip() else {}, code
    except Exception as e:
        return None, str(e)

def curl_post(url, data=None, timeout=15):
    """通过 curl + 代理发送 POST 请求"""
    cmd = ['curl', '-s', '-X', 'POST', '--connect-timeout', str(timeout), '-w', '\n%{http_code}', url]
    if proxy:
        cmd[1:1] = ['-x', proxy]
    if data:
        for k, v in data.items():
            cmd[1:1] = ['-d', f'{k}={v}']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        output = result.stdout.strip()
        if not output:
            return None, None
        lines = output.rsplit('\n', 1)
        body = lines[0] if len(lines) == 2 else output
        code = int(lines[1]) if len(lines) == 2 else 0
        return json.loads(body) if body.strip() else {}, code
    except Exception as e:
        return None, str(e)

print()
print('=== Meta 凭据验证 (curl) ===')
print(f'代理: {proxy if proxy else "直连"}')
print()

if not app_id:
    print('[FAIL] App ID 未填写')
    input(); sys.exit(1)
if not app_secret:
    print('[FAIL] App Secret 未填写')
    input(); sys.exit(1)

print(f'App ID: {app_id[:8]}...')
print(f'App Secret: {"*" * len(app_secret)}')
print()

# 1. 验证凭据
print('>>> 1/4 验证 App ID + Secret...')
data, status = curl_get(
    f'https://graph.facebook.com/v25.0/oauth/access_token'
    f'?client_id={app_id}&client_secret={app_secret}&grant_type=client_credentials'
)
if data and 'access_token' in data:
    print('[OK] 凭据正确')
    app_token = data['access_token']
elif data:
    err = data.get('error', {})
    print(f'[FAIL] {err.get("message", data)}')
    input(); sys.exit(1)
else:
    print(f'[FAIL] 连接失败: {status}')
    input(); sys.exit(1)

# 应用名
data, _ = curl_get(f'https://graph.facebook.com/v25.0/{app_id}?access_token={app_token}&fields=id,name')
if data:
    print(f'[OK] 应用: {data.get("name", "未知")}')
print()

# 2. Token
print('>>> 2/4 验证 Token...')
if not token:
    print('[WARN] 未填写 Token')
    input(); sys.exit(0)

data, _ = curl_get(f'https://graph.facebook.com/v25.0/debug_token?input_token={token}&access_token={app_token}')
debug = (data or {}).get('data', {})

if debug.get('is_valid'):
    scopes = debug.get('scopes', [])
    print(f'[OK] Token 有效')
    print(f'  权限: {", ".join(scopes[:8])}...' if len(scopes) > 8 else f'  权限: {", ".join(scopes)}')
    missing = [p for p in ['ads_read', 'ads_management', 'business_management'] if p not in scopes]
    if missing:
        print(f'[WARN] 缺少: {", ".join(missing)}')
    else:
        print('[OK] 三项必需权限齐全')
else:
    print(f'[FAIL] Token 无效: {debug}')
    input(); sys.exit(1)

print()

# 3. 拉取账户
print('>>> 3/4 拉取广告账户...')
data, _ = curl_get(f'https://graph.facebook.com/v25.0/me/adaccounts?access_token={token}&fields=id,name,account_status&limit=50')
if data and 'error' in data:
    print(f'[FAIL] {data["error"].get("message", data)}')
elif data:
    accts = data.get('data', [])
    print(f'[OK] 直连: {len(accts)} 个账户')
    for a in accts[:20]:
        status = 'active' if a.get('account_status') == 1 else 'disabled'
        print(f'  {a.get("id")} - {a.get("name")} [{status}]')
else:
    print(f'[FAIL] 请求失败')

print()

# 4. BM
print('>>> 4/4 拉取 BM...')
data, _ = curl_get(f'https://graph.facebook.com/v25.0/me/businesses?access_token={token}&fields=id,name&limit=20')
if data and 'error' in data:
    print(f'[FAIL] {data["error"].get("message", data)}')
elif data:
    bms = data.get('data', [])
    print(f'[OK] {len(bms)} 个 BM')
    total = 0
    for b in bms:
        bm_id = b.get('id', '')
        bm_name = b.get('name', '')
        d1, _ = curl_get(f'https://graph.facebook.com/v25.0/{bm_id}/owned_ad_accounts?access_token={token}&fields=id,name&limit=50')
        oa = d1.get('data', []) if d1 else []
        d2, _ = curl_get(f'https://graph.facebook.com/v25.0/{bm_id}/client_ad_accounts?access_token={token}&fields=id,name&limit=50')
        ca = d2.get('data', []) if d2 else []
        total += len(oa) + len(ca)
        print(f'  {bm_id} - {bm_name}: 自有{len(oa)} + 代投{len(ca)}')
        for a in oa[:5]:
            print(f'    [自有] {a.get("id")} - {a.get("name")}')
        for a in ca[:5]:
            print(f'    [代投] {a.get("id")} - {a.get("name")}')
    print(f'[OK] BM 下共 {total} 个账户')
    print(f'[OK] 总计可发现: {len(accts) + total} 个')

print()
print('=== 验证完成 ===')
input('\n按回车退出...')
