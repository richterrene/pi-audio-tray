#!/usr/bin/python3 -I
"""Bounded Bluetooth startup privacy initialization; no pairing agent is opened."""
import os
import sys
import time

ADAPTER = 'org.bluez.Adapter1'
OBJECTS = 'org.freedesktop.DBus.ObjectManager'
PROPERTIES = 'org.freedesktop.DBus.Properties'


class NotReady(RuntimeError):
    """BlueZ or an adapter is not ready to confirm the requested state."""


def close_adapter(props, api):
    # Attempt both privacy switches even if one setter fails. Verify the result
    # before powering an initially disabled adapter, then verify again afterwards.
    for name in ('Pairable', 'Discoverable'):
        try:
            props.Set(ADAPTER, name, api.Boolean(False), timeout=2)
        except api.DBusException:
            pass
    failed = []
    for name in ('Pairable', 'Discoverable'):
        try:
            if props.Get(ADAPTER, name, timeout=2):
                failed.append(name)
        except api.DBusException:
            failed.append(name)
    if failed:
        raise NotReady('Cannot confirm Bluetooth privacy: ' + ', '.join(failed))


def configure(bus, api):
    try:
        manager = api.Interface(bus.get_object('org.bluez', '/'), OBJECTS)
        paths = sorted(path for path, interfaces in manager.GetManagedObjects(timeout=2).items()
                       if ADAPTER in interfaces)
        if not paths:
            raise NotReady('No Bluetooth controller available yet')
        # Close all visible adapters first; do not leave a second adapter pairable.
        properties = [api.Interface(bus.get_object('org.bluez', path), PROPERTIES) for path in paths]
        failures = []
        for props in properties:
            try:
                close_adapter(props, api)
            except NotReady as error:
                failures.append(str(error))
        if failures:
            raise NotReady('; '.join(failures))
        for props in properties:
            props.Set(ADAPTER, 'Powered', api.Boolean(True), timeout=2)
            close_adapter(props, api)
            if not props.Get(ADAPTER, 'Powered', timeout=2):
                raise NotReady('Bluetooth controller did not power on')
    except api.DBusException as error:
        raise NotReady('BlueZ is not ready: ' + str(error)) from error


def wait_until_private(apply, timeout=30, interval=0.5, now=time.monotonic, sleep=time.sleep):
    deadline = now() + timeout
    while True:
        try:
            apply()
            return
        except NotReady as error:
            remaining = deadline - now()
            if remaining <= 0:
                raise RuntimeError('Bluetooth startup privacy timed out: ' + str(error)) from error
            sleep(min(interval, remaining))


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError('Run the fixed startup privacy helper as administrator without arguments')
    import dbus
    # Recreate the connection on retries so a restarting BlueZ/bus can recover.
    def apply():
        try:
            configure(dbus.SystemBus(), dbus)
        except dbus.DBusException as error:
            raise NotReady('System bus is not ready: ' + str(error)) from error
    wait_until_private(apply)
    print('Bluetooth ready; pairing and discoverability disabled.')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
