#!/usr/bin/env bash
# 3D Bot — start on Render
set -e
echo "=== 3D Bot starting on 0.0.0.0:${PORT:-10000} ==="
exec gunicorn -w 1 -b "0.0.0.0:${PORT:-10000}" --timeout 120 app:flask_app
