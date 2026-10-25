#!/usr/bin/python3 -I
"""Run only inside a disposable bus: dbus-run-session -- /usr/bin/python3 -I tools/check_dbus_agent.py.

Uses three real D-Bus connections, without Bluetooth hardware, sudo, or credentials.
"""
import importlib.util
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


def main():
    DBusGMainLoop(set_as_default=True)
    spec = importlib.util.spec_from_file_location('isolated_agent', Path(__file__).resolve().parents[1] / 'pi_helper.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    owner, exporter, outsider = (dbus.SessionBus(private=True) for _ in range(3))
    if owner.request_name('org.bluez', dbus.bus.NAME_FLAG_DO_NOT_QUEUE) != dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
        raise RuntimeError('Use a fresh dbus-run-session bus')
    loop = GLib.MainLoop()
    agent = helper.PairingAgent(exporter, loop, None)
    events = []
    helper.emit = lambda **event: events.append(event)

    def call(connection, name, *args):
        result = []
        proxy = dbus.Interface(connection.get_object(exporter.get_unique_name(), '/org/piaudiotray/agent', introspect=False), helper.AGENT)
        def done(*values):
            result.append(('ok', values))
            loop.quit()
        def failed(error):
            result.append(('error', error.get_dbus_name()))
            loop.quit()
        timeout = GLib.timeout_add_seconds(5, loop.quit)
        getattr(proxy, name)(*args, reply_handler=done, error_handler=failed, timeout=4)
        loop.run()
        GLib.source_remove(timeout)
        if not result:
            raise RuntimeError('D-Bus test timed out')
        return result[0]

    for name, arguments in (('DisplayPinCode', ('/test/device', '1234')),
                            ('RequestAuthorization', ('/test/device',)), ('Cancel', ()), ('Release', ())):
        assert call(outsider, name, *arguments) == ('error', 'org.bluez.Error.Rejected'), name
    assert not events and not agent.closing
    assert call(owner, 'DisplayPinCode', '/test/device', '1234')[0] == 'ok'
    assert events == [{'event': 'display', 'code': '1234'}]
    assert call(owner, 'Cancel')[0] == 'ok'
    # Release quits the service loop before its asynchronous reply arrives; the
    # separate call above proves successful sender injection without that race.
    agent.remove_from_connection()
    for connection in (outsider, exporter, owner):
        connection.close()
    print('Real D-Bus sender authentication: passed (no Bluetooth changes)')


if __name__ == '__main__':
    main()
