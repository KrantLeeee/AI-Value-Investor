#!/usr/bin/env bash
# ============================================================================
# Phase 2: Batch Report Generation (纯本地，无网络依赖)
# ============================================================================
# 前提：已运行 batch_fetch.sh，数据库有数据
# 此脚本不请求东财，只调用 LLM 生成报告
#
# 用法：
#   ./scripts/batch_report.sh              # 生成全部
#   ./scripts/batch_report.sh 10           # 只生成前 10 只
#   ./scripts/batch_report.sh --quick      # 快速模式（无 LLM）

set -euo pipefail

PROJECT_ROOT="/Users/krantlee/Documents/Study/Vibe Coding Experiments/AI Value Investment"
REPORTS_DIR="$PROJECT_ROOT/output/reports"
LOG_DIR="$PROJECT_ROOT/output/logs"
TODAY=$(date +%Y-%m-%d)
TARGET_DIR="$REPORTS_DIR/batch_${TODAY}"
LOG_FILE="$LOG_DIR/report_${TODAY}.log"

cd "$PROJECT_ROOT"
mkdir -p "$TARGET_DIR" "$LOG_DIR"

# ── 股票列表（与 batch_fetch.sh 保持一致）────────────────────────────────
TICKERS=(
  "600519.SH"   # 贵州茅台
  "000858.SZ"   # 五粮液
  "601318.SH"   # 中国平安
  "601398.SH"   # 工商银行
  "600036.SH"   # 招商银行
  "002714.SZ"
  "601088.SH"
  "601138.SH"
  "600900.SH"
  "601668.SH"
  "601933.SH"
  "600018.SH"
  "603043.SH"
  "600941.SH"
  "600048.SH"
  "600705.SH"
  "688066.SH"
  "300070.SZ"
  "001323.SZ"
  "605098.SH"
  "300760.SZ"
  "002739.SZ"
  "600604.SH"
  "601857.SH"
  "600309.SH"
  "600019.SH"
  "601899.SH"
  "002594.SZ"
  "000333.SZ"
  "603839.SH"
  "002078.SZ"
  "600276.SH"
  "600585.SH"
  "603136.SH"
  "600893.SH"
  "603189.SH"
  "002027.SZ"
  "000063.SZ"
  "300750.SZ"
  "002032.SZ"
  "300896.SZ"
  "601808.SH"
)

# ── 参数解析 ────────────────────────────────────────────────────────────
QUICK_MODE=""
LIMIT=${#TICKERS[@]}

for arg in "$@"; do
  case $arg in
    --quick)
      QUICK_MODE="--quick"
      shift
      ;;
    [0-9]*)
      LIMIT=$arg
      shift
      ;;
  esac
done

TOTAL=${#TICKERS[@]}
if [ "$LIMIT" -lt "$TOTAL" ]; then
  TOTAL=$LIMIT
fi

echo "========================================" | tee -a "$LOG_FILE"
echo "📝 Batch Report Start: $(date)" | tee -a "$LOG_FILE"
echo "   Tickers: $TOTAL" | tee -a "$LOG_FILE"
echo "   Mode: ${QUICK_MODE:-'Full LLM'}" | tee -a "$LOG_FILE"
echo "   Output: $TARGET_DIR" | tee -a "$LOG_FILE"
echo "   Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

SUCCESS=0
FAILED=()

for i in "${!TICKERS[@]}"; do
  if [ "$i" -ge "$LIMIT" ]; then
    break
  fi

  TICKER="${TICKERS[$i]}"
  IDX=$((i + 1))
  SAFE_TICKER="${TICKER//./_}"

  echo "" | tee -a "$LOG_FILE"
  echo "[$IDX/$TOTAL] 📝 Generating report for $TICKER ..." | tee -a "$LOG_FILE"
  echo "   Time: $(date +%H:%M:%S)" | tee -a "$LOG_FILE"

  # 生成报告（--skip-confirm 跳过确认，自动化模式）
  if poetry run invest report --ticker "$TICKER" --skip-confirm $QUICK_MODE >> "$LOG_FILE" 2>&1; then
    echo "   ✅ Report generated" | tee -a "$LOG_FILE"

    # 移动报告到目标目录
    REPORT_FILE=$(ls "$REPORTS_DIR/${SAFE_TICKER}_"*.md 2>/dev/null | head -1 || true)
    if [ -n "$REPORT_FILE" ]; then
      mv "$REPORT_FILE" "$TARGET_DIR/"
      echo "   📁 Moved to: $TARGET_DIR/$(basename "$REPORT_FILE")" | tee -a "$LOG_FILE"
    fi
    SUCCESS=$((SUCCESS + 1))
  else
    echo "   ❌ Failed" | tee -a "$LOG_FILE"
    FAILED+=("$TICKER")
  fi
done

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "📝 Batch Report Complete: $(date)" | tee -a "$LOG_FILE"
echo "   Success: $SUCCESS / $TOTAL" | tee -a "$LOG_FILE"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "   Failed: ${FAILED[*]}" | tee -a "$LOG_FILE"
fi
echo "   Reports: $TARGET_DIR" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
