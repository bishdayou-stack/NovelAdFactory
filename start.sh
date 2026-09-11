#!/bin/bash
# Novel Ad Factory 启动脚本（Linux / Ubuntu）
cd "$(dirname "$0")"

# 挑一个真的装了 uvicorn 的解释器。裸命令 `uvicorn` 常常不在 PATH 里（宝塔的 python 装在
# /www/server/pyporject_evn/versions/…），直接调用只会让 nohup 报 "No such file or
# directory"，而脚本照样往下走 —— 生产上因此静默留着旧进程在跑，还打印"服务已启动"。
PY=""
for cand in "$PYTHON_BIN" /www/server/pyporject_evn/versions/*/bin/python3* python3 python; do
    [ -n "$cand" ] || continue
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import uvicorn" >/dev/null 2>&1; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "找不到装了 uvicorn 的 Python，请用 PYTHON_BIN=/path/to/python 指定解释器"; exit 1
fi
echo "使用解释器: $PY"

# 安装依赖：requirements.txt 比标记新就重装。原来只看标记在不在，
# 所以新加的依赖（如 cryptography）永远装不上。
if [ ! -f .dep_installed ] || [ requirements.txt -nt .dep_installed ]; then
    echo "安装依赖..."
    "$PY" -m pip install -r requirements.txt || { echo "依赖安装失败，中止"; exit 1; }
    touch .dep_installed
fi

# 端口被占时新进程绑不上、直接退出，却看不到任何提示 —— 先拦住
if ss -lntp 2>/dev/null | grep -q ':8000 '; then
    echo "端口 8000 已被占用，先执行: pkill -f 'uvicorn main:app'"; exit 1
fi

echo "启动服务..."
nohup "$PY" -m uvicorn main:app --host 0.0.0.0 --port 8000 >> server.log 2>&1 &
sleep 3

# 起没起来要看端口通不通，不看 nohup 有没有返回
if curl -sf -o /dev/null http://127.0.0.1:8000/static/index.html; then
    echo "服务已启动，日志: server.log"
    echo "访问: http://服务器IP:8000/static/index.html"
else
    echo "启动失败，看 tail -n 20 server.log（别信任何“服务已启动”的字样）"
    exit 1
fi
