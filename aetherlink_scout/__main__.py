import argparse
import ipaddress
import os
import math
from pathlib import Path
import uvicorn
from .app import create_app
from .replay import demo_dataset, load_capture
from .geocoding import validate_endpoint
from .receiver import Receiver
from .history import HistoryStore


def main():
    parser = argparse.ArgumentParser(prog="aetherlink-scout", description="AetherLink Scout local replay or continuous input dashboard; no TAK output.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", help="Authorized local PCAP/PCAPNG path")
    source.add_argument("--demo", action="store_true", help="Fictitious two-aircraft replay")
    source.add_argument('--simulate', metavar='PCAP', help='Timed historical input simulation; no radio capture')
    source.add_argument('--simulate-demo', action='store_true', help='Timed fictitious input simulation')
    source.add_argument('--live-interface', metavar='INTERFACE', help='START/STOP capture on an already prepared monitor radio; never changes interface state')
    parser.add_argument('--simulation-speed', type=float, default=1, help='Simulation pacing only: 1–20 (default 1)')
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.2", help="IPv4 loopback address only (default: 127.0.0.2)")
    geocoding = parser.add_mutually_exclusive_group()
    geocoding.add_argument("--geocoder-url", help="Local/approved Nominatim-compatible reverse endpoint; disabled by default")
    geocoding.add_argument("--geoapify", action="store_true", help="Enable Geoapify; reads AETHERLINK_SCOUT_GEOAPIFY_KEY from server environment")
    args = parser.parse_args()
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
    if args.live_interface:
        history = HistoryStore(Path(__file__).resolve().parent.parent/'local-data/scout-history.sqlite3')
        receiver = Receiver(live_interface=args.live_interface, history=history)
    else:
        try:
            replay = demo_dataset() if args.demo or args.simulate_demo else load_capture(args.simulate or args.capture)
        except Exception:
            parser.exit(2, "Unable to load recording. Check format, read access, and presence of Remote ID. No payload contents printed.\n")
        if simulation_mode:
            receiver = Receiver(simulation=replay, speed=args.simulation_speed)
            replay = None
    label = 'STREAM SIMULATION' if simulation_mode else 'LIVE INPUT STREAM' if receiver else 'HISTORICAL REPLAY'
    print(f'AetherLink Scout {label} — http://{host}:{args.port}', flush=True)
    print('TAK disabled. Prepare monitor radio separately; START listens until STOP using renewable bounded capture segments.' if args.live_interface else 'No radio capture or TAK output.', flush=True)
    uvicorn.run(create_app(replay, str(host), args.geocoder_url, api_key, receiver=receiver), host=str(host), port=args.port, access_log=False, timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
