# -*- coding: utf-8 -*-
"""探测 geeknow 代理可用的视频模型（一次运行，不属于系统功能）。"""
import json, subprocess, tempfile, os, sys

sys.stdout.reconfigure(encoding='utf-8')
cfg = json.load(open('config.json', encoding='utf-8'))
key = cfg['api_key'].strip()
base = cfg['api_url'].rstrip('/')
proxy = (cfg.get('meta') or {}).get('proxy', '')
print('base:', base, '| proxy:', repr(proxy), '| key tail:', repr(key[-6:]))


def post(model, use_proxy=True, path='/chat/completions'):
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
    tf = tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w', encoding='utf-8')
    tf.write(payload)
    tf.close()
    cmd = ['curl', '-s', '-m', '60', '-X', 'POST', '-w', '\n%{http_code}']
    if use_proxy and proxy:
        cmd += ['-x', proxy]
    cmd += ['-H', 'Content-Type: application/json', '-H', 'Authorization: Bearer ' + key,
            '-d', '@' + tf.name, base + path]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
    os.unlink(tf.name)
    body, _, code = out.rpartition('\n')
    print(f'--- {model} (proxy={use_proxy}): HTTP {code.strip()} | {body.strip()[:200]}')


post('gemini-3.5-flash', use_proxy=True)
post('gemini-3.5-flash', use_proxy=False)
for m in ['sora-2', 'sora-2-pro', 'kling-v2-master', 'kling-v1-6', 'veo3', 'runway-gen3-turbo', 'pika-v2', 'wan2.5-t2v']:
    post(m)
