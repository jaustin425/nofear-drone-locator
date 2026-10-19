#!/usr/bin/env bash
# Explicit, unprivileged 20-second legacy BLE discovery test.
set -euo pipefail
umask 077
[[ $# == 0 && $EUID != 0 ]] || { echo 'Run normally: bash scripts/capture_ble.sh (no sudo)' >&2; exit 1; }
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"
mkdir -p local-data
output_dir=$(mktemp -d "$project_root/local-data/ble-capture-XXXXXXXX")
printf 'AetherLink Scout legacy BLE test; UTC start: %s\n' "$(date -u +%FT%TZ)" > "$output_dir/test.txt"
echo 'Legacy BLE discovery on powered hci0 for up to 20s. No pairing, power change, Wi-Fi changes or TAK output.'
echo "Private local artifacts: $output_dir"
status=0
timeout --signal=INT --kill-after=3s 20s /usr/bin/python3 -I -u \
    "$project_root/scripts/ble_bluez.py" --adapter hci0 \
    > "$output_dir/events.jsonl" 2> "$output_dir/reader.log" || status=$?
printf 'UTC end: %s; capture exit: %s\n' "$(date -u +%FT%TZ)" "$status" >> "$output_dir/test.txt"
if [[ "$status" != 124 ]]; then
    echo "BLE discovery test ended unexpectedly (status $status). Inspect private diagnostics; no controller power settings were changed." >&2
    exit 1
fi
echo 'Bounded BLE test completed. Scout discovery session released; inspect aggregates before claiming Remote ID reception.'
