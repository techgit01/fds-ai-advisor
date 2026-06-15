#!/usr/bin/env bash
# FDS AI 상담사 — 서버 실행 스크립트
set -euo pipefail

PORT=${PORT:-5000}

# 포트 충돌 해소
if lsof -ti:"$PORT" &>/dev/null; then
  echo "포트 $PORT 사용 중 → 기존 프로세스 종료"
  lsof -ti:"$PORT" | xargs kill -9
  sleep 0.5
fi

# Python 실행 파일 탐색
if [[ -f ".venv/bin/python3" ]]; then
  PYTHON=".venv/bin/python3"
elif command -v uv &>/dev/null; then
  PYTHON="uv run python"
else
  echo "오류: .venv 또는 uv를 찾을 수 없습니다. setup.sh를 먼저 실행하세요." >&2
  exit 1
fi

echo "→ http://localhost:${PORT}"
exec $PYTHON src/app.py
