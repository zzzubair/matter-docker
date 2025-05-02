#!/usr/bin/env bash

set -euo pipefail

BM="./network-benchmark.py"
COMMON="--sample 200 --ping 3 --iperf 3"

# name  streams  workers  profile
SCENARIOS=(
  "baseline                  1  6   permissive"
  "baseline_selective        1  6   selective"
  "baseline_selective_log    1  6   selective_log"
  "scaled_low_load           1  16  permissive"
  "scaled_high_load          4  16  permissive"
  "scaled_selective_low      1  16  selective"
  "scaled_selective_high     4  16  selective"
  "scaled_selective_low_log  1  16  selective_log"
  "scaled_selective_high_log 4  16  selective_log"
)

for REP in 1 2 3; do
  for entry in "${SCENARIOS[@]}"; do
    read -r NAME STREAMS WORKERS PROFILE <<<"$entry"
    RUN="${NAME}_run${REP}"

    echo
    echo "════════  $RUN  ════════"
    docker exec router reload-rules-ip "$PROFILE"

    EXTRA=""
    if [[ "$PROFILE" == selective* ]]; then
      EXTRA="--cross-zone-only"
    fi

    python3 "$BM" "$RUN" \
      --streams "$STREAMS" \
      --workers "$WORKERS" \
      $COMMON \
      $EXTRA
  done
done

