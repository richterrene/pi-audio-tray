"""On-demand Bluetooth management over the selected receiver's verified SSH."""
import json
from core import connect, run
import provision
from bluetooth_schema import request as validate_request, status as validate_status

HELPER = '/usr/local/lib/pi-audio-tray/pi-bluetooth'
PI_FILES = ('install-bluetooth.py', 'setup-receiver.py', 'pi-audio-bluetooth.service', '60-bluetooth-output.conf')
TOP_FILES = ('pi_bluetooth.py', 'bluetooth_schema.py', 'pi_policy.py')


def request(config, value=None):
    value = validate_request({'action': 'status'} if value is None else value)
    client = connect(config)
    try:
        return validate_status(json.loads(run(client, HELPER + ' request', input_data=json.dumps(value), timeout=55)))
    finally:
        client.close()


def install(config, password, progress):
    return provision.administrator_install(config, {}, password, progress,
        'install-bluetooth.py', PI_FILES, TOP_FILES, failure_log='/var/lib/pi-audio-bluetooth/install.log')
