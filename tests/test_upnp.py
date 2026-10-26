import contextlib
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
import core
import pi_music
import pi_upnp as upnp
from network_ui import NetworkWindow

SERVER = {'id': 'a' * 32, 'name': 'Example music', 'address': '192.168.1.20',
          'control': 'http://192.168.1.20:8000/control', 'service': upnp.SERVICE}
TRACK = {'object': 'track-1', 'Title': 'Test song', 'Artist': 'Artist', 'Album': 'Album',
         'url': 'http://192.168.1.20:8000/audio/1'}


class UPnPSecurity(unittest.TestCase):
    def test_content_directory_v3_is_supported(self):
        description = ET.fromstring('<root xmlns="urn:schemas-upnp-org:device-1-0"><device><UDN>uuid:example</UDN><friendlyName>Example</friendlyName><serviceList><service><serviceType>urn:schemas-upnp-org:service:ContentDirectory:3</serviceType><controlURL>/control</controlURL></service></serviceList></device></root>')
        with patch.object(upnp, 'fetch', return_value=description):
            server = upnp.describe('http://192.168.1.20:8000/device.xml', '192.168.1.20')
        self.assertEqual(server['service'], upnp.SERVICE[:-1] + '3')
        self.assertEqual(server['control'], SERVER['control'])

    def test_private_ipv4_only_and_no_redirects(self):
        for address in ('127.0.0.1', '169.254.169.254', '8.8.8.8', '::1', '224.0.0.1', '0.0.0.0'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                upnp.lan_address(address)
        with self.assertRaises(ValueError):
            upnp.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_endpoints_cannot_escape_discovered_server_or_contain_credentials(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'http://192.168.1.21/',
                    'http://example.invalid/', 'http://u:p@192.168.1.20/',
                    'http://192.168.1.20/#fragment', 'http://192.168.1.20/\nclear',
                    'http://192.168.1.20:65536/', 'http://192.168.1.20\\other/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                upnp.pinned_url(url, SERVER['address'])
        self.assertEqual(upnp.pinned_url(TRACK['url'], SERVER['address']), TRACK['url'])

    def test_xml_entities_and_oversized_documents_rejected(self):
        for data in (b'<!DOCTYPE x [<!ENTITY e "evil">]><x>&e;</x>',
                     '<!DOCTYPE x [<!ENTITY e "evil">]><x>&e;</x>'.encode('utf-16'),
                     b'x' * (upnp.MAX_XML + 1)):
            with self.assertRaises(ValueError):
                upnp.parse_xml(data)

    def test_soap_escapes_object_ids_and_hides_media_urls_from_ui(self):
        didl = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:u="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="track-1"><dc:title>Test song</dc:title><u:class>object.item.audioItem.musicTrack</u:class><res protocolInfo="http-get:*:audio/mpeg:*">http://192.168.1.20:8000/audio/1</res></item></DIDL-Lite>'
        response = ET.Element('response')
        for key, value in (('Result', didl), ('NumberReturned', '1'), ('TotalMatches', '2')):
            ET.SubElement(response, key).text = value
        with patch.object(upnp, 'fetch', return_value=response) as fetch:
            rows, more = upnp.browse(SERVER, 'A<&"B', 0)
        self.assertTrue(more)
        self.assertEqual(rows[0]['url'], TRACK['url'])
        sent = ET.fromstring(fetch.call_args.args[2])
        self.assertEqual(next(x.text for x in sent.iter() if x.tag == 'ObjectID'), 'A<&"B')
        with self.assertRaises(ValueError):
            upnp.browse(SERVER, 'x\nplay')

    def test_allowlist_rejects_unknown_actions_and_request_fields(self):
        for request in ({'action': 'network_kill'}, {'action': 'network_sources', 'url': 'http://x'},
                        {'action': 'network_browse', 'offset': True}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                upnp.dispatch(Mock(), request)

    def state(self, state):
        @contextlib.contextmanager
        def get_state():
            yield state
        return patch.object(upnp, 'registry', get_state)

    def test_only_recently_discovered_sources_can_be_enabled(self):
        import time
        state = {'found': {SERVER['id']: SERVER}, 'enabled': {}, 'discovered_at': 0}
        with self.state(state):
            with self.assertRaises(ValueError):
                upnp.dispatch(Mock(), {'action': 'network_enable', 'server': SERVER['id']})
            state['discovered_at'] = time.time()
            upnp.dispatch(Mock(), {'action': 'network_enable', 'server': SERVER['id']})
            self.assertEqual(state['enabled'][SERVER['id']], SERVER)
            upnp.dispatch(Mock(), {'action': 'network_remove', 'server': SERVER['id']})
            self.assertFalse(state['enabled'])

    def test_disabled_sources_cannot_browse_or_enqueue(self):
        with self.state({'enabled': {}, 'found': {}}), patch.object(upnp, 'browse') as browse:
            for action in ('network_browse', 'network_add', 'network_play'):
                with self.assertRaises(ValueError):
                    upnp.dispatch(Mock(), {'action': action, 'server': SERVER['id']})
            browse.assert_not_called()

    def test_remote_urls_never_accepted_from_desktop_and_play_uses_new_id(self):
        mpd = Mock()
        mpd.command.side_effect = lambda command, *args: [('Id', '42')] if command == 'addid' else []
        with self.state({'enabled': {SERVER['id']: SERVER}}), patch.object(upnp, 'browse', return_value=([TRACK], False)):
            upnp.dispatch(mpd, {'action': 'network_play', 'server': SERVER['id'], 'object': 'track-1'})
        self.assertEqual(mpd.command.call_args_list[0].args, ('addid', TRACK['url']))
        self.assertEqual(mpd.command.call_args_list[-1].args, ('playid', '42'))

    def test_large_or_nested_album_is_rejected_before_any_queue_change(self):
        mpd = Mock()
        for children, more in (([TRACK], True), ([{'folder': True}], False)):
            with self.state({'enabled': {SERVER['id']: SERVER}}), patch.object(upnp, 'browse', side_effect=[([{'folder': True}], False), (children, more)]):
                with self.assertRaises(ValueError):
                    upnp.dispatch(mpd, {'action': 'network_add', 'server': SERVER['id'], 'object': 'album'})
        mpd.command.assert_not_called()

    def test_failed_metadata_update_removes_only_newly_added_song(self):
        mpd = Mock()
        def command(name, *args):
            if name == 'addid':
                return [('Id', '42')]
            if name == 'addtagid':
                raise RuntimeError('metadata failed')
            return []
        mpd.command.side_effect = command
        with self.state({'enabled': {SERVER['id']: SERVER}}), patch.object(upnp, 'browse', return_value=([TRACK], False)):
            with self.assertRaises(RuntimeError):
                upnp.dispatch(mpd, {'action': 'network_add', 'server': SERVER['id'], 'object': 'track-1'})
        self.assertEqual(mpd.command.call_args_list[-1].args, ('deleteid', '42'))
        self.assertFalse(any(call.args[0] in ('clear', 'stop', 'playid') for call in mpd.command.call_args_list))

    def test_bridge_loads_sibling_under_isolated_python(self):
        with patch.object(upnp, 'discover'):
            # Unknown operations are refused even through the script-directory loader.
            with self.assertRaises(ValueError):
                pi_music.dispatch(Mock(), {'action': 'network_kill'})

    @unittest.skipUnless(upnp.fcntl, 'Pi registry uses Linux flock')
    def test_registry_is_private_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(upnp.Path, 'home', return_value=Path(temp)):
            with upnp.registry() as state:
                state['enabled']['test'] = SERVER
            path = Path(temp) / '.local/state/pi-audio-network/sources.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with upnp.registry() as state:
                self.assertEqual(state['enabled']['test'], SERVER)
            path.unlink()
            path.symlink_to(Path(temp) / 'outside')
            with self.assertRaises(ValueError):
                with upnp.registry():
                    pass


class NetworkUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.parent.config = core.DEFAULTS.copy()
        self.parent.generation = 0
        self.window = NetworkWindow(self.parent)
        with patch.object(self.window, 'request'):
            self.window.show()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.parent.deleteLater()

    def test_no_background_or_hidden_requests(self):
        self.window.hide()
        with patch('network_ui.music_request') as request:
            self.window.request({'action': 'network_discover'})
            request.assert_not_called()
        self.assertFalse(self.window.pending)

    def test_stale_results_are_ignored_and_network_metadata_is_plain(self):
        source = upnp.public(SERVER, True) | {'name': '<img src="x">'}
        payload = ({'action': 'network_sources'}, self.parent.config.copy(), 0, {'sources': [source]})
        self.window.operation_done(payload, None)
        self.assertIn('<img src="x">', self.window.sources.item(0).text())
        self.parent.generation = 1
        self.window.operation_done((payload[0], payload[1], 0, {'sources': []}), None)
        self.assertEqual(self.window.sources.count(), 1)

    def test_connection_change_discards_old_source_selection_and_commands(self):
        self.window.connection = (self.parent.config.copy(), 0)
        self.window.server = SERVER
        self.parent.generation = 1
        with patch('network_ui.music_request') as request:
            self.window.request({'action': 'network_add', 'server': SERVER['id'], 'object': 'track-1'})
            request.assert_not_called()
        self.assertIsNone(self.window.server)
