@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo   Meta 凭据验证工具
echo ==========================================
echo.
python -c "
import json, requests, sys

config = json.loads(open('config.json', encoding='utf-8').read())
meta = config.get('meta', {})

app_id = meta.get('app_id', '').strip()
app_secret = meta.get('app_secret', '').strip()
token = meta.get('default_access_token', '').strip()

print('=== Meta 凭据验证 ===')
print()

if not app_id:
    print('[FAIL] App ID 未填写 —— 请在「Meta 数据」Tab 填写后保存')
    input(); sys.exit(1)
if not app_secret:
    print('[FAIL] App Secret 未填写 —— 请在「Meta 数据」Tab 填写后保存')
    input(); sys.exit(1)

print(f'App ID: {app_id[:8]}... ✓')
print(f'App Secret: {\"*\" * len(app_secret)} ✓')
print()

# 验证 App ID + Secret
try:
    resp = requests.get('https://graph.facebook.com/v25.0/oauth/access_token', params={
        'client_id': app_id, 'client_secret': app_secret, 'grant_type': 'client_credentials'
    }, timeout=15)
    data = resp.json()
    if 'access_token' in data:
        print('[OK] App ID + App Secret 验证通过')
        app_token = data['access_token']
    else:
        err = data.get('error', {})
        print(f'[FAIL] 凭据验证失败: {err.get(\"message\", err)}')
        print('建议: 检查复制的 App ID 和 Secret 是否有空格/遗漏字符')
        input(); sys.exit(1)
except Exception as e:
    print(f'[FAIL] 无法连接 Meta API: {e}')
    print('请确保网络能访问 graph.facebook.com')
    input(); sys.exit(1)

# 查看应用名称
try:
    resp = requests.get(f'https://graph.facebook.com/v25.0/{app_id}', params={
        'access_token': app_token, 'fields': 'id,name'
    }, timeout=15)
    data = resp.json()
    print(f'[OK] 应用名称: {data.get(\"name\", \"未知\")}')
except Exception:
    pass

print()

# 验证 User Token
if not token:
    print('[WARN] 未填写 Access Token')
    print('请在「Meta 数据」Tab 填写后保存，再运行本工具')
    input(); sys.exit(0)

try:
    resp = requests.get('https://graph.facebook.com/v25.0/debug_token', params={
        'input_token': token, 'access_token': app_token
    }, timeout=15)
    data = resp.json()
    debug = data.get('data', {})
    if debug.get('is_valid'):
        scopes = debug.get('scopes', [])
        expires = debug.get('expires_at', 0)
        print(f'[OK] Access Token 有效')
        print(f'  过期时间戳: {expires}')
        print(f'  权限列表: {\", \".join(scopes) if scopes else \"(无)\"}')
        missing = [p for p in ['ads_read', 'ads_management', 'business_management'] if p not in scopes]
        if missing:
            print(f'')
            print(f'[WARN] ⚠ 缺少以下权限: {\", \".join(missing)}')
            print(f'')
            print(f'修复方法:')
            print(f'  1. 打开 https://developers.facebook.com/tools/explorer')
            print(f'  2. 选择你的应用')
            print(f'  3. 点击「添加权限」→ 勾选 {", ".join(missing)}')
            print(f'  4. 点击「Generate Access Token」')
            print(f'  5. 复制新 Token 到系统「Meta 数据」Tab')
        else:
            print(f'  [OK] 三项必需权限齐全 ✓')

        # 测试发现账户
        print(f'')
        print(f'--- 测试拉取广告账户 ---')
        try:
            resp2 = requests.get('https://graph.facebook.com/v25.0/me/adaccounts', params={
                'access_token': token,
                'fields': 'id,name,account_status',
                'limit': '50'
            }, timeout=15)
            d2 = resp2.json()
            if 'error' in d2:
                print(f'[FAIL] 拉取账户失败: {d2[\"error\"].get(\"message\", d2)}')
            else:
                accts = d2.get('data', [])
                print(f'[OK] 直接可访问的广告账户: {len(accts)} 个')
                for a in accts[:5]:
                    print(f'      {a.get(\"id\")} - {a.get(\"name\")}')
                if len(accts) > 5:
                    print(f'      ... 共 {len(accts)} 个')
        except Exception as e:
            print(f'[FAIL] 拉取账户异常: {e}')

        # 测试 BM
        print(f'')
        print(f'--- 测试拉取 BM ---')
        try:
            resp3 = requests.get('https://graph.facebook.com/v25.0/me/businesses', params={
                'access_token': token,
                'fields': 'id,name',
                'limit': '10'
            }, timeout=15)
            d3 = resp3.json()
            if 'error' in d3:
                print(f'[FAIL] 拉取 BM 失败: {d3[\"error\"].get(\"message\", d3)}')
                print(f'  可能缺少 business_management 权限')
            else:
                bms = d3.get('data', [])
                print(f'[OK] 可访问的 BM: {len(bms)} 个')
                for b in bms:
                    print(f'      {b.get(\"id\")} - {b.get(\"name\")}')
                if bms:
                    print(f'')
                    print(f'--- 测试 BM 下账户 ---')
                    for b in bms[:3]:
                        try:
                            resp4 = requests.get(f'https://graph.facebook.com/v25.0/{b[\"id\"]}/owned_ad_accounts', params={
                                'access_token': token, 'fields': 'id,name', 'limit': '50'
                            }, timeout=15)
                            d4 = resp4.json()
                            b_accts = d4.get('data', [])
                            print(f'  BM {b.get(\"name\")}: {len(b_accts)} 个自有账户')
                        except Exception:
                            pass
        except Exception as e:
            print(f'[FAIL] 拉取 BM 异常: {e}')
    else:
        err = debug.get('error', {})
        print(f'[FAIL] Token 无效: {err.get(\"message\", debug)}')
        print(f'建议: 重新生成 Token，确保勾选 ads_read + ads_management + business_management')
except Exception as e:
    print(f'[FAIL] Token 验证失败: {e}')

print()
print('==========================================')
print('验证完成，按任意键退出')
"
pause
