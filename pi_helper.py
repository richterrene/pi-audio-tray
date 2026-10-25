#!/usr/bin/python3
"""Pi-side JSON protocol; install root-owned before running with sudo."""
import json
import os
import signal
import sys
import subprocess
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
PROPS = 'org.freedesktop.DBus.Properties'
AGENT = 'org.bluez.Agent1'
SERVICES = ('raspotify', 'bluetooth', 'bluealsa', 'bluealsa-aplay')


def emit(**event):
    print(json.dumps(event), flush=True)


def objects(bus):
    return dbus.Interface(bus.get_object('org.bluez', '/'),
                          'org.freedesktop.DBus.ObjectManager').GetManagedObjects()


def adapter(bus):
    for path, interfaces in objects(bus).items():
        if ADAPTER in interfaces:
            return dbus.Interface(bus.get_object('org.bluez', path), PROPS)
    raise RuntimeError('No Bluetooth adapter found')


def status():
    bus = dbus.SystemBus()
    devices, adapters = [], []
    for path, interfaces in objects(bus).items():
        if ADAPTER in interfaces:
            p = interfaces[ADAPTER]
            adapters.append({k: bool(p.get(k, False)) for k in
                             ('Powered', 'Pairable', 'Discoverable')})
        if DEVICE in interfaces:
            p = interfaces[DEVICE]
            if p.get('Connected'):
                devices.append({'name': str(p.get('Alias', 'Bluetooth device')),
                                'address': str(p.get('Address', ''))})
    result = subprocess.run(['systemctl', 'show', '--no-pager', '-p', 'Id',
                             '-p', 'ActiveState', *[s + '.service' for s in SERVICES]],
                            capture_output=True, text=True, timeout=8, check=True)
    services = {}
    for block in result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        services[values.get('Id', '').removesuffix('.service')] = values.get('ActiveState', 'unknown')
    emit(protocol=1, services=services, adapter=adapters[0] if adapters else {},
         devices=devices, uptime=int(float(open('/proc/uptime').read().split()[0])))


class Rejected(dbus.DBusException):
    _dbus_error_name = 'org.bluez.Error.Rejected'


class PairingAgent(dbus.service.Object):
    def __init__(self, bus, loop, props):
        super().__init__(bus, '/org/piaudiotray/agent')
        self.bus, self.loop, self.props = bus, loop, props
        self.pending = {}
        self.counter = 0
        self.approved = set()

    def request(self, device, kind, ok, fail, code=None):
        self.counter += 1
        token = str(self.counter)
        self.pending[token] = (ok, fail, str(device))
        p = dbus.Interface(self.bus.get_object('org.bluez', device), PROPS).GetAll(DEVICE)
        emit(event='confirm', token=token, kind=kind, code=code,
             name=str(p.get('Alias', 'Bluetooth device')), address=str(p.get('Address', '')))

    @dbus.service.method(AGENT, in_signature='ou', out_signature='', async_callbacks=('ok', 'fail'))
    def RequestConfirmation(self, device, passkey, ok, fail):
        self.request(device, 'passkey', ok, fail, f'{passkey:06d}')

    @dbus.service.method(AGENT, in_signature='o', out_signature='', async_callbacks=('ok', 'fail'))
    def RequestAuthorization(self, device, ok, fail):
        self.request(device, 'authorize', ok, fail)

    @dbus.service.method(AGENT, in_signature='os', out_signature='', async_callbacks=('ok', 'fail'))
    def AuthorizeService(self, device, uuid, ok, fail):
        p = dbus.Interface(self.bus.get_object('org.bluez', device), PROPS)
        if p.Get(DEVICE, 'Trusted') or str(device) in self.approved:
            ok()
        else:
            self.request(device, 'service', ok, fail)

    @dbus.service.method(AGENT, in_signature='', out_signature='')
    def Cancel(self):
        for token, (_, fail, _) in list(self.pending.items()):
            fail(Rejected('Pairing canceled'))
            emit(event='canceled', token=token)
        self.pending.clear()

    @dbus.service.method(AGENT, in_signature='', out_signature='')
    def Release(self):
        self.loop.quit()

    @dbus.service.method(AGENT, in_signature='o', out_signature='s')
    def RequestPinCode(self, device):
        raise Rejected('Legacy PIN devices are not supported')

    @dbus.service.method(AGENT, in_signature='o', out_signature='u')
    def RequestPasskey(self, device):
        raise Rejected('Passkey entry is not supported')

    @dbus.service.method(AGENT, in_signature='os', out_signature='')
    def DisplayPinCode(self, device, pincode):
        emit(event='display', code=str(pincode))

    @dbus.service.method(AGENT, in_signature='ouq', out_signature='')
    def DisplayPasskey(self, device, passkey, entered):
        emit(event='display', code=f'{passkey:06d}')

    def command(self, message):
        if message.get('action') == 'stop':
            self.loop.quit()
        elif message.get('token') in self.pending:
            ok, fail, device = self.pending.pop(message['token'])
            if message.get('action') == 'approve':
                self.approved.add(device)
                ok()
            else:
                fail(Rejected('Rejected by user'))


