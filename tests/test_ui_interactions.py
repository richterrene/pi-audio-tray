"""Interaction regressions: real Qt widgets, isolated settings and no receiver access."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem, QWidget
from shiboken6 import isValid

import core
import dsp
import audio
import updates
from app import TrayApp
from component_ui import ComponentsDialog
from dsp_ui import DspDialog, DspInstallDialog
from maintenance_ui import PolicyDialog, UpdatesDialog
from music_ui import MusicWindow
from network_ui import NetworkWindow
from setup_ui import SetupDialog
from test_dsp import status as dsp_status
from test_updates import inventory as update_status


class UIInteractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()
        self.polling = patch.object(TrayApp, 'refresh')
        self.polling.start()
        self.config = {**core.DEFAULTS, 'host': 'audio-pi.local', 'username': 'audio'}
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            if isValid(widget):
                widget.close()
                widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.polling.stop()
        self.env.stop()
        self.temp.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('UI operation did not finish')

    def tray(self):
        with patch.object(TrayApp, 'refresh'):
            window = self.keep(TrayApp(self.app))
        window.timer.stop(); window.clock.stop(); window.tray.hide()
        window.config = self.config.copy()
        return window

    def test_failed_refresh_invalidates_stale_maintenance_controls(self):
        cases = [(DspDialog, dsp, 'request', dsp_status(), 'apply_button'),
                 (DspInstallDialog, dsp, 'request', dsp_status(), 'install_button'),
                 (PolicyDialog, audio, 'policy_request',
                  {'installed': True, 'exclusive': False, 'state': 'active', 'source': '', 'degraded': False}, 'apply_button'),
                 (UpdatesDialog, updates, 'check', update_status(), 'install_button')]
        for cls, module, method, status, button in cases:
            with self.subTest(dialog=cls.__name__):
                dialog = self.keep(cls(self.config))
                dialog.show_status(status); dialog.controls()
                self.assertTrue(getattr(dialog, button).isEnabled())
                with patch.object(module, method, side_effect=RuntimeError('Receiver unavailable')):
                    dialog.refresh()
                    self.wait_for(lambda: dialog.worker is None)
                self.assertIsNone(dialog.snapshot)
                self.assertFalse(getattr(dialog, button).isEnabled())
                self.assertTrue(dialog.close_button.isEnabled())
                # Recovery must restore usable controls without reopening the dialog.
                with patch.object(module, method, return_value=status):
                    dialog.refresh()
                    self.wait_for(lambda: dialog.worker is None)
                self.assertTrue(getattr(dialog, button).isEnabled())

    def test_setup_constructor_failure_releases_operation_guard(self):
        window = self.tray()
        with patch('setup_ui.SetupDialog', side_effect=RuntimeError('Construction failed')), patch.object(window, 'refresh'):
            with self.assertRaises(RuntimeError):
                window.setup()
        self.assertNotIn('provision', window.busy)
        self.assertTrue(window.timer.isActive())

    def test_setup_clears_unused_admin_password_on_all_close_paths(self):
        for mode in ('button', 'escape', 'window'):
            with self.subTest(mode=mode):
                dialog = self.keep(SetupDialog(self.config))
                dialog.password.setText('fixture-password-only')
                dialog.show()
                if mode == 'button': dialog.close_button.click()
                elif mode == 'escape': QTest.keyClick(dialog, Qt.Key.Key_Escape)
                else: dialog.close()
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.password.text(), '')

    def test_nested_dialog_cannot_start_a_read_after_its_event_loop_returns(self):
        from audio_ui import AudioDialog
        cases = [('audio', AudioDialog, audio, 'request'),
                 ('policy', PolicyDialog, audio, 'policy_request'),
                 ('updates', UpdatesDialog, updates, 'check'),
                 ('dsp', DspInstallDialog, dsp, 'request')]
        parent = self.keep(ComponentsDialog(self.config))
        for kind, cls, module, method in cases:
            with self.subTest(kind=kind), patch.object(cls, 'exec', return_value=0), patch.object(parent, 'check'), patch.object(module, method) as request:
                if kind == 'audio': parent.audio()
                else: parent.maintenance(kind)
                self.app.processEvents()
                request.assert_not_called()

    def test_modal_dialogs_are_disposed_after_repeated_use(self):
        from app import Settings
        from profile_ui import ProfilesDialog
        window = self.tray()
        for method, cls in ((window.settings, Settings), (window.manage_profiles, ProfilesDialog), (window.setup, SetupDialog)):
            with self.subTest(dialog=cls.__name__), patch.object(cls, 'exec', return_value=0), patch.object(window, 'refresh'):
                for _ in range(3): method()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertFalse(window.findChildren(cls))

    def test_failed_settings_validation_keeps_edits_visible(self):
        from app import Settings
        window = self.tray()
        dialog = self.keep(Settings(self.config, window))
        dialog.show()
        dialog.fields['music_web_url'].setText('http://audio-pi.local/')
        with patch('app.plain_warning') as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.fields['music_web_url'].text(), 'http://audio-pi.local/')
        dialog.fields['music_web_url'].setText('https://audio-pi.local/')
        dialog.accept()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

    def test_input_prompt_clears_private_value_on_save_and_cancel(self):
        from ui_style import LocalizedInputDialog, get_text
        parent = self.keep(QWidget())
        for result in (LocalizedInputDialog.DialogCode.Accepted, LocalizedInputDialog.DialogCode.Rejected):
            with self.subTest(result=result), patch.object(LocalizedInputDialog, 'exec', return_value=result):
                value, accepted = get_text(parent, 'Fixture key', 'Key', text='fixture-secret')
                self.assertEqual(value, 'fixture-secret' if accepted else '')
                prompt = parent.findChild(LocalizedInputDialog)
                self.assertEqual(prompt.textValue(), '')
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertFalse(parent.findChildren(LocalizedInputDialog))

    def test_all_maintenance_write_paths_run_workers_and_recover_close(self):
        from audio_ui import AudioDialog
        from test_audio import snapshot as audio_status
        policy = {'installed': True, 'exclusive': False, 'state': 'active', 'source': '', 'degraded': False}
        cases = [(DspDialog, dsp, 'request', dsp_status(), 'apply'),
                 (PolicyDialog, audio, 'policy_request', policy, 'apply'),
                 (AudioDialog, audio, 'request', audio_status(), 'enable'),
                 (UpdatesDialog, updates, 'operate', None, 'check')]
        for cls, module, method, status, action in cases:
            with self.subTest(dialog=cls.__name__):
                dialog = self.keep(cls(self.config)); dialog.show()
                if status is not None: dialog.show_status(status)
                dialog.controls()
                entered, release = threading.Event(), threading.Event()
                def request(*args):
                    entered.set()
                    if not release.wait(2): raise RuntimeError('Fixture worker timed out')
                    return status
                try:
                    with patch.object(module, method, side_effect=request) as remote:
                        if action == 'enable': dialog.enable_button.click()
                        elif action == 'check':
                            # The update-list action chains a fresh cached inventory afterwards.
                            with patch.object(updates, 'check', return_value=update_status()):
                                dialog.operate('check')
                                self.wait_for(entered.is_set)
                                release.set()
                                self.wait_for(lambda: dialog.worker is None)
                            remote.assert_called_once()
                            continue
                        else:
                            with patch.object(dialog, 'confirm', return_value=True, create=True): dialog.apply()
                        self.wait_for(entered.is_set)
                        self.assertFalse(dialog.close_button.isEnabled())
                        for mode in ('escape', 'window'):
                            if mode == 'escape': QTest.keyClick(dialog, Qt.Key.Key_Escape)
                            else: dialog.close()
                            self.assertTrue(dialog.isVisible())
                        release.set()
                        self.wait_for(lambda: dialog.worker is None)
                        remote.assert_called_once()
                    dialog.close_button.click()
                    self.assertFalse(dialog.isVisible())
                finally:
                    release.set()
                    self.wait_for(lambda: dialog.worker is None)

    def test_volume_debounce_drains_latest_volume_and_mute_before_escape(self):
        from maintenance_ui import VolumeDialog
        import volume
        from test_volume import status
        dialog = self.keep(VolumeDialog(self.config))
        dialog.show_status(status()); dialog.controls(); dialog.show()
        calls = []
        def request(config, action):
            calls.append(action)
            return status(volume=action['value'] if action['action'] == 'volume' else 40,
                          muted=action['value'] if action['action'] == 'mute' else False)
        with patch.object(volume, 'request', side_effect=request):
            dialog.slider.setValue(20); dialog.slider.setValue(40)
            dialog.muted.setChecked(True)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.wait_for(lambda: dialog.worker is None and not dialog.isVisible())
        self.assertEqual([(a['action'], a['value']) for a in calls], [('volume', 40), ('mute', True)])
        self.assertFalse(dialog.debounce.isActive())

    def test_nested_setup_components_does_not_start_late_check(self):
        import components
        dialog = self.keep(SetupDialog(self.config))
        with patch.object(ComponentsDialog, 'exec', return_value=0), patch.object(components, 'check') as remote:
            dialog.components()
            self.app.processEvents()
        remote.assert_not_called()
        self.assertFalse(dialog.install_button.isEnabled())

    def test_modal_exception_cancels_initial_callback_and_disposes_dialog(self):
        from ui_style import exec_dialog
        dialog = self.keep(PolicyDialog(self.config))
        initial = Mock()
        with patch.object(dialog, 'exec', side_effect=RuntimeError('Event loop failed')):
            with self.assertRaises(RuntimeError): exec_dialog(dialog, initial)
        self.app.processEvents()
        initial.assert_not_called()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(dialog))


    def test_host_trust_save_failure_is_visible_without_escaping_qt_callback(self):
        from PySide6.QtWidgets import QMessageBox
        key = Mock(); key.asbytes.return_value = b'fixture-public-key'
        error = core.UnknownHost('audio-pi.local', key)
        for cls, module in ((SetupDialog, 'setup_ui'), (ComponentsDialog, 'component_ui')):
            with self.subTest(dialog=cls.__name__):
                dialog = self.keep(cls(self.config))
                with patch(module + '.exec_dialog', return_value=QMessageBox.StandardButton.Yes), patch(module + '.trust_host', side_effect=OSError('Host trust is read-only')):
                    dialog.failure(error)
                self.assertIn('Host trust is read-only', dialog.log.toPlainText())


    def music(self):
        parent = self.keep(QWidget()); parent.config = self.config.copy(); parent.generation = 0
        window = self.keep(MusicWindow(parent))
        with patch.object(window, 'request'): window.show()
        window.timer.stop()
        return window

    @staticmethod
    def item(widget, data):
        item = QListWidgetItem(data.get('Title', data.get('directory', 'track')))
        item.setData(Qt.ItemDataRole.UserRole, data)
        widget.addItem(item); widget.setCurrentItem(item)
        return item

    def test_enter_on_library_item_activates_it_without_player_button(self):
        window = self.music()
        self.item(window.library, {'file': 'Album/track.flac', 'Title': 'Track'})
        window.library.setFocus()
        with patch.object(window, 'request') as request:
            QTest.keyClick(window.library, Qt.Key.Key_Return)
        request.assert_called_once_with({'action': 'play_file', 'path': 'Album/track.flac'})

    def test_music_volume_focus_changes_do_not_write_an_unedited_value(self):
        window = self.music()
        self.assertFalse(window.volume.isEnabled())
        status = {'status': {'state': 'pause', 'volume': '55'}, 'current': {}}
        with patch.object(window, 'request') as request:
            window.apply_result(({'action': 'status'}, status, status, self.config.copy(), 0), None)
            self.assertTrue(window.volume.isEnabled())
            window.volume.editingFinished.emit()
            request.assert_not_called()
            window.volume.setValue(65)
            window.volume.editingFinished.emit()
            window.volume.editingFinished.emit()
        request.assert_called_once_with({'action': 'volume', 'value': 65})


    def test_queue_enter_plays_only_selected_id(self):
        window = self.music()
        with patch.object(window, 'request'):
            window.tabs.setCurrentIndex(1)
        self.item(window.queue, {'file': 'Album/track.flac', 'Id': '42'})
        window.queue.setFocus()
        with patch.object(window, 'request') as request:
            QTest.keyClick(window.queue, Qt.Key.Key_Return)
        request.assert_called_once_with({'action': 'play_id', 'value': 42})

    def test_queue_up_cannot_change_the_hidden_library_path(self):
        window = self.music()
        window.path = 'Artist/Album'
        with patch.object(window, 'request') as request:
            window.tabs.setCurrentIndex(1)
            request.reset_mock()
            window.up()
        self.assertEqual(window.path, 'Artist/Album')
        request.assert_not_called()

    def test_network_and_profile_lists_support_enter_without_default_actions(self):
        from profile_ui import ProfilesDialog
        parent = self.keep(QWidget()); parent.config = self.config.copy(); parent.generation = 0
        network = self.keep(NetworkWindow(parent))
        with patch.object(network, 'request'): network.show()
        self.item(network.sources, {'id': 'server', 'name': 'Server', 'enabled': True})
        network.sources.setFocus()
        with patch.object(network, 'request') as request:
            QTest.keyClick(network.sources, Qt.Key.Key_Return)
        request.assert_called_once_with({'action': 'network_browse', 'server': 'server', 'object': '0', 'offset': 0})
        profiles = self.keep(ProfilesDialog(parent)); profiles.show(); profiles.list.setFocus()
        with patch.object(profiles, 'add') as add:
            QTest.keyClick(profiles.list, Qt.Key.Key_Return)
        self.assertEqual(profiles.result(), profiles.DialogCode.Accepted)
        self.assertEqual(profiles.selected, 'default')
        add.assert_not_called()


    def test_library_navigation_drops_old_click_targets_before_reply(self):
        window = self.music()
        folder = self.item(window.library, {'directory': 'New album'})
        window.page_contexts['browse'] = ('', 0)
        with patch.object(window, 'request') as request:
            window.activate_library(folder)
            self.assertEqual(window.library.count(), 0)
            window.add_selected()
        request.assert_called_once_with({'action': 'browse', 'path': 'New album', 'offset': 0}, background=False)

    def test_switching_network_server_cannot_enqueue_the_old_servers_track(self):
        parent = self.keep(QWidget()); parent.config = self.config.copy(); parent.generation = 0
        window = self.keep(NetworkWindow(parent))
        with patch.object(window, 'request'): window.show()
        window.server = {'id': 'old', 'name': 'Old server'}
        self.item(window.items, {'object': 'old-track', 'Title': 'Old track'})
        server = self.item(window.sources, {'id': 'new', 'name': 'New server', 'enabled': True})
        with patch.object(window, 'request') as request:
            window.open_source(server)
            self.assertEqual(window.items.count(), 0)
            window.add(True)
        request.assert_called_once_with({'action': 'network_browse', 'server': 'new', 'object': '0', 'offset': 0})

    def test_network_same_page_refresh_preserves_selection(self):
        parent = self.keep(QWidget()); parent.config = self.config.copy(); parent.generation = 0
        window = self.keep(NetworkWindow(parent))
        window.server = {'id': 'one', 'name': 'Server'}
        request = {'action': 'network_browse', 'server': 'one', 'object': '0', 'offset': 0}
        rows = [{'object': 'track', 'Title': 'Track'}]
        payload = (request, self.config.copy(), 0, {'items': rows})
        window.operation_done(payload, None)
        window.items.setCurrentRow(0)
        window.operation_done(payload, None)
        self.assertIsNotNone(window.items.currentItem())
        self.assertEqual(window.items.currentItem().data(Qt.ItemDataRole.UserRole)['object'], 'track')


if __name__ == '__main__':
    unittest.main()
