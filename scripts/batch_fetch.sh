#!/usr/bin/env bash
# ============================================================================
# Phase 1: Batch Data Fetch (深夜低频抓取)
# ============================================================================
# 建议运行时间：深夜 2:00-6:00（东财服务器负载低）
# 每只股票间隔 5 分钟，模拟散户缓慢浏览
#
# 用法：
#   ./scripts/batch_fetch.sh              # 抓取全部
#   ./scripts/batch_fetch.sh 10           # 只抓取前 10 只
#   DELAY_MINUTES=3 ./scripts/batch_fetch.sh  # 自定义间隔（分钟）

set -euo pipefail

PROJECT_ROOT="/Users/krantlee/Documents/Study/Vibe Coding Experiments/AI Value Investment"
LOG_DIR="$PROJECT_ROOT/output/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/fetch_${TODAY}.log"

cd "$PROJECT_ROOT"
mkdir -p "$LOG_DIR"

# ── Rate Limiting (传递给 Python) ────────────────────────────────────────
export FETCH_DELAY=3.0
export FETCH_DELAY_BETWEEN_SOURCES=2.0
export FETCH_DELAY_BETWEEN_TYPES=2.0
export FETCH_DELAY_BETWEEN_TICKERS=5.0

# ── 跳过 AKShare（避免 eastmoney.com 封 IP）────────────────────────────────
# 数据源优先级变为：Tushare → BaoStock → Sina → QVeris
export SKIP_AKSHARE=true

# ── 股票列表 ────────────────────────────────────────────────────────────
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
LIMIT=${1:-${#TICKERS[@]}}  # 默认全部
DELAY_MINUTES=${DELAY_MINUTES:-5}  # 默认 5 分钟
DELAY_SECONDS=$((DELAY_MINUTES * 60))

TOTAL=${#TICKERS[@]}
if [ "$LIMIT" -lt "$TOTAL" ]; then
  TOTAL=$LIMIT
fi

echo "========================================" | tee -a "$LOG_FILE"
echo "📊 Batch Fetch Start: $(date)" | tee -a "$LOG_FILE"
echo "   Tickers: $TOTAL" | tee -a "$LOG_FILE"
echo "   Delay: ${DELAY_MINUTES} minutes between tickers" | tee -a "$LOG_FILE"
echo "   Estimated time: $((TOTAL * DELAY_MINUTES)) minutes" | tee -a "$LOG_FILE"
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

  # 股票间延迟（除了第一只）
  if [ "$i" -gt 0 ]; then
    echo "" | tee -a "$LOG_FILE"
    echo "⏳ Waiting ${DELAY_MINUTES} minutes before next ticker..." | tee -a "$LOG_FILE"
    sleep "$DELAY_SECONDS"
  fi

  echo "" | tee -a "$LOG_FILE"
  echo "[$IDX/$TOTAL] 📈 Fetching $TICKER ..." | tee -a "$LOG_FILE"
  echo "   Time: $(date +%H:%M:%S)" | tee -a "$LOG_FILE"

  if poetry run invest fetch --ticker "$TICKER" >> "$LOG_FILE" 2>&1; then
    echo "   ✅ Success" | tee -a "$LOG_FILE"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "   ❌ Failed" | tee -a "$LOG_FILE"
    FAILED+=("$TICKER")
  fi
done

echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "📊 Batch Fetch Complete: $(date)" | tee -a "$LOG_FILE"
echo "   Success: $SUCCESS / $TOTAL" | tee -a "$LOG_FILE"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "   Failed: ${FAILED[*]}" | tee -a "$LOG_FILE"
fi
echo "========================================" | tee -a "$LOG_FILE"

# 完成后发送通知（可选）
# poetry run invest notify --message "Batch fetch complete: $SUCCESS/$TOTAL"
