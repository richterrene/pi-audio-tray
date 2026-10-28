"""Real Qt task lifecycle: keep QDialog.finished(int) distinct from worker cleanup."""
import os
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QSignalSpy
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import audio
import components
import updates
import volume
from audio_ui import AudioDialog
from component_ui import ComponentsDialog
from maintenance_ui import PolicyDialog, UpdatesDialog, VolumeDialog
from test_audio import snapshot as airplay_status
from test_components import inventory
from test_updates import inventory as update_status
from test_volume import status as volume_status


class DialogWorkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('Qt worker or dialog did not complete')

    def test_all_maintenance_dialogs_run_real_threads_and_close_normally(self):
        policy = {'installed': True, 'exclusive': True, 'state': 'active', 'source': '', 'degraded': False}
        cases = [(VolumeDialog, volume, 'request', volume_status()),
                 (PolicyDialog, audio, 'policy_request', policy),
                 (UpdatesDialog, updates, 'check', update_status()),
                 (AudioDialog, audio, 'request', airplay_status()),
                 (ComponentsDialog, components, 'check', inventory())]
        for cls, module, method, value in cases:
            with self.subTest(dialog=cls.__name__), patch.object(module, method, return_value=value) as call:
                dialog = cls({'host': 'audio-pi.local', 'username': 'audio'})
                closed = QSignalSpy(dialog.finished)
                dialog.show()
                (dialog.check if cls is ComponentsDialog else dialog.refresh)()
                self.wait_for(lambda: dialog.worker is None)
                self.assertIsNotNone(dialog.snapshot)
                call.assert_called_once()
                dialog.close_button.click()
                self.wait_for(lambda: not dialog.isVisible())
                self.assertEqual(closed.count(), 1)
                dialog.deleteLater()

    def test_volume_write_drains_then_close_button_and_window_x_both_work(self):
        for mode in ('button', 'window'):
            entered, release = threading.Event(), threading.Event()
            calls = []
            def request(config, action=None):
                calls.append(action)
                if action:
                    entered.set()
                    if not release.wait(2): raise RuntimeError('Test request was not released')
                    return volume_status(volume=action['value'])
                return volume_status()
            with self.subTest(close=mode), patch.object(volume, 'request', side_effect=request):
                dialog = VolumeDialog({'host': 'audio-pi.local', 'username': 'audio'})
                dialog.show(); dialog.refresh()
                self.wait_for(lambda: dialog.worker is None)
                dialog.slider.setValue(35); dialog.flush()
                self.wait_for(entered.is_set)
                if mode == 'button': dialog.close_button.click()
                else: dialog.close()
                self.assertTrue(dialog.close_after)
                self.assertTrue(dialog.isVisible())
                release.set()
                self.wait_for(lambda: dialog.worker is None and not dialog.isVisible())
                self.assertEqual(calls[-1]['value'], 35)
                self.assertEqual(len(calls), 2)
                dialog.deleteLater()

    def test_failed_volume_read_releases_close_guard(self):
        with patch.object(volume, 'request', side_effect=RuntimeError('Fixture SSH failure')):
            dialog = VolumeDialog({'host': 'audio-pi.local', 'username': 'audio'})
            dialog.show(); dialog.refresh()
            self.wait_for(lambda: dialog.worker is None)
            self.assertIn('Fixture SSH failure', dialog.message.text())
            dialog.close()
            self.wait_for(lambda: not dialog.isVisible())
            dialog.deleteLater()

    def test_status_window_can_hide_to_tray_after_volume_dialog_closes(self):
        from app import TrayApp
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}), patch.object(TrayApp, 'refresh'), patch.object(volume, 'request', return_value=volume_status()), patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True):
            window = TrayApp(self.app)
            window.timer.stop(); window.clock.stop()
            window.config.update(host='audio-pi.local', username='audio')
            window.show()
            completed = False
            def close_when_ready():
                if completed:
                    return
                dialog = next((d for d in window.findChildren(VolumeDialog) if d.isVisible()), None)
                if dialog is None or dialog.worker is not None:
                    QTimer.singleShot(5, close_when_ready)
                else:
                    dialog.close_button.click()
            QTimer.singleShot(5, close_when_ready)
            # Failsafe ends a broken nested event loop without hanging the suite.
            failsafe = QTimer(window)
            failsafe.setSingleShot(True)
            failsafe.timeout.connect(lambda: [d.done(0) for d in window.findChildren(VolumeDialog) if d.isVisible()])
            failsafe.start(2000)
            window.receiver_dialog('volume')
            completed = True
            failsafe.stop()
            self.assertNotIn('provision', window.busy)
            window.close(); self.app.processEvents()
            self.assertFalse(window.isVisible())
            window.timer.stop(); window.clock.stop(); window.tray.hide()
            window.deleteLater()


if __name__ == '__main__':
    unittest.main()
