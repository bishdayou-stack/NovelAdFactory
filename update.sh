#!/bin/bash
# Novel Ad Factory 更新脚本（Linux / Ubuntu）
cd "$(dirname "$0")"

echo "[1/3] 停止服务..."
pkill -f "uvicorn main:app" 2>/dev/null
sleep 1

echo "[2/3] 备份本地配置..."
cp config.json config.local.json 2>/dev/null

echo "[3/3] 从 GitHub 拉取最新代码..."
git pull origin master

# 恢复本地配置（保留服务器上的 proxy 等 meta 配置）
if [ -f config.local.json ]; then
    python3 -c "import json; bak=json.load(open('config.local.json')); cur=json.load(open('config.json')); cur['meta']=bak.get('meta',{}); json.dump(cur,open('config.json','w'),ensure_ascii=False,indent=2)" 2>/dev/null
    rm config.local.json
fi

echo "更新完成，请运行 bash start.sh 重新启动"
