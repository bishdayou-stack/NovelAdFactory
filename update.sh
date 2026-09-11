#!/bin/bash
# Novel Ad Factory 更新脚本（Linux / Ubuntu）
cd "$(dirname "$0")"

echo "[1/3] 停止服务..."
pkill -f "uvicorn main:app" 2>/dev/null
sleep 1

echo "[2/3] 备份本地配置..."
cp config.json config.local.json 2>/dev/null

echo "[3/3] 从 GitHub 拉取最新代码..."
# config.json 在服务器上被本地改过（真密钥），只要新版本也动了这个文件，git 就会拒绝
# pull。先让开（已备份），拉完再把服务器的值合回去。
git checkout -- config.json 2>/dev/null
if ! git pull origin master; then
    echo "拉取失败（网络不通或仍有冲突）。配置已还原，服务当前处于停止状态。"
    cp config.local.json config.json 2>/dev/null
    exit 1
fi

# 恢复本地配置：以模板为底，服务器上的 meta 值优先，模板新增的键（如 pingykj_sites）补齐
if [ -f config.local.json ]; then
    python3 - <<'PYEOF'
import json
new = json.load(open('config.json'))          # 刚拉下来的模板
old = json.load(open('config.local.json'))    # 服务器原本的（真密钥）
meta = new.setdefault('meta', {})
for k, v in (old.get('meta') or {}).items():
    meta[k] = v
json.dump(new, open('config.json', 'w'), ensure_ascii=False, indent=2)
PYEOF
    rm -f config.local.json
fi

echo "更新完成，请运行 bash start.sh 重新启动"
