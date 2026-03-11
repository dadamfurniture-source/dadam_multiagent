#!/bin/bash
# ============================================================
# 다담 SaaS 자동 저장 스크립트
# - 5분 주기로 변경사항 자동 커밋
# - 사용법: bash scripts/auto-save.sh &
# - 중지: kill $(cat .auto-save.pid)
# ============================================================

INTERVAL=${1:-300}  # 기본 5분 (초)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJECT_DIR/.auto-save.pid"
LOG_FILE="$PROJECT_DIR/.auto-save.log"

cd "$PROJECT_DIR" || exit 1

# 이미 실행중이면 종료
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Auto-save already running (PID: $OLD_PID). Stop with: kill $OLD_PID"
    exit 1
  fi
fi

echo $$ > "$PID_FILE"
echo "[$(date)] Auto-save started (PID: $$, interval: ${INTERVAL}s)" | tee -a "$LOG_FILE"

cleanup() {
  rm -f "$PID_FILE"
  echo "[$(date)] Auto-save stopped" | tee -a "$LOG_FILE"
  exit 0
}
trap cleanup SIGINT SIGTERM

auto_commit() {
  # 변경사항 확인
  if [ -z "$(git status --porcelain)" ]; then
    return 0  # 변경 없음
  fi

  CHANGED=$(git status --porcelain | wc -l)
  TIMESTAMP=$(date +"%Y-%m-%d %H:%M")

  # 스테이징 (.env, .venv 등 제외)
  git add -A
  git reset -- .env .venv/ __pycache__/ *.pyc .auto-save.pid .auto-save.log 2>/dev/null

  # 변경사항이 스테이징에 있는지 재확인
  if [ -z "$(git diff --cached --name-only)" ]; then
    return 0
  fi

  # 커밋
  git commit -m "auto-save: ${TIMESTAMP} (${CHANGED} files changed)" --no-verify 2>/dev/null

  echo "[$(date)] Auto-saved: ${CHANGED} files" | tee -a "$LOG_FILE"
}

# 메인 루프
while true; do
  auto_commit
  sleep "$INTERVAL"
done
