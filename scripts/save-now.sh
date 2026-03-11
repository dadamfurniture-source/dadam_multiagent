#!/bin/bash
# ============================================================
# 즉시 저장 (중요 작업 후 수동 호출용)
# - 사용법: bash scripts/save-now.sh "저장 메시지"
# ============================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

MSG="${1:-checkpoint: $(date +"%Y-%m-%d %H:%M")}"

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to save."
  exit 0
fi

CHANGED=$(git status --porcelain | wc -l)

git add -A
git reset -- .env .venv/ __pycache__/ *.pyc .auto-save.pid .auto-save.log 2>/dev/null

if [ -z "$(git diff --cached --name-only)" ]; then
  echo "No changes to save."
  exit 0
fi

git commit -m "$MSG" --no-verify
echo "Saved: ${CHANGED} files — $MSG"
