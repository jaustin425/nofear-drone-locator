#!/usr/bin/env bash
# Dedicated Panda preparation; Python stays unprivileged and TAK stays disabled.
set -euo pipefail
umask 077

capture_mode=${1:-bounded}
if [[ $# -gt 1 || ( "$capture_mode" != bounded && "$capture_mode" != --live && "$capture_mode" != --live-scan ) ]]; then
    echo "Usage: bash scripts/capture_panda.sh [--live | --live-scan]" >&2
    exit 1
fi
[[ $EUID != 0 ]] || { echo "Run this launcher as your normal user, not with sudo." >&2; exit 1; }
capture_interface=wlx9cefd5f67d4c
uplink_interface=wlp3s0
capture_channel=6
capture_frequency=2437
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"

if [[ "$capture_mode" != bounded ]]; then
    [[ -x "$project_root/.venv/bin/python" ]] || {
        echo "Project Python environment is unavailable; nothing changed." >&2; exit 1;
    }
    "$project_root/.venv/bin/python" - <<'PY'
import socket
with socket.socket() as check:
    check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        check.bind(('127.0.0.2', 8765))
    except OSError:
        raise SystemExit('Dashboard port is occupied; stop the existing server first. Nothing changed.')
PY
fi
for tool in sudo nmcli iw ip tcpdump timeout; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
[[ $(nmcli -g GENERAL.STATE device show "$uplink_interface") == 100* ]] || {
    echo "Intel uplink is not connected; stopping before any change." >&2; exit 1;
}
ip route show default | awk -v dev="$uplink_interface" '$0 ~ "dev " dev " " {found=1} END {exit !found}' || {
    echo "Intel has no default route; stopping before any change." >&2; exit 1;
}
[[ $(readlink "/sys/class/net/$capture_interface/device/driver") == */mt76x0u ]] || {
    echo "Panda driver/interface differs from inspected hardware; stopping." >&2; exit 1;
}
radio_info=$(iw dev "$capture_interface" info)
original_type=$(awk '$1=="type" {print $2}' <<< "$radio_info")
[[ "$original_type" == managed || "$original_type" == monitor ]] || {
    echo "Panda must be managed or an already prepared monitor radio; nothing changed." >&2; exit 1;
}
original_connection=$(nmcli -g GENERAL.CON-UUID device show "$capture_interface")
if [[ ! "$original_connection" =~ ^[0-9a-fA-F-]{36}$ ]]; then original_connection=''; fi
sudo -n true || {
    echo "Run sudo -v privately in this terminal, then rerun this script. Nothing changed." >&2; exit 1;
}

restore_needed=0
app_pid=''
auth_pid=''
keep_sudo_alive() {
    local sleeper=''
    trap 'if [[ -n "$sleeper" ]]; then kill "$sleeper" 2>/dev/null || true; fi; exit 0' TERM INT
    while true; do
        sleep 45 & sleeper=$!
        wait "$sleeper" || return 0
        sleeper=''
        if ! sudo -n -v >/dev/null 2>&1; then
            echo "Cached sudo authorization expired; future START/renewal may fail. No password was requested." >&2
            return 0
        fi
    done
}
restore() {
    local status=$?
    trap - EXIT INT TERM
    # Stop the app before changing its radio, including Ctrl+C while wait runs.
    if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
        kill -INT "$app_pid" 2>/dev/null || true
        local attempts=0
        while kill -0 "$app_pid" 2>/dev/null && (( attempts < 150 )); do
            sleep .2
            attempts=$((attempts+1))
        done
        if kill -0 "$app_pid" 2>/dev/null; then
            echo "Scout shutdown is still pending; Panda was not reconfigured. Stop the launch process and restore Panda manually." >&2
            restore_needed=0
            status=1
        else
            wait "$app_pid" 2>/dev/null || true
        fi
    fi
    if [[ -n "$auth_pid" ]]; then
        kill "$auth_pid" 2>/dev/null || true
        wait "$auth_pid" 2>/dev/null || true
    fi
    if [[ "$restore_needed" == 1 ]]; then
        local restored=1
        if ! sudo -n true >/dev/null 2>&1; then
            if [[ -t 0 && -t 2 ]]; then
                echo "Refresh sudo privately in this terminal to restore Panda." >&2
                sudo -v || restored=0
            else
                restored=0
            fi
        fi
        if [[ "$restored" == 1 ]]; then
            sudo -n ip link set "$capture_interface" down || restored=0
            sudo -n iw dev "$capture_interface" set type managed || restored=0
            sudo -n ip link set "$capture_interface" up || restored=0
            sudo -n nmcli device set "$capture_interface" managed yes || restored=0
            if [[ -n "$original_connection" ]]; then
                sudo -n nmcli --wait 20 connection up uuid "$original_connection" ifname "$capture_interface" || restored=0
            fi
        fi
        if [[ "$restored" == 0 ]]; then
            echo "Panda restoration incomplete. Restore it manually; Intel was not changed." >&2
            status=1
        else
            echo "Panda returned to managed mode under NetworkManager; original connection restored when known."
        fi
    fi
    exit "$status"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Preparing only Panda; Intel remains the uplink."
echo "Retuning, when needed, requests channel 1 then 6 while Panda is up."
echo "On exit Panda returns to managed mode under NetworkManager; a known original connection is restored."
mkdir -p "$project_root/local-data"
if [[ "$capture_mode" == bounded ]]; then
    output_dir=$(mktemp -d "$project_root/local-data/panda-capture-XXXXXXXX")
    echo "Local artifacts: $output_dir"
else
    echo "Cached sudo authorization is refreshed noninteractively every 45s while this launcher is open."
    echo "Capture diagnostics stay in ignored local-data/scout-capture-*.log; raw packets are not archived."
    keep_sudo_alive & auth_pid=$!
fi
restore_needed=1
if [[ "$original_type" == managed ]]; then
    if [[ -n "$original_connection" ]]; then
        sudo -n nmcli --wait 10 device disconnect "$capture_interface"
    fi
    sudo -n nmcli device set "$capture_interface" managed no
    sudo -n ip link set "$capture_interface" down
    sudo -n iw dev "$capture_interface" set type monitor
    sudo -n ip link set "$capture_interface" up
else
    # Adopt a prepared Panda without cycling its mode or disconnecting Intel.
    sudo -n nmcli device set "$capture_interface" managed no
    sudo -n ip link set "$capture_interface" up
fi
radio_info=$(iw dev "$capture_interface" info)
channel_matches() {
    awk -v channel="$capture_channel" -v frequency="$capture_frequency" \
        '$1=="channel" && $2==channel && $3==("(" frequency) && $4=="MHz)," {found=1} END {exit !found}' <<< "$radio_info"
}
if ! channel_matches; then
    sudo -n iw dev "$capture_interface" set channel 1
    sudo -n iw dev "$capture_interface" set channel "$capture_channel"
    radio_info=$(iw dev "$capture_interface" info)
fi
printf '%s\n' "$radio_info"
if ! channel_matches; then
    echo "Panda did not confirm channel $capture_channel / $capture_frequency MHz. Capture and dashboard were not started; restoring Panda." >&2
    exit 1
fi
ip route show default | awk -v dev="$uplink_interface" '$0 ~ "dev " dev " " {found=1} END {exit !found}' || {
    echo "Intel default route is missing after preparation; restoring Panda." >&2; exit 1;
}
if [[ "$capture_mode" != bounded ]]; then
    echo "Open http://127.0.0.2:8765. START listens until STOP, renewing bounded 120s capture segments."
    echo "Ctrl+C exits Scout and restores Panda; STOP leaves Panda prepared for another START."
    scan_args=()
    if [[ "$capture_mode" == --live-scan ]]; then
        echo "SCAN SELECTED: START retunes Panda between channels 6 (3s) and 149 (1s); STOP cancels retuning. Intel stays connected."
        scan_args=(--scan)
    fi
    "$project_root/.venv/bin/python" -m aetherlink_scout --live-interface "$capture_interface" "${scan_args[@]}" & app_pid=$!
    app_status=0
    wait "$app_pid" || app_status=$?
    app_pid=''
    exit "$app_status"
fi
capture_status=0
sudo -n timeout --signal=INT --kill-after=5s 60s \
    tcpdump -i "$capture_interface" -nn -U -s 0 -w - \
    '(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)' \
    > "$output_dir/capture.pcap" 2> "$output_dir/capture.log" || capture_status=$?
if [[ "$capture_status" != 0 && "$capture_status" != 124 && "$capture_status" != 130 ]]; then
    echo "Capture failed (status $capture_status); details in local capture.log." >&2
    exit 1
fi
echo "Capture finished. Restoring Panda."
