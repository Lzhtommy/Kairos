#!/usr/bin/env sh
# ECS 侧的回补执行器：停 api → 一次性容器跑脚本 → 无论成败拉回 api。
# 由 Backfill workflow 以 nohup 方式调用（SSH 掉线不影响执行），
# 完成后把退出码写入 /tmp/kairos-backfill.done 供 workflow 轮询。
#
# 用法：run_backfill.sh factors 500   # 因子快照回补 N 个交易日
#       run_backfill.sh kline 1250    # 日 K 深度回补到 N 根（断点续跑）
#       run_backfill.sh 500           # 兼容旧调用 = factors 500
MODE="${1:-factors}"
ARG="$2"
case "$MODE" in
  *[!0-9]*) ;;                        # 非纯数字 → 就是模式名
  *) ARG="$MODE"; MODE="factors" ;;   # 纯数字 → 旧调用风格
esac
case "$MODE" in
  factors)    CMD="python -m scripts.backfill_factors --days ${ARG:-300}" ;;
  kline)      CMD="python -m scripts.backfill_kline --bars ${ARG:-1250} --only-missing" ;;
  kline-full) CMD="python -m scripts.backfill_kline --bars ${ARG:-1250}" ;;  # 季度 qfq 重刷
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
cd /opt/kairos || exit 1
rm -f /tmp/kairos-backfill.done
docker compose stop api
docker compose run --rm -T api $CMD > /tmp/kairos-backfill.log 2>&1
status=$?
docker compose start api
echo "$status" > /tmp/kairos-backfill.done
exit "$status"
