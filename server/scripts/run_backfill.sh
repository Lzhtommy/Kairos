#!/usr/bin/env sh
# ECS 侧的因子回补执行器：停 api → 一次性容器跑脚本 → 无论成败拉回 api。
# 由 Backfill Factors workflow 以 nohup 方式调用（SSH 掉线不影响执行），
# 完成后把退出码写入 /tmp/kairos-backfill.done 供 workflow 轮询。
DAYS="${1:-300}"
cd /opt/kairos || exit 1
rm -f /tmp/kairos-backfill.done
docker compose stop api
docker compose run --rm -T api python -m scripts.backfill_factors --days "$DAYS" \
  > /tmp/kairos-backfill.log 2>&1
status=$?
docker compose start api
echo "$status" > /tmp/kairos-backfill.done
exit "$status"
