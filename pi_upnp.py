"""Opt-in LAN UPnP browsing; selected audio joins the existing MPD queue.

No listeners, mounts, credentials, background discovery or remote control actions.
Discovery data is untrusted. Only explicitly enabled servers can supply audio.
"""
import contextlib
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
try:
    import fcntl
except ImportError:  # Desktop tests import this Pi-only module on Windows too.
    fcntl = None

LIMIT = 100
MAX_XML = 1024 * 1024
SERVICE = 'urn:schemas-upnp-org:service:ContentDirectory:1'
DC = '{http://purl.org/dc/elements/1.1/}'
UPNP = '{urn:schemas-upnp-org:metadata-1-0/upnp/}'
DIDL = '{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}'


def lan_address(value):
    address = ipaddress.IPv4Address(value)
    if not any(address in ipaddress.ip_network(net) for net in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('UPnP is restricted to private IPv4 LAN addresses')
    return str(address)


def pinned_url(url, address):
    """Require a same-server HTTP endpoint; remove DNS rebinding opportunities."""
    if not isinstance(url, str) or len(url) > 4096 or '\\' in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('Invalid media endpoint')
    address = lan_address(address)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('Invalid media endpoint')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('Invalid media port')
    if parsed.hostname != address:
        raise ValueError('Media endpoint must use the discovered server IPv4 address')
    return urllib.parse.urlunsplit((parsed.scheme, f'{address}:{port}', parsed.path or '/', parsed.query, ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('UPnP redirects are not accepted')


def fetch(url, address, body=None, service=SERVICE):
    url = pinned_url(url, address)
    headers = {'Content-Type': 'text/xml; charset=utf-8',
               'SOAPAction': f'"{service}#Browse"'} if body is not None else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, data=body, headers=headers)
    with opener.open(request, timeout=2) as response:
        result = response.read(MAX_XML + 1)
    return parse_xml(result)


def parse_xml(data):
    if isinstance(data, str):
        data = data.encode()
    if len(data) > MAX_XML or b'\x00' in data or b'<!doctype' in data.lower() or b'<!entity' in data.lower():
        raise ValueError('UPnP XML exceeded its limit or contains a DTD')
    data.decode('utf-8')  # Do not accept UTF-16/32 encodings that evade DTD checks.
    return ET.fromstring(data)


def describe(location, address):
    root = fetch(location, address)
    ns = {'d': 'urn:schemas-upnp-org:device-1-0'}
    for device in root.findall('.//d:device', ns):
        udn = device.findtext('d:UDN', '', ns)
        if not udn or len(udn) > 256:
            continue
        for service in device.findall('d:serviceList/d:service', ns):
            kind = service.findtext('d:serviceType', '', ns)
            if kind not in tuple(SERVICE[:-1] + str(version) for version in range(1, 5)):
                continue
            control = urllib.parse.urljoin(location, service.findtext('d:controlURL', '', ns))
            return {'id': hashlib.sha256(udn.encode()).hexdigest()[:32],
                    'name': device.findtext('d:friendlyName', 'Media server', ns)[:200],
                    'address': lan_address(address), 'control': pinned_url(control, address), 'service': kind}
    raise ValueError('Server has no UPnP content directory')


def discover():
    message = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
               b'MAN: "ssdp:discover"\r\nMX: 2\r\n'
               b'ST: urn:schemas-upnp-org:device:MediaServer:1\r\n\r\n')
    locations = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(.3)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(message, ('239.255.255.250', 1900))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(locations) < 8:
            try:
                data, peer = sock.recvfrom(8192)
                lan_address(peer[0])
                if not data.startswith(b'HTTP/1.1 200'):
                    continue
                headers = dict(line.split(':', 1) for line in data.decode(errors='replace').splitlines()[1:] if ':' in line)
                headers = {k.lower().strip(): v.strip() for k, v in headers.items()}
                location = headers.get('location', '')
                # Do not perform DNS lookups on unsolicited discovery packets.
                if urllib.parse.urlsplit(location).hostname != peer[0]:
                    continue
                locations[pinned_url(location, peer[0])] = peer[0]
            except (OSError, ValueError):
                continue
    found = {}
    deadline = time.monotonic() + 10
    for location, address in locations.items():
        if time.monotonic() >= deadline:
            break
        try:
            server = describe(location, address)
            found[server['id']] = server
        except (OSError, ValueError, ET.ParseError):
            continue
    return found


def text(value, limit=4096):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid UPnP identifier')
    return value


def browse(server, object_id='0', offset=0, metadata=False):
    text(object_id)
    envelope = ET.Element('{http://schemas.xmlsoap.org/soap/envelope/}Envelope')
    body = ET.SubElement(envelope, '{http://schemas.xmlsoap.org/soap/envelope/}Body')
    operation = ET.SubElement(body, '{' + server['service'] + '}Browse')
    for key, value in (('ObjectID', object_id), ('BrowseFlag', 'BrowseMetadata' if metadata else 'BrowseDirectChildren'),
                       ('Filter', '*'), ('StartingIndex', str(offset)), ('RequestedCount', str(LIMIT)), ('SortCriteria', '')):
        ET.SubElement(operation, key).text = value
    result = fetch(server['control'], server['address'], ET.tostring(envelope), server['service'])
    fields = {node.tag.rsplit('}', 1)[-1]: node.text for node in result.iter()}
    if 'Result' not in fields:
        raise ValueError('Media server rejected browsing')
    tree = parse_xml(fields['Result'] or '<empty/>')
    if len(tree) > LIMIT:
        raise ValueError('Media server ignored the page limit')
    items = []
    for node in tree:
        row = {'object': text(node.get('id', '')), 'Title': text(node.findtext(DC + 'title', 'Untitled'), 1024)}
        kind = node.findtext(UPNP + 'class', '')
        if node.tag == DIDL + 'container':
            row['folder'] = True
        elif node.tag == DIDL + 'item' and kind.startswith('object.item.audioItem'):
            resources = node.findall(DIDL + 'res')
            url = next((r.text for r in resources if r.get('protocolInfo', '').startswith('http-get:') and r.text), None)
            if not url:
                continue
            try:
                row['url'] = pinned_url(url, server['address'])
            except (ValueError, OSError):
                continue
            for key, tag in (('Artist', UPNP + 'artist'), ('Album', UPNP + 'album')):
                row[key] = text(node.findtext(tag, ''), 1024)
        else:
            continue
        items.append(row)
    try:
        returned, total = int(fields.get('NumberReturned', '0')), int(fields.get('TotalMatches', '0'))
    except (TypeError, ValueError):
        raise ValueError('Invalid UPnP page size') from None
    if returned < 0 or returned > LIMIT or total < 0:
        raise ValueError('Invalid UPnP page size')
    return items, offset + returned < total


@contextlib.contextmanager
def registry():
    if fcntl is None:
        raise RuntimeError('Network discovery must run on the Linux Pi')
    root = Path.home() / '.local/state/pi-audio-network'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('Network source directory must be private')
    fd = os.open(root / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'sources.json'
        if path.is_symlink():
            raise ValueError('Refusing symbolic-link source settings')
        state = json.loads(path.read_text()) if path.exists() else {'enabled': {}, 'found': {}}
        yield state
        handle, temporary = tempfile.mkstemp(dir=root, prefix='.sources-')
        try:
            with os.fdopen(handle, 'w') as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def public(server, enabled):
    return {key: server[key] for key in ('id', 'name', 'address')} | {'enabled': enabled}


def dispatch(mpd, request):
    action = request['action']
    if set(request) - {'action', 'server', 'object', 'offset'}:
        raise ValueError('Invalid network request')
    offset = request.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('Invalid page')
    if action not in ('network_sources', 'network_discover', 'network_enable', 'network_remove', 'network_browse', 'network_add', 'network_play'):
        raise ValueError('Unknown network operation')
    with registry() as state:
        if action == 'network_discover':
            state['found'] = discover()
            state['discovered_at'] = time.time()
        if action in ('network_sources', 'network_discover'):
            candidates = state['found'] if action == 'network_discover' else state['enabled']
            return {'sources': [public(v, key in state['enabled']) for key, v in candidates.items()]}
        key = text(request.get('server', ''), 32)
        if action == 'network_enable':
            if time.time() - state.get('discovered_at', 0) > 300 or key not in state['found']:
                raise ValueError('Discover the server again before enabling it')
            if key not in state['enabled'] and len(state['enabled']) >= 32:
                raise ValueError('Remove an old source before adding another')
            state['enabled'][key] = state['found'][key]
            return {'ok': True}
        if action == 'network_remove':
            state['enabled'].pop(key, None)
            return {'ok': True}
        if key not in state['enabled']:
            raise ValueError('Enable this media server first')
        server = state['enabled'][key]
        object_id = request.get('object', '0')
        items, more = browse(server, object_id, offset, metadata=action != 'network_browse')
        if action == 'network_browse':
            return {'items': [{k: v for k, v in row.items() if k != 'url'} for row in items], 'more': more}
        if len(items) != 1:
            raise ValueError('Media server did not return the selected item')
        if items[0].get('folder'):
            # Albums/flat track folders only: no unbounded recursive traversal.
            items, more = browse(server, object_id)
            if more or any(row.get('folder') for row in items):
                raise ValueError('Choose an album or a flat folder of at most 100 tracks')
        if not items:
            raise ValueError('No audio tracks in this folder')
        added = []
        try:
            for row in items:
                song_id = dict(mpd.command('addid', row['url']))['Id']
                added.append(song_id)
                # Stream metadata supplied by the selected, trusted media server.
                for tag in ('Title', 'Artist', 'Album'):
                    if row.get(tag):
                        mpd.command('addtagid', song_id, tag, row[tag])
            if action == 'network_play':
                mpd.command('playid', added[0])
        except Exception:
            # Never clear an existing queue on a failed album/metadata operation.
            for song_id in added:
                try:
                    mpd.command('deleteid', song_id)
                except Exception:
                    pass  # Lost connections cannot guarantee rollback.
            raise
        return {'ok': True}
