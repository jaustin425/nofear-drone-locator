#!/usr/bin/env bash
# Bounded comparison or combined live detector; optional write-only TAK output.
set -euo pipefail
umask 077
[[ $# -le 1 && ( ${1:-} == '' || ${1:-} == --live || ${1:-} == --live-tak ) && $EUID != 0 ]] || { echo 'Run as your normal user: bash scripts/capture_dual.sh [--live|--live-tak]' >&2; exit 1; }
launch_mode=${1:-bounded}
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"
radios=(wlx9cefd5f67d4c wlx1cbfce038535)
labels=(panda techkey)
drivers=(mt76x0u rtl88x2bu)
channels=(6 149)
frequencies=(2437 5745)
connections=('' '')
changed=(0 0)
pids=('' '')
app_pid=''
auth_pid=''
app_abnormal=0
phys=('' '')
for tool in sudo nmcli iw ip tcpdump timeout flock; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool; nothing changed." >&2; exit 1; }
done
mkdir -p local-data
exec 9>local-data/dual-capture.lock
flock -n 9 || { echo 'Another dual capture launcher is open; nothing changed.' >&2; exit 1; }
has_uplink() {
    [[ $(nmcli -g GENERAL.STATE device show wlp3s0) == 100* ]] &&
        ip route get 1.1.1.1 | awk '$0 ~ /dev wlp3s0 / {found=1} END {exit !found}'
}
has_uplink || { echo 'Intel must be the connected preferred uplink; nothing changed.' >&2; exit 1; }
# Validate both radios before touching either one.
for i in 0 1; do
    radio=${radios[$i]}
    [[ $(readlink "/sys/class/net/$radio/device/driver") == */"${drivers[$i]}" ]] || {
        echo "${labels[$i]} interface/driver differs from inspected hardware; nothing changed." >&2; exit 1;
    }
    info=$(iw dev "$radio" info)
    grep -Eq '^[[:space:]]*type managed[[:space:]]*$' <<< "$info" || {
        echo "${labels[$i]} must start in managed mode; stop other capture launchers first. Nothing changed." >&2; exit 1;
    }
    [[ $(nmcli -g GENERAL.NM-MANAGED device show "$radio") == yes ]] || {
        echo "${labels[$i]} must start under NetworkManager; nothing changed." >&2; exit 1;
    }
    phys[$i]=$(awk '$1=="wiphy" {print $2}' <<< "$info")
    [[ ${phys[$i]} =~ ^[0-9]+$ ]] || { echo 'Cannot identify radio PHY; nothing changed.' >&2; exit 1; }
    capabilities=$(iw phy "phy${phys[$i]}" info)
    grep -Eq '^[[:space:]]*\* monitor[[:space:]]*$' <<< "$capabilities" || { echo 'Monitor mode unavailable; nothing changed.' >&2; exit 1; }
    line=$(awk -v f="${frequencies[$i]}" -v c="[${channels[$i]}]" '$1=="*" && $2+0==f && $4==c {print}' <<< "$capabilities")
    [[ -n "$line" && ! "$line" =~ disabled|radar|no[[:space:]-]IR ]] || {
        echo "${labels[$i]} target channel unavailable/restricted; nothing changed." >&2; exit 1;
    }
    uuid=$(nmcli -g GENERAL.CON-UUID device show "$radio")
    if [[ "$uuid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
        connections[$i]=$uuid
    elif [[ $(nmcli -g GENERAL.STATE device show "$radio") == 100* ]]; then
        echo 'Cannot identify a connected radio profile for restoration; nothing changed.' >&2; exit 1
    fi
done
[[ ${phys[0]} != "${phys[1]}" ]] || { echo 'Two independent PHYs are required; nothing changed.' >&2; exit 1; }
if [[ "$launch_mode" == --live || "$launch_mode" == --live-tak ]]; then
    [[ -x "$project_root/.venv/bin/python" ]] || { echo 'Project environment missing; nothing changed.' >&2; exit 1; }
    if [[ "$launch_mode" == --live-tak ]]; then
        "$project_root/.venv/bin/python" -m aetherlink_scout.tak --validate-config
    fi
    "$project_root/.venv/bin/python" - <<'PYCHECK'
import socket
with socket.socket() as check:
    check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        check.bind(('127.0.0.2',8765))
    except OSError:
        raise SystemExit('Scout port is occupied; stop the old server first. Nothing changed.')
PYCHECK
    /usr/bin/python3 -I - <<'BTCHECK'
import dbus
import gi
bus=dbus.SystemBus(private=True)
try:
    props=dbus.Interface(bus.get_object('org.bluez','/org/bluez/hci0'),'org.freedesktop.DBus.Properties')
    if not props.Get('org.bluez.Adapter1','Powered',timeout=3):
        raise SystemExit('Bluetooth hci0 must already be powered on; nothing changed.')
finally:
    bus.close()
BTCHECK
fi
sudo -n true || { echo 'Run sudo -v privately in this terminal, then retry. Nothing changed.' >&2; exit 1; }
output_dir=$(mktemp -d "$project_root/local-data/dual-capture-XXXXXXXX")
printf 'AetherLink Scout dual capture; UTC start: %s\nPanda: 6/2437 MHz; Techkey: 149/5745 MHz; each bound: 60s\n' "$(date -u +%FT%TZ)" > "$output_dir/test.txt"
keep_sudo_alive() {
    local sleeper=''
    trap 'if [[ -n "$sleeper" ]]; then kill "$sleeper" 2>/dev/null || true; fi; exit 0' TERM INT
    while true; do
        sleep 45 & sleeper=$!
        wait "$sleeper" || return 0
        sleeper=''
        sudo -n -v >/dev/null 2>&1 || { echo 'Cached sudo expired; capture renewal may fail. No password requested.' >&2; return 0; }
    done
}
restore() {
    local result=$? unsafe=0 i pid attempts restored
    trap - EXIT INT TERM
    # The non-daemon combined supervisor keeps the app alive until every
    # source/capture has confirmed exit. Never race restoration with it.
    if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
        kill -INT "$app_pid" 2>/dev/null || true
        attempts=0
        while kill -0 "$app_pid" 2>/dev/null && (( attempts < 150 )); do sleep .2; attempts=$((attempts+1)); done
        if kill -0 "$app_pid" 2>/dev/null; then
            unsafe=1
        else
            app_exit=0
            wait "$app_pid" 2>/dev/null || app_exit=$?
            if [[ "$app_exit" != 0 ]]; then unsafe=1; fi
        fi
    fi
    if [[ -n "$auth_pid" ]]; then kill "$auth_pid" 2>/dev/null || true; wait "$auth_pid" 2>/dev/null || true; fi
    if [[ "$app_abnormal" == 1 ]]; then unsafe=1; fi
    # Cancel BOTH captures before restoring EITHER radio.
    for pid in "${pids[@]}"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kill -TERM "$pid" 2>/dev/null || true; fi
    done
    for pid in "${pids[@]}"; do
        [[ -n "$pid" ]] || continue
        attempts=0
        while kill -0 "$pid" 2>/dev/null && (( attempts < 25 )); do sleep .2; attempts=$((attempts+1)); done
        if kill -0 "$pid" 2>/dev/null; then unsafe=1; else wait "$pid" 2>/dev/null || true; fi
    done
    if [[ "$unsafe" == 1 ]]; then
        echo 'Capture/app exit unconfirmed or abnormal. Neither radio was restored; each capture retains its configured time bound. Inspect the app/captures before manual restoration.' >&2
        exit 1
    fi
    if ! sudo -n true >/dev/null 2>&1; then
        if [[ -t 0 && -t 2 ]]; then
            echo 'Refresh sudo privately to restore both radios.' >&2
            sudo -v || { echo 'Authorization unavailable; restore radios manually.' >&2; exit 1; }
        else
            echo 'Authorization expired; restore radios manually. Intel was not reconfigured.' >&2; exit 1
        fi
    fi
    for i in 1 0; do
        [[ ${changed[$i]} == 1 ]] || continue
        restored=1
        sudo -n ip link set "${radios[$i]}" down || restored=0
        sudo -n iw dev "${radios[$i]}" set type managed || restored=0
        sudo -n ip link set "${radios[$i]}" up || restored=0
        sudo -n nmcli device set "${radios[$i]}" managed yes || restored=0
        if [[ -n ${connections[$i]} ]]; then
            sudo -n nmcli --wait 20 connection up uuid "${connections[$i]}" ifname "${radios[$i]}" || restored=0
        fi
        if [[ "$restored" == 1 ]]; then
            echo "${labels[$i]} restored to managed/NetworkManager; original connection restored when known."
        else
            echo "${labels[$i]} restoration incomplete; inspect manually." >&2; result=1
        fi
    done
    printf 'UTC end: %s; script status: %s\n' "$(date -u +%FT%TZ)" "$result" >> "$output_dir/test.txt"
    exit "$result"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
verify_radio() {
    local i=$1 phase=${2:-prepared} info
    info=$(iw dev "${radios[$i]}" info)
    printf '%s\n' "$info" > "$output_dir/${labels[$i]}-$phase-radio.txt"
    grep -Eq '^[[:space:]]*type monitor[[:space:]]*$' <<< "$info" &&
        grep -Eq "^[[:space:]]*channel ${channels[$i]} \\(${frequencies[$i]}(\\.0+)? MHz\\)" <<< "$info"
}
echo 'Panda and Techkey will disconnect and listen on 6/149. Intel stays the uplink; Bluetooth power stays unchanged.'
echo "Private local artifacts: $output_dir"
for i in 0 1; do
    radio=${radios[$i]}
    changed[$i]=1
    if [[ -n ${connections[$i]} ]]; then sudo -n nmcli --wait 10 device disconnect "$radio"; fi
    sudo -n nmcli device set "$radio" managed no
    sudo -n ip link set "$radio" down
    sudo -n iw dev "$radio" set type monitor
    sudo -n ip link set "$radio" up
    if [[ "$i" == 0 ]]; then sudo -n iw dev "$radio" set channel 1; fi
    sudo -n iw dev "$radio" set channel "${channels[$i]}"
    verify_radio "$i" || { echo "${labels[$i]} did not confirm its target channel; no captures started." >&2; exit 1; }
done
has_uplink || { echo 'Intel preferred uplink missing; no captures started.' >&2; exit 1; }
# Recheck both after preparation, then launch concurrently on the same host clock.
for i in 0 1; do verify_radio "$i" || { echo 'Prepared radio drifted; no captures started.' >&2; exit 1; }; done
if [[ "$launch_mode" == --live || "$launch_mode" == --live-tak ]]; then
    echo 'Open http://127.0.0.2:8765. START/STOP controls Wi-Fi 6, Wi-Fi 149 and legacy BLE together.'
    echo 'STOP leaves Wi-Fi radios prepared. Ctrl+C exits Scout before restoring both Wi-Fi connections.'
    echo 'Every source uses renewable 120s bounds; source failures stop the group.'
    tak_args=()
    if [[ "$launch_mode" == --live-tak ]]; then
        tak_args=(--tak-config "$project_root/config/local-tak.ini")
        echo 'Optional verified write-only TAK TLS enabled; output errors leave local detection running.'
    else
        echo 'TAK stays disabled.'
    fi
    keep_sudo_alive & auth_pid=$!
    "$project_root/.venv/bin/python" -m aetherlink_scout --combined "${tak_args[@]}" & app_pid=$!
    app_status=0
    wait "$app_pid" || app_status=$?
    app_pid=''
    if [[ "$app_status" != 0 ]]; then app_abnormal=1; fi
    exit "$app_status"
fi
capture_user=$(id -un)
filter='(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)'
for i in 0 1; do
    printf '%s launch UTC: %s\n' "${labels[$i]}" "$(date -u +%FT%T.%NZ)" >> "$output_dir/test.txt"
    sudo -n timeout --signal=INT --kill-after=3s 60s \
        tcpdump -i "${radios[$i]}" -nn -U -s 0 -w - -Z "$capture_user" "$filter" \
        > "$output_dir/${labels[$i]}.pcap" 2> "$output_dir/${labels[$i]}.log" & pids[$i]=$!
done
echo 'Both captures launched: up to 60s each. Keep the Mavic broadcasting during the common interval.'
# Poll only for process exit; wait each exact PID to retain already-finished
# child statuses when both timeouts expire almost simultaneously.
while [[ -n ${pids[0]} || -n ${pids[1]} ]]; do
    for i in 0 1; do
        pid=${pids[$i]}
        [[ -n "$pid" ]] || continue
        if ! kill -0 "$pid" 2>/dev/null; then
            status=0
            wait "$pid" || status=$?
            printf '%s capture exit: %s\n' "${labels[$i]}" "$status" >> "$output_dir/test.txt"
            pids[$i]=''
            [[ "$status" == 124 ]] || { echo "A capture ended unexpectedly (status $status); stopping its peer before restoration." >&2; exit 1; }
        fi
    done
    if [[ -n ${pids[0]} || -n ${pids[1]} ]]; then sleep .1; fi
done
for i in 0 1; do verify_radio "$i" final || { echo 'Radio state drifted during capture; inspect recordings before conclusions.' >&2; exit 1; }; done
has_uplink || { echo 'Intel preferred uplink missing after capture.' >&2; exit 1; }
echo 'Both bounded captures completed. Restoring radios; decoding and channel comparison remain offline checks.'
