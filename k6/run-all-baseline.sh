#!/usr/bin/env bash
# Chạy tuần tự 3 baseline scenarios + ghi kết quả vào folder results/
# Usage: VOTE_URL=http://20.189.249.7:31000 ./run-all-baseline.sh

set -euo pipefail

VOTE_URL="${VOTE_URL:-http://20.189.249.7:31000}"
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="results/$TS"
mkdir -p "$OUT_DIR"

echo "=== Output dir: $OUT_DIR ==="
echo "=== Vote URL: $VOTE_URL ==="

run_test() {
  local name="$1"
  local script="$2"
  echo
  echo "###########################################"
  echo "### Bắt đầu: $name"
  echo "### Script: $script"
  echo "### Started: $(date -Iseconds)"
  echo "###########################################"

  k6 run \
    -e VOTE_URL="$VOTE_URL" \
    --summary-export="$OUT_DIR/${name}-summary.json" \
    "$script" 2>&1 | tee "$OUT_DIR/${name}.log"

  echo "### Done $name at $(date -Iseconds)"
  echo "### Sleep 60s để hệ thống ổn định trước test tiếp theo..."
  sleep 60
}

run_test "normal" "baseline-normal.js"
run_test "medium" "baseline-medium.js"
run_test "spike"  "baseline-spike.js"

echo
echo "=== Tất cả baseline tests xong. Kết quả tại: $OUT_DIR ==="
ls -la "$OUT_DIR"
