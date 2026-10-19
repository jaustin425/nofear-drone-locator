"""Unprivileged BlueZ LE discovery; stdout protocol is private RID-only JSONL.

Run with Ubuntu /usr/bin/python3 (python3-dbus/python3-gi), never sudo. The
parent applies a timeout. No controller power/pair/connect operations are used.
"""
import argparse
import json
import os
import re
import signal
import sys
import time

UUID = '0000fffa-0000-1000-8000-00805f9b34fb'


def emit(value):
    print(json.dumps(value, separators=(',', ':'), allow_nan=False), flush=True)


class Discovery:
    def __init__(self, bus, dbus, loop, adapter):
        self.bus, self.dbus, self.loop = bus, dbus, loop
        self.path = '/org/bluez/'+adapter
        self.session = False
        self.running = False
        self.code = 0
        self.advertisements = self.candidates = 0

    def fail(self, message):
        self.code = 1
        emit({'kind': 'error', 'message': message})
        self.loop.quit()
        return False

    def finish(self):
        self.loop.quit()
        return False

    def expire(self):
        self.code = 124
        return self.finish()

    def ingest(self, path, properties):
        if not self.running or not str(path).startswith(self.path+'/dev_') or 'ServiceData' not in properties:
            return
        self.advertisements += 1
        service = properties['ServiceData']
        for uuid, value in service.items():
            if str(uuid).lower() != UUID:
                continue
            raw = bytes(value)
            # ServiceData excludes the UUID: app code, counter, ODID payload.
            # Retain no names, paired-device state or unrelated service data.
            if not 3 <= len(raw) <= 4096 or raw[0] != 0x0d:
                continue
            try:
                address = properties.get('Address')
                if address is None:
                    device = self.bus.get_object('org.bluez', path)
                    address = self.dbus.Interface(device, 'org.freedesktop.DBus.Properties').Get('org.bluez.Device1', 'Address', timeout=2)
                if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', str(address)):
                    continue
                rssi = properties.get('RSSI')
                self.candidates += 1
                emit({'kind': 'advertisement', 'timestamp': time.time(), 'address': str(address),
                      'rssi': int(rssi) if rssi is not None else None, 'service_data': raw.hex()})
            except BrokenPipeError:
                raise
            except Exception:
                self.fail('Bluetooth Remote ID event could not be read; detection stopped.')

    def added(self, path, interfaces):
        properties = interfaces.get('org.bluez.Device1')
        if properties is not None:
            self.ingest(path, properties)

    def changed(self, interface, properties, invalidated, path=None):
        if str(path) == self.path and interface == 'org.bluez.Adapter1':
            if 'Powered' in properties and not properties['Powered']:
                self.fail('Bluetooth was powered off; detection stopped.')
            return
        if interface == 'org.bluez.Device1':
            self.ingest(path, properties)

    def start(self):
        obj = self.bus.get_object('org.bluez', self.path)
        self.properties = self.dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
        if not self.properties.Get('org.bluez.Adapter1', 'Powered', timeout=2):
            raise RuntimeError('not powered')
        self.adapter = self.dbus.Interface(obj, 'org.bluez.Adapter1')
        # No GetManagedObjects replay: cached ServiceData is never a fresh fix.
        self.bus.add_signal_receiver(self.added, signal_name='InterfacesAdded', dbus_interface='org.freedesktop.DBus.ObjectManager', bus_name='org.bluez')
        self.bus.add_signal_receiver(self.changed, signal_name='PropertiesChanged', dbus_interface='org.freedesktop.DBus.Properties', bus_name='org.bluez', path_keyword='path')
        self.adapter.SetDiscoveryFilter(self.dbus.Dictionary({'Transport': 'le', 'DuplicateData': self.dbus.Boolean(True)}, signature='sv'), timeout=3)
        self.adapter.StartDiscovery(timeout=5)
        self.session = self.running = True
        emit({'kind': 'ready', 'reader': 'bluez-dbus', 'coverage': 'legacy BLE; service updates may coalesce'})

    def heartbeat(self):
        if self.code:
            return False
        try:
            if not self.properties.Get('org.bluez.Adapter1', 'Powered', timeout=2) or not self.properties.Get('org.bluez.Adapter1', 'Discovering', timeout=2):
                return self.fail('Bluetooth discovery stopped or adapter was lost; detection stopped.')
            emit({'kind': 'status', 'service_data_updates': self.advertisements, 'rid_candidates': self.candidates})
            return True
        except Exception:
            return self.fail('Bluetooth adapter or BlueZ service unavailable; detection stopped.')

    def close(self):
        self.running = False
        if self.session:
            emit({'kind': 'status', 'final': True, 'service_data_updates': self.advertisements, 'rid_candidates': self.candidates})
            self.session = False
            try:
                # BlueZ releases this client's session, preserving other clients.
                self.adapter.StopDiscovery(timeout=3)
            except Exception:
                self.code = 1
                emit({'kind': 'error', 'message': 'Bluetooth discovery cleanup was not confirmed; process disconnect will release its BlueZ client.'})


def main():
    parser = argparse.ArgumentParser(description='AetherLink Scout bounded-parent BLE discovery helper')
    parser.add_argument('--adapter', default='hci0')
    parser.add_argument('--seconds', type=int, default=120, help='Independent helper lifetime bound, 1–120 seconds')
    args = parser.parse_args()
    if os.geteuid() == 0 or not re.fullmatch(r'hci[0-9]{1,3}', args.adapter) or not 1 <= args.seconds <= 120:
        parser.exit(2, 'Use a normal user and an hci adapter name.\n')
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus(private=True)
    except Exception:
        emit({'kind': 'error', 'message': 'Ubuntu D-Bus bindings or BlueZ unavailable; no scan started.'})
        return 1
    loop = GLib.MainLoop()
    reader = Discovery(bus, dbus, loop, args.adapter)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, reader.finish)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, reader.finish)
    try:
        reader.start()
        GLib.timeout_add_seconds(1, reader.heartbeat)
        GLib.timeout_add_seconds(args.seconds, reader.expire)
        loop.run()
    except BrokenPipeError:
        reader.code = 1
    except Exception:
        reader.fail('Bluetooth scan could not start; verify powered adapter and BlueZ authorization. No power settings were changed.')
    finally:
        try:
            reader.close()
        finally:
            bus.close()
    return reader.code


if __name__ == '__main__':
    sys.exit(main())
