"""Small-screen wizard layout and accessible, keyboard-operable wrapped choices."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from i18n import available_languages, set_language
from setup_ui import SetupDialog
from ui_style import STYLE


class SetupLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def tearDown(self):
        set_language('en')

    def make_dialog(self, area=QRect(0, 0, 640, 480)):
        screen = Mock(); screen.availableGeometry.return_value = area
        with patch.object(SetupDialog, 'screen', return_value=screen):
            dialog = SetupDialog({'host': 'audio-pi.local', 'username': 'audio'})
        dialog.setStyleSheet(STYLE)
        dialog.show(); self.app.processEvents()
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_all_languages_fit_small_work_area_and_keep_close_and_progress_visible(self):
        for code, _, _ in available_languages():
            with self.subTest(language=code):
                set_language(code)
                dialog = self.make_dialog()
                self.assertLessEqual(dialog.width(), 640)
                self.assertLessEqual(dialog.height(), 480)
                scroll = dialog.findChild(QScrollArea)
                self.assertIsNotNone(scroll)
                overflow = [(type(widget).__name__, widget.minimumSizeHint().width())
                            for widget in scroll.findChildren(QWidget)
                            if widget.minimumSizeHint().width() > scroll.viewport().width()]
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0, overflow)
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                for control in (dialog.close_button, dialog.log):
                    top_left = control.mapTo(dialog, QPoint(0, 0))
                    self.assertTrue(dialog.rect().contains(QRect(top_left, control.size())))
                # Every bottom-of-form operation can be brought into view.
                scroll.ensureWidgetVisible(dialog.output_select)
                self.app.processEvents()
                self.assertGreater(scroll.verticalScrollBar().value(), 0)
                point = dialog.output_select.mapTo(scroll.viewport(), QPoint(0, 0))
                self.assertTrue(scroll.viewport().rect().intersects(QRect(point, dialog.output_select.size())))
                dialog.close()

    def test_initial_size_fits_the_actual_logical_screen(self):
        dialog = SetupDialog({'host': 'audio-pi.local', 'username': 'audio'})
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.setStyleSheet(STYLE)
        dialog.show(); self.app.processEvents()
        area = dialog.screen().availableGeometry()
        self.assertLessEqual(dialog.width(), area.width())
        self.assertLessEqual(dialog.height(), area.height())
        self.assertTrue(dialog.close_button.isVisible())


    def test_long_target_does_not_force_window_wider_and_remains_accessible(self):
        screen = Mock(); screen.availableGeometry.return_value = QRect(0, 0, 640, 480)
        host = 'long-host-label-' * 30 + '.local'
        with patch.object(SetupDialog, 'screen', return_value=screen):
            dialog = SetupDialog({'host': host, 'username': 'audio'})
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.setStyleSheet(STYLE)
        dialog.show(); self.app.processEvents()
        self.assertLessEqual(dialog.width(), 640)
        self.assertLessEqual(dialog.height(), 480)
        self.assertIn(host, dialog.target.accessibleDescription())
        self.assertIn(host, dialog.target.toolTip())


    def test_wrapped_choice_retains_label_click_and_space_keyboard_behavior(self):
        dialog = self.make_dialog()
        scroll = dialog.findChild(QScrollArea)
        choice = dialog.updates
        scroll.ensureWidgetVisible(choice)
        self.app.processEvents()
        self.assertTrue(choice.accessibleName())
        self.assertTrue(choice.isChecked())
        QTest.mouseClick(choice, Qt.MouseButton.LeftButton, pos=QPoint(choice.width()-8, choice.height()//2))
        self.assertFalse(choice.isChecked())
        choice.setFocus()
        QTest.keyClick(choice, Qt.Key.Key_Space)
        self.assertTrue(choice.isChecked())
        self.assertTrue(choice.hasHeightForWidth())
        self.assertGreaterEqual(choice.heightForWidth(240), choice.heightForWidth(500))

    def test_small_screen_scrolling_does_not_change_setup_choices_or_start_requests(self):
        import provision
        dialog = self.make_dialog(QRect(100, 100, 800, 600))
        scroll = dialog.findChild(QScrollArea)
        with patch.object(provision, 'preflight') as remote:
            dialog.music.setChecked(True)
            dialog.web.setChecked(True)
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            self.app.processEvents()
            dialog.music.setChecked(False)
            self.assertFalse(dialog.web.isChecked())
            self.assertFalse(dialog.web.isEnabled())
            self.assertFalse(dialog.usb.isEnabled())
            self.assertTrue(dialog.check.isEnabled())
            remote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
