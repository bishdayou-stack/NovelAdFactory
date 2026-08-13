#!/bin/bash
# Novel Ad Factory 启动脚本（Linux / Ubuntu）
cd "$(dirname "$0")"

# 安装依赖（首次运行）
if [ ! -f .dep_installed ]; then
    echo "安装依赖..."
    pip install -r requirements.txt
    touch .dep_installed
fi

# 启动服务
echo "启动服务..."
nohup uvicorn main:app --host 0.0.0.0 --port 8000 >> server.log 2>&1 &
echo "服务已启动，日志: server.log"
echo "访问: http://服务器IP:8000/static/index.html"
