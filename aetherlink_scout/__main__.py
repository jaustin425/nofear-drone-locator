import argparse
import ipaddress
import os
import math
import re
from pathlib import Path
import uvicorn
from .app import create_app
from .replay import demo_dataset, load_capture
from .geocoding import validate_endpoint
from .receiver import Receiver
from .history import HistoryStore
from .ble import BleReceiver
from .combined import CombinedReceiver
from .tak import TakOutput, read_config, DEFAULTS, ROOT


def main():
    parser = argparse.ArgumentParser(prog="aetherlink-scout", description="AetherLink Scout local replay or continuous input dashboard; optional verified TAK output.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", help="Authorized local PCAP/PCAPNG path")
    source.add_argument("--demo", action="store_true", help="Fictitious two-aircraft replay")
    source.add_argument('--simulate', metavar='PCAP', help='Timed historical input simulation; no radio capture')
    source.add_argument('--simulate-demo', action='store_true', help='Timed fictitious input simulation')
    source.add_argument('--live-interface', metavar='INTERFACE', help='START/STOP on an already prepared monitor radio; fixed channel by default, --scan explicitly enables Panda retuning')
    source.add_argument('--live-bluetooth', metavar='HCI', help='START/STOP legacy BLE discovery on an already powered BlueZ adapter, e.g. hci0; no Wi-Fi changes')
    source.add_argument('--combined', action='store_true', help='One START/STOP: prepared Panda channel 6, Techkey channel 149 and powered hci0; shared tracks/history/alerts')
    parser.add_argument('--recover-radios',action='store_true',help='Terminal-approved combined mode: recover only pinned USB radios after physical reconnect; restore fixed 6/149 profiles')
    parser.add_argument('--autostart',action='store_true',help='Explicit unattended combined/recovery startup; starts detection once at process launch, never on browser refresh')
    parser.add_argument('--scan', action='store_true', help='Explicit live Panda scan: START retunes channels 6/149 with 3/1-second dwell; requires terminal authorization')
    parser.add_argument('--simulation-speed', type=float, default=1, help='Simulation pacing only: 1–20 (default 1)')
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.2", help="IPv4 loopback address only (default: 127.0.0.2)")
    geocoding = parser.add_mutually_exclusive_group()
    geocoding.add_argument("--geocoder-url", help="Local/approved Nominatim-compatible reverse endpoint; disabled by default")
    geocoding.add_argument("--geoapify", action="store_true", help="Enable Geoapify; reads AETHERLINK_SCOUT_GEOAPIFY_KEY from server environment")
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--tak-config', type=Path, help='Private mode-600 TAK config; optional live-only write-only TLS')
    output.add_argument('--cot-local-output', type=Path, help='New private XML file under local-data; live-only local CoT, no network')
    args = parser.parse_args()
    if (args.tak_config or args.cot_local_output) and not (args.combined or args.live_interface or args.live_bluetooth):
        parser.error('CoT output requires live input; replay/simulation never transmits.')
    if args.recover_radios and not args.combined:
        parser.error('--recover-radios requires --combined; no replay or scan-mode recovery.')
    if args.autostart and not (args.combined and args.recover_radios):
        parser.error('--autostart requires explicit --combined --recover-radios.')
    tak_config = None
    if args.tak_config:
        try:
            tak_config = read_config(args.tak_config)
        except Exception:
            parser.exit(2, 'Private TAK configuration invalid/missing; run python -m aetherlink_scout.tak --validate-config privately. No radio changes.\n')
    if args.live_bluetooth and not re.fullmatch(r'hci[0-9]{1,3}', args.live_bluetooth):
        parser.error('live-bluetooth must name an hci adapter, e.g. hci0')
    if args.scan and not args.live_interface:
        parser.error('--scan requires --live-interface; replay/simulation never retunes')
    simulation_mode = args.simulate is not None or args.simulate_demo
    if not math.isfinite(args.simulation_speed) or not 1 <= args.simulation_speed <= 20:
        parser.error('simulation-speed must be between 1 and 20')
    if not simulation_mode and args.simulation_speed != 1:
        parser.error('simulation-speed applies only to simulation')
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        host = ipaddress.IPv4Address(args.host)
        if not host.is_loopback:
            raise ValueError()
    except ValueError:
        parser.error("host must be an IPv4 loopback address in 127.0.0.0/8")
    if args.geocoder_url:
        try:
            validate_endpoint(args.geocoder_url)
        except ValueError as error:
            parser.error(str(error))
    api_key = os.environ.get("AETHERLINK_SCOUT_GEOAPIFY_KEY") if args.geoapify else None
    if args.geoapify and not api_key:
        parser.error("Set AETHERLINK_SCOUT_GEOAPIFY_KEY privately in this terminal; do not paste it into chat.")
    replay = receiver = None
    if args.live_interface or args.live_bluetooth or args.combined:
        history = HistoryStore(Path(__file__).resolve().parent.parent/'local-data/scout-history.sqlite3')
        receiver = CombinedReceiver(history=history,recover_radios=args.recover_radios) if args.combined else BleReceiver(adapter=args.live_bluetooth, history=history) if args.live_bluetooth else Receiver(live_interface=args.live_interface, history=history, scan=args.scan)
    else:
        try:
            replay = demo_dataset() if args.demo or args.simulate_demo else load_capture(args.simulate or args.capture)
        except Exception:
            parser.exit(2, "Unable to load recording. Check format, read access, and presence of Remote ID. No payload contents printed.\n")
        if simulation_mode:
            receiver = Receiver(simulation=replay, speed=args.simulation_speed)
            replay = None
    if args.tak_config:
        receiver.tak_output = TakOutput(receiver, tak_config)
    elif args.cot_local_output:
        path = args.cot_local_output.resolve()
        if not path.is_relative_to(ROOT/'local-data'):
            parser.error('Local CoT output must be a new file under ignored local-data.')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            parser.exit(2, 'Cannot create private local CoT output; use a new filename.\n')
        receiver.tak_output = TakOutput(receiver, DEFAULTS, local_output=os.fdopen(fd, 'wb'))
    label = 'STREAM SIMULATION' if simulation_mode else 'LIVE INPUT STREAM' if receiver else 'HISTORICAL REPLAY'
    print(f'AetherLink Scout {label} — http://{host}:{args.port}', flush=True)
    if receiver and receiver.tak_output:
        print('Optional TAK TLS configured; START sends current live reports. Local detection continues through output errors.' if args.tak_config else 'LOCAL CoT output configured; no TAK traffic. START writes live reports to the private file.', flush=True)
    if args.combined:
        print('Capture unchanged. Prepare Panda on 6, Techkey on 149 and powered Bluetooth before START. START/STOP controls all bounded sources; any source failure stops the group. Opening this page does not prepare or retune radios.', flush=True)
    elif args.live_bluetooth:
        print('START discovers legacy BLE on the already powered adapter until STOP; bounded 120s sessions renew only on confirmed expiry. BlueZ D-Bus may coalesce updates; Bluetooth 5 Coded PHY is not covered. Wi-Fi stays unchanged.', flush=True)
    else:
        print('Prepare monitor radio separately; START listens until STOP using renewable bounded capture segments.' if args.live_interface else 'No radio capture or TAK output.', flush=True)
    if args.recover_radios:
        print('USB RECOVERY SELECTED: returning pinned radios may be disconnected from NetworkManager and restored to fixed 6/149; Intel remains uplink. STOP cancels retries.',flush=True)
    if args.autostart:
        print('AUTOSTART SELECTED: detection starts once with this process. Browser sound remains opt-in; STOP persists until explicit START or process restart.',flush=True)
    if args.scan:
        print('SCAN SELECTED: START retunes only prepared Panda between channels 6 (3s) and 149 (1s). STOP cancels retuning; Intel remains the uplink.', flush=True)
    uvicorn.run(create_app(replay, str(host), args.geocoder_url, api_key, receiver=receiver,autostart=args.autostart), host=str(host), port=args.port, access_log=False, timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
