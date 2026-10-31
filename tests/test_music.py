import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
import core
import pi_music
from music_ui import MusicWindow


class MusicProtocol(unittest.TestCase):
    def test_path_and_argument_injection_is_rejected_before_send(self):
        for path in ('../secret', '/etc/passwd', 'file:///etc/passwd', 'http://example.invalid/a', 'x\nclear', 'x\rplay', 'a\\b', 'x\0y'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pi_music.music_path(path)
        self.assertEqual(pi_music.quote('An "Album"'), '"An \\"Album\\""')
        self.assertEqual(pi_music.music_path('Artist/Album/a.mp3'), 'Artist/Album/a.mp3')

    def test_only_allowlisted_actions_and_strict_numbers_are_accepted(self):
        mpd = Mock()
        for request in ({'action': 'kill'}, {'action': 'volume', 'value': True}, {'action': 'volume', 'value': 101},
                        {'action': 'play_id', 'value': -1}, {'action': 'browse', 'offset': -1},
                        {'action': 'browse', 'offset': 1.5}, {'action': 'play', 'command': 'kill'}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                pi_music.dispatch(mpd, request)
        mpd.command.assert_not_called()

    def test_colons_in_nested_music_names_reach_mpd_unchanged(self):
        for path in ('Network music/Artist/Album: Part 1/song: live.flac',
                     'USB music/Artist: Band/recording.mp3'):
            with self.subTest(path=path):
                mpd = Mock()
                self.assertEqual(pi_music.dispatch(mpd, {'action': 'add', 'path': path}), {'ok': True})
                mpd.command.assert_called_once_with('add', path)

    def test_colon_support_does_not_allow_uri_or_traversal_inputs(self):
        for path in ('smb://server/music', 'file:/etc/passwd', 'C:/Windows/song.mp3',
                     '1scheme:track', 'Network music/../secret:track',
                     'Network music/Album: Part 1/../../..', '//server/share:track'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pi_music.dispatch(Mock(), {'action': 'add', 'path': path})

    def test_play_file_targets_the_new_song_id(self):
        mpd = Mock()
        mpd.command.side_effect = [[('Id', '57')], []]
        self.assertEqual(pi_music.dispatch(mpd, {'action': 'play_file', 'path': 'A/B/song.mp3'}), {'ok': True})
        self.assertEqual(mpd.command.call_args_list[1].args, ('playid', '57'))

    def test_browse_is_paginated_and_export_metadata_is_not_a_library_folder(self):
        mpd = Mock()
        mpd.command.return_value = [('directory', '_Export')] + [('directory', f'Artist {i:03}') for i in range(240)]
        page = pi_music.dispatch(mpd, {'action': 'browse', 'offset': 100})
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['items'][0], {'directory': 'Artist 100'})
        self.assertTrue(page['more'])

    def test_web_urls_require_https_and_cannot_include_credentials(self):
        for url in ('http://pi.local/', 'file:///etc/passwd', 'https://user:password@pi.local/', 'https://pi.local/#secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                core.validate_config({**core.DEFAULTS, 'music_web_url': url})
        self.assertEqual(core.music_web_url({**core.DEFAULTS, 'host': 'pi.local'}), 'https://pi.local:8443/')
        self.assertEqual(core.music_web_url({**core.DEFAULTS, 'host': '::1'}), 'https://[::1]:8443/')

    def test_request_uses_fixed_remote_command_and_stdin(self):
        client = Mock()
        with patch.object(core, 'connect', return_value=client), patch.object(core, 'run', return_value='{"ok":true}') as run:
            core.music_request(core.DEFAULTS, {'action': 'add', 'path': '$(touch secret).mp3'})
        self.assertEqual(run.call_args.args[1], '/usr/local/lib/pi-audio-tray/pi-music')
        self.assertIn('$(touch secret)', run.call_args.kwargs['input_data'])
        client.close.assert_called_once()


class MusicWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.parent.config = core.DEFAULTS.copy()
        self.window = MusicWindow(self.parent)

    def tearDown(self):
        self.window.hide()
        self.window.deleteLater()
        self.parent.deleteLater()


class MusicVisibility(MusicWindowTests):
    def test_connection_reset_clears_previous_pi_data_without_new_commands(self):
        self.window.library.addItem('Old Pi track')
        self.window.queue.addItem('Old Pi queue')
        self.window.path = 'Old Pi album'
        self.window.busy = True
        self.window.pending_requests = [({'action': 'play'}, {}, 0, False)]
        self.window.page_contexts = {'browse': ('Old Pi album', 0)}
        with patch('music_ui.music_request') as request:
            self.window.reset_connection()
        request.assert_not_called()
        self.assertEqual(self.window.library.count(), 0)
        self.assertEqual(self.window.queue.count(), 0)
        self.assertEqual(self.window.path, '')
        self.assertFalse(self.window.pending_requests)
        self.assertFalse(self.window.page_contexts)
        self.assertTrue(self.window.busy)  # The old worker must still complete safely.

    def test_hidden_window_does_not_request_status(self):
        with patch('music_ui.music_request') as request:
            self.window.refresh_status()
            self.window.request({'action': 'status'})
            request.assert_not_called()
        self.assertFalse(self.window.timer.isActive())

    def test_timer_stops_after_window_is_closed(self):
        with patch.object(self.window, 'request'):
            self.window.show()
            self.assertTrue(self.window.timer.isActive())
            self.window.close()
            self.assertFalse(self.window.timer.isActive())

    def test_track_text_is_plain_and_stale_connection_results_are_ignored(self):
        request = {'action': 'browse'}
        result = {'items': [{'file': 'a.mp3', 'Title': '<img src="https://example.invalid/">'}], 'more': False}
        status = {'status': {'state': 'stop', 'volume': '30'}, 'current': {}}
        self.window.operation_done((request, result, status, self.parent.config.copy()), None)
        self.assertEqual(self.window.library.item(0).text(), '<img src="https://example.invalid/">')
        self.assertEqual(self.window.now.textFormat(), Qt.TextFormat.PlainText)
        self.window.operation_done((request, {'items': []}, status, {'host': 'old'}), None)
        self.assertEqual(self.window.library.count(), 1)


class NonblockingRefresh(MusicWindowTests):
    def test_language_change_does_not_discard_an_accepted_control(self):
        self.show_window()
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response) as remote:
            self.window.refresh_status()
            self.window.request({'action': 'pause'})
            self.parent.config['language'] = 'de'
            jobs[0]()
            self.assertEqual(len(jobs), 2)
            jobs[1]()
        self.assertIn('pause', [call.args[1]['action'] for call in remote.call_args_list])

    def test_more_pages_flag_cannot_leak_from_library_into_queue(self):
        self.show_window()
        self.window.more = True
        with patch.object(self.window, 'request'):
            self.window.tabs.setCurrentIndex(1)
            self.window.page(100)
        self.assertEqual(self.window.queue_offset, 0)
        self.assertFalse(self.window.more)

    def test_usb_rescan_does_not_claim_the_async_scan_has_finished(self):
        self.window.operation_done(({'action': 'update'}, {'ok': True}, self.response(None, {'action': 'status'}), self.parent.config.copy()), None)
        self.assertIn('after the scan finishes', self.window.message.text())
    def show_window(self):
        with patch.object(self.window, 'request'):
            self.window.show()
        self.window.timer.stop()

    def hold_workers(self, jobs):
        def thread(*, target, daemon):
            return Mock(start=lambda: jobs.append(target))
        return patch('music_ui.threading.Thread', side_effect=thread)

    @staticmethod
    def response(config, request):
        if request['action'] == 'status':
            return {'status': {'state': 'pause', 'volume': '30'}, 'current': {}}
        if request['action'] in ('browse', 'queue'):
            return {'items': [], 'more': False}
        return {'ok': True}

    def test_background_refresh_keeps_controls_enabled_and_delivers_pause_click(self):
        self.show_window()
        # Controls become available after the first valid status; a later poll
        # must not disable them or prevent a queued playback command.
        status = self.response(None, {'action': 'status'})
        self.window.apply_result(({'action': 'status'}, status, status, self.parent.config.copy()), None)
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response) as remote:
            self.window.refresh_status()
            self.assertEqual(len(jobs), 1)
            self.assertTrue(self.window.tabs.isEnabled())
            self.assertTrue(self.window.volume.isEnabled())
            self.assertTrue(all(button.isEnabled() for button in self.window.buttons))
            self.window.buttons[2].click()  # Pause while status request is still in flight.
            self.assertEqual(len(jobs), 1)  # Keep SSH work serial, without losing the click.
            jobs[0]()
            self.assertEqual(len(jobs), 2)
            jobs[1]()
            self.assertEqual([call.args[1]['action'] for call in remote.call_args_list][:3],
                             ['status', 'pause', 'status'])

    def test_slow_poll_does_not_accumulate_more_background_polls(self):
        self.show_window()
        jobs = []
        with self.hold_workers(jobs):
            for _ in range(5):
                self.window.refresh_status()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.window.pending_requests, [])

    def test_background_reply_cannot_overwrite_a_pending_volume_edit(self):
        self.show_window()
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response):
            self.window.refresh_status()
            self.window.volume.setValue(80)
            self.window.request({'action': 'volume', 'value': 80})
            jobs[0]()
            self.assertEqual(self.window.volume.value(), 80)
            self.assertEqual(len(jobs), 2)

    def test_only_latest_queued_navigation_is_sent(self):
        self.show_window()
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response) as remote:
            self.window.refresh_status()
            for path in ('Old folder', 'New folder'):
                self.window.path = path
                self.window.reload_page()
            jobs[0]()
            jobs[1]()
            browsed = [call.args[1]['path'] for call in remote.call_args_list if call.args[1]['action'] == 'browse']
            self.assertEqual(browsed, ['New folder'])

    def test_queued_controls_are_not_sent_after_connection_generation_changes(self):
        self.show_window()
        self.parent.generation = 0
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response) as remote:
            self.window.refresh_status()
            self.window.request({'action': 'pause'})
            self.parent.generation = 1
            jobs[0]()
            self.assertEqual(len(jobs), 1)
            self.assertEqual([call.args[1]['action'] for call in remote.call_args_list], ['status'])
            self.assertEqual(self.window.now.text(), 'Player not checked yet')

    def test_hiding_preserves_an_accepted_playback_click_and_drops_reads(self):
        self.show_window()
        jobs = []
        with self.hold_workers(jobs), patch('music_ui.music_request', side_effect=self.response) as remote:
            self.window.refresh_status()
            self.window.request({'action': 'pause'})
            self.window.request({'action': 'browse'})
            self.window.hide()
            jobs[0]()
            self.assertEqual(len(jobs), 2)
            jobs[1]()
            self.assertEqual([call.args[1]['action'] for call in remote.call_args_list], ['status', 'pause', 'status'])
            self.assertEqual(self.window.pending_requests, [])

    def test_old_folder_response_cannot_replace_the_new_view(self):
        self.window.path = 'New folder'
        request = {'action': 'browse', 'path': 'Old folder', 'offset': 0}
        self.window.operation_done((request, {'items': [{'file': 'Old folder/a.mp3'}], 'more': False},
                                    self.response(None, {'action': 'status'}), self.parent.config.copy()), None)
        self.assertEqual(self.window.library.count(), 0)

    def test_queue_updates_preserve_item_identity_selection_and_scroll(self):
        self.show_window()
        with patch.object(self.window, 'request'):
            self.window.tabs.setCurrentIndex(1)
        rows = [{'file': f'Album/{i}.mp3', 'Id': str(i), 'Title': f'Track {i}'} for i in range(90)]
        request = {'action': 'queue', 'offset': 0}
        status = self.response(None, {'action': 'status'})
        def apply(items):
            self.window.apply_result((request, {'items': items, 'more': False}, status, self.parent.config.copy()), None, background=True)
        apply(rows)
        self.app.processEvents()
        selected = self.window.queue.item(50)
        self.window.queue.setCurrentItem(selected)
        self.window.queue.verticalScrollBar().setValue(300)
        scroll = self.window.queue.verticalScrollBar().value()
        apply(rows)
        self.assertIs(self.window.queue.currentItem(), selected)
        self.assertEqual(self.window.queue.verticalScrollBar().value(), scroll)
        apply([{'file': 'Album/new.mp3', 'Id': '999', 'Title': 'New track'}] + rows)
        self.assertIs(self.window.queue.currentItem(), selected)
        self.assertEqual(self.window.queue.currentItem().data(Qt.ItemDataRole.UserRole)['Id'], '50')
        self.assertEqual(self.window.queue.verticalScrollBar().value(), scroll)


if __name__ == '__main__':
    unittest.main()
