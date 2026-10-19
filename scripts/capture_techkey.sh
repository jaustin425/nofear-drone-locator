#!/usr/bin/env bash
# Bounded Techkey tests. No dashboard, hopping or TAK output.
set -euo pipefail
umask 077
[[ $# -le 1 && ( ${1:-} == '' || ${1:-} == --diagnostic ) && $EUID != 0 ]] || {
    echo 'Run as your normal user: bash scripts/capture_techkey.sh [--diagnostic]' >&2; exit 1;
}
test_mode=${1:-filtered}
channel=6
frequency=2437
seconds=60
snaplen=0
capture_args=('(wlan[0] & 0xfc = 0x80) or (wlan[0] & 0xfc = 0xd0)')
if [[ "$test_mode" == --diagnostic ]]; then
    channel=44
    frequency=5220
    seconds=20
    snaplen=96
    capture_args=(-c 10000)
fi
radio=wlx1cbfce038535
uplink=wlp3s0
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_root"
for tool in sudo nmcli iw ip tcpdump timeout; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool; nothing changed." >&2; exit 1; }
done
[[ $(readlink "/sys/class/net/$radio/device/driver") == */rtl88x2bu ]] || {
    echo 'Inspected Techkey interface/driver not found; nothing changed.' >&2; exit 1;
}
[[ $(nmcli -g GENERAL.STATE device show "$uplink") == 100* ]] || {
    echo 'Intel uplink is not connected; nothing changed.' >&2; exit 1;
}
has_uplink() { ip route show default | awk '$0 ~ /dev wlp3s0 / {found=1} END {exit !found}'; }
has_uplink || { echo 'Intel default route is missing; nothing changed.' >&2; exit 1; }
info=$(iw dev "$radio" info)
grep -Eq '^[[:space:]]*type managed[[:space:]]*$' <<< "$info" || {
    echo 'Techkey must start in managed mode; nothing changed.' >&2; exit 1;
}
[[ $(nmcli -g GENERAL.NM-MANAGED device show "$radio") == yes ]] || {
    echo 'Techkey must start under NetworkManager; nothing changed.' >&2; exit 1;
}
phy=$(awk '$1=="wiphy" {print $2}' <<< "$info")
[[ "$phy" =~ ^[0-9]+$ ]] || { echo 'Cannot identify Techkey PHY; nothing changed.' >&2; exit 1; }
capabilities=$(iw phy "phy$phy" info)
grep -Eq '^[[:space:]]*\* monitor[[:space:]]*$' <<< "$capabilities" || {
    echo 'Monitor mode is not advertised; nothing changed.' >&2; exit 1;
}
channel_info=$(awk -v frequency="$frequency" -v channel="[$channel]" '$1=="*" && $2+0==frequency && $4==channel {print}' <<< "$capabilities")
[[ -n "$channel_info" && ! "$channel_info" =~ disabled|radar|no[[:space:]-]IR ]] || {
    echo "Channel $channel is unavailable or restricted; nothing changed." >&2; exit 1;
}
original_connection=$(nmcli -g GENERAL.CON-UUID device show "$radio")
if [[ ! "$original_connection" =~ ^[0-9a-fA-F-]{36}$ ]]; then original_connection=''; fi
sudo -n true || { echo 'Run sudo -v privately in this terminal, then retry. Nothing changed.' >&2; exit 1; }
mkdir -p local-data
output_dir=$(mktemp -d "$project_root/local-data/techkey-capture-XXXXXXXX")
printf 'AetherLink Scout Techkey capture; UTC start: %s\n' "$(date -u +%FT%TZ)" > "$output_dir/test.txt"
printf 'Mode: %s; channel: %s; duration limit: %ss; snaplen: %s\n' "$test_mode" "$channel" "$seconds" "$snaplen" >> "$output_dir/test.txt"
restore_needed=0
capture_pid=''
restore() {
    local result=$? restored=1 attempts=0
    trap - EXIT INT TERM
    if [[ -n "$capture_pid" ]] && kill -0 "$capture_pid" 2>/dev/null; then
        kill -TERM "$capture_pid" 2>/dev/null || true
        while kill -0 "$capture_pid" 2>/dev/null && (( attempts < 25 )); do
            sleep .2
            attempts=$((attempts+1))
        done
        if kill -0 "$capture_pid" 2>/dev/null; then
            echo "Capture exit is unconfirmed. Radio restoration skipped; capture remains bounded to ${seconds}s plus kill grace. Check the terminal before manual restoration." >&2
            exit 1
        fi
        wait "$capture_pid" 2>/dev/null || true
    fi
    if [[ "$restore_needed" == 1 ]]; then
        if ! sudo -n true >/dev/null 2>&1; then
            if [[ -t 0 && -t 2 ]]; then
                echo 'Refresh sudo privately to restore Techkey.' >&2
                sudo -v || restored=0
            else
                restored=0
            fi
        fi
        if [[ "$restored" == 1 ]]; then
            sudo -n ip link set "$radio" down || restored=0
            sudo -n iw dev "$radio" set type managed || restored=0
            sudo -n ip link set "$radio" up || restored=0
            sudo -n nmcli device set "$radio" managed yes || restored=0
            if [[ -n "$original_connection" ]]; then
                sudo -n nmcli --wait 20 connection up uuid "$original_connection" ifname "$radio" || restored=0
            fi
        fi
        if [[ "$restored" == 0 ]]; then
            echo 'Techkey restoration incomplete; restore it manually. Intel and Panda were not reconfigured.' >&2
            result=1
        else
            echo 'Techkey restored to managed mode/NetworkManager; original connection restored when known.'
        fi
    fi
    printf 'UTC end: %s; script status: %s\n' "$(date -u +%FT%TZ)" "$result" >> "$output_dir/test.txt"
    exit "$result"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Only Techkey will disconnect and listen on channel $channel for up to ${seconds}s. Intel and Panda remain unchanged."
if [[ "$test_mode" == --diagnostic ]]; then
    echo 'Diagnostic: no packet filter, at most 10,000 frames, each truncated to 96 bytes; private offline analysis only.'
fi
echo "Private local artifacts: $output_dir"
restore_needed=1
if [[ -n "$original_connection" ]]; then
    sudo -n nmcli --wait 10 device disconnect "$radio"
fi
sudo -n nmcli device set "$radio" managed no
sudo -n ip link set "$radio" down
sudo -n iw dev "$radio" set type monitor
sudo -n ip link set "$radio" up
sudo -n iw dev "$radio" set channel "$channel"
info=$(iw dev "$radio" info)
if ! grep -Eq '^[[:space:]]*type monitor[[:space:]]*$' <<< "$info" || ! grep -Eq "^[[:space:]]*channel $channel \\($frequency(\\.0+)? MHz\\)" <<< "$info"; then
    echo "Techkey did not confirm monitor mode/channel $channel; capture not started." >&2
    exit 1
fi
printf '%s\n' "$info" > "$output_dir/radio-prepared.txt"
has_uplink || { echo 'Intel default route is missing; capture not started.' >&2; exit 1; }
capture_status=0
sudo -n timeout --signal=INT --kill-after=3s "${seconds}s" \
    tcpdump -i "$radio" -nn -U -s "$snaplen" -w - -Z "$(id -un)" \
    "${capture_args[@]}" \
    > "$output_dir/capture.pcap" 2> "$output_dir/capture.log" & capture_pid=$!
wait "$capture_pid" || capture_status=$?
capture_pid=''
printf 'Capture exit status: %s\n' "$capture_status" >> "$output_dir/test.txt"
if [[ "$capture_status" != 124 && !( "$test_mode" == --diagnostic && "$capture_status" == 0 ) ]]; then
    echo "Capture did not complete its expected bound (status $capture_status); inspect private diagnostics locally." >&2
    exit 1
fi
if [[ "$capture_status" == 0 ]]; then
    echo 'Diagnostic packet limit reached before the time limit.'
fi
echo 'Bounded capture completed. Restoring Techkey; received frames still need offline verification.'
