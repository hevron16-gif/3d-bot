#!/bin/bash
# AutoDiag AI v1.0 — Запуск сервера
set -e

echo "=== AutoDiag AI v1.0.9 ==="
echo "Starting server on 0.0.0.0:${PORT:-8000}"

exec python main.py
