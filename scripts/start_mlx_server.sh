#!/bin/bash
# scripts/start_mlx_server.sh
# Ensures the local Apple Silicon MLX LoRA server is running on port 8080.

set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8080
LOG_FILE="logs/mlx_server.log"
MODEL="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
ADAPTER="adapters/bayesian-pivot-lora"
PYTHON_BIN="./venv_train/bin/python"

mkdir -p logs

if curl -s -f "http://127.0.0.1:${PORT}/v1/models" > /dev/null 2>&1; then
    echo "✔ Apple Silicon MLX LoRA server is already online and healthy on port ${PORT}."
    exit 0
fi

echo "🚀 Starting Apple Silicon MLX LoRA server daemon on port ${PORT}..."
echo "Base model: ${MODEL}"
echo "Adapter:    ${ADAPTER}"
echo "Log file:   ${LOG_FILE}"

nohup "${PYTHON_BIN}" -m mlx_lm server \
    --model "${MODEL}" \
    --adapter-path "${ADAPTER}" \
    --port "${PORT}" >> "${LOG_FILE}" 2>&1 &

PID=$!
echo "Server spawned with PID ${PID}. Awaiting health check..."

for i in $(seq 1 15); do
    if curl -s -f "http://127.0.0.1:${PORT}/v1/models" > /dev/null 2>&1; then
        echo "✔ MLX LoRA server successfully launched and ready on http://127.0.0.1:${PORT} (PID ${PID})."
        exit 0
    fi
    sleep 1
done

echo "⚠️ Server did not respond within 15 seconds. Check logs at ${LOG_FILE}."
exit 1