def pair():
    # A process lock also prevents two desktops from taking over one another.
    import fcntl
    lock = open('/run/pi-audio-tray-pairing.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    props = adapter(bus)
    loop = GLib.MainLoop()
    manager = dbus.Interface(bus.get_object('org.bluez', '/org/bluez'), 'org.bluez.AgentManager1')
    agent = PairingAgent(bus, loop, props)
    buffer = bytearray()

    def read_input(fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            loop.quit()
            return False
        data = os.read(fd, 8192)
        if not data:
            loop.quit()
            return False
        buffer.extend(data)
        while b'\n' in buffer:
            line, _, tail = buffer.partition(b'\n')
            buffer[:] = tail
            try:
                agent.command(json.loads(line))
            except (ValueError, TypeError):
                pass
        return True

    old_timeout = props.Get(ADAPTER, 'DiscoverableTimeout')
    old_pair_timeout = props.Get(ADAPTER, 'PairableTimeout')
    try:
        manager.RegisterAgent('/org/piaudiotray/agent', 'DisplayYesNo')
        manager.RequestDefaultAgent('/org/piaudiotray/agent')
        props.Set(ADAPTER, 'Powered', dbus.Boolean(True))
        props.Set(ADAPTER, 'PairableTimeout', dbus.UInt32(180))
        props.Set(ADAPTER, 'DiscoverableTimeout', dbus.UInt32(180))
        props.Set(ADAPTER, 'Pairable', dbus.Boolean(True))
        props.Set(ADAPTER, 'Discoverable', dbus.Boolean(True))
        GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, read_input)
        GLib.timeout_add_seconds(180, lambda: (loop.quit(), False)[1])
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), False)[1])
        emit(event='ready', expires=int(time.time()) + 180)
        loop.run()
    finally:
        # Trust only devices whose request this user approved and which completed pairing.
        for device in agent.approved:
            try:
                p = dbus.Interface(bus.get_object('org.bluez', device), PROPS)
                if p.Get(DEVICE, 'Paired'):
                    p.Set(DEVICE, 'Trusted', dbus.Boolean(True))
            except dbus.DBusException:
                pass
        for name in ('Discoverable', 'Pairable'):
            props.Set(ADAPTER, name, dbus.Boolean(False))
        props.Set(ADAPTER, 'DiscoverableTimeout', old_timeout)
        props.Set(ADAPTER, 'PairableTimeout', old_pair_timeout)
        try:
            manager.UnregisterAgent('/org/piaudiotray/agent')
        except dbus.DBusException:
            pass
        emit(event='closed')


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action == 'status':
        status()
    elif action == 'pair':
        pair()
    elif action == 'off':
        props = adapter(dbus.SystemBus())
        props.Set(ADAPTER, 'Discoverable', dbus.Boolean(False))
        props.Set(ADAPTER, 'Pairable', dbus.Boolean(False))
        emit(event='closed')
    else:
        raise SystemExit('Usage: pi-helper status|pair|off')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit(event='error', message=str(exc))
        sys.exit(1)
