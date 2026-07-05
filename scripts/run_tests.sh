#!/bin/bash
set -uo pipefail

SRC_DIR="/mnt/d/code/aiops-traffic-shaper"
DEST_DIR="$HOME/aiops-traffic-shaper"

echo "===================================================="
echo "BƯỚC 1: Kiểm tra Redis local"
echo "===================================================="
if redis-cli ping > /dev/null 2>&1; then
    echo "Redis đang chạy (PONG nhận được)"
else
    echo "Redis chưa chạy, đang khởi động..."
    sudo service redis-server start
    sleep 1
    if redis-cli ping > /dev/null 2>&1; then
        echo "Redis đã khởi động thành công"
    else
        echo "LỖI: Không khởi động được Redis. Kiểm tra 'sudo apt-get install redis-server' đã chạy chưa."
        exit 1
    fi
fi

echo ""
echo "===================================================="
echo "BƯỚC 2: Copy project sang ổ Linux gốc (~/) để tránh /mnt/d chậm"
echo "===================================================="
if [ -d "$DEST_DIR" ]; then
    echo "Đã tồn tại $DEST_DIR, đang xoá bản cũ để copy lại bản mới nhất..."
    rm -rf "$DEST_DIR"
fi
cp -r "$SRC_DIR" "$DEST_DIR"
cd "$DEST_DIR" || { echo "LỖI: không cd được vào $DEST_DIR"; exit 1; }
echo "Đã copy xong, đang làm việc tại: $(pwd)"

echo ""
echo "===================================================="
echo "BƯỚC 3: Tạo venv và cài dependencies (có thể mất 30-60s)"
echo "===================================================="
rm -rf .venv
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q \
    -r services/ai_engine/requirements.txt \
    -r services/worker_orchestrator/requirements.txt \
    pytest pytest-asyncio anyio trio
echo "Cài dependencies xong."

echo ""
echo "===================================================="
echo "BƯỚC 4a: Chạy pytest cho AI Engine"
echo "===================================================="
PYTHONPATH=. INTERNAL_SECRET=test-secret-for-ci REDIS_HOST=localhost \
    ./.venv/bin/python -m pytest tests/ai-engine/ -v
AI_ENGINE_RESULT=$?

echo ""
echo "===================================================="
echo "BƯỚC 4b: Chạy pytest cho Worker Orchestrator"
echo "===================================================="
PYTHONPATH=. INTERNAL_SECRET=test-secret-for-ci REDIS_HOST=localhost \
    ./.venv/bin/python -m pytest tests/worker-orchestrator/ -v
WORKER_RESULT=$?

echo ""
echo "===================================================="
echo "TỔNG KẾT"
echo "===================================================="
if [ $AI_ENGINE_RESULT -eq 0 ]; then
    echo "AI Engine:          PASS"
else
    echo "AI Engine:          FAIL (exit code $AI_ENGINE_RESULT)"
fi

if [ $WORKER_RESULT -eq 0 ]; then
    echo "Worker Orchestrator: PASS"
else
    echo "Worker Orchestrator: FAIL (exit code $WORKER_RESULT)"
fi

echo ""
echo "Project đã được copy sang: $DEST_DIR"
echo "Từ giờ làm việc (git commit/push) ở thư mục này, không phải $SRC_DIR"

if [ $AI_ENGINE_RESULT -eq 0 ] && [ $WORKER_RESULT -eq 0 ]; then
    exit 0
else
    exit 1
fi
