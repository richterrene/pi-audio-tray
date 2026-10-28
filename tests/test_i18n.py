import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import i18n
from tools.check_translations import sources


class TranslationCatalogs(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('en')

    def test_all_requested_languages_cover_the_complete_ui_template(self):
        codes = {'en','de','fr','es','it','pt','nl','pl','cs','ro','hu','el','sv','da','nb','fi','uk','tr','ru'}
        catalogs = i18n.catalogs()
        self.assertTrue(codes <= catalogs.keys())
        for code, catalog in catalogs.items():
            self.assertEqual(set(catalog['messages']), sources(), code)
            self.assertTrue((i18n.ROOT.parent / 'assets/flags' / (catalog['country'] + '.svg')).is_file(), code)

    def test_unknown_language_and_missing_messages_fall_back_to_english(self):
        self.assertEqual(i18n.set_language('../../arbitrary'), 'en')
        self.assertEqual(i18n.tr('New untranslated message'), 'New untranslated message')
        self.assertEqual(i18n.tr('Host {host}', host='{not_a_placeholder}'), 'Host {not_a_placeholder}')

    def test_nonbreaking_spaces_are_valid_but_placeholder_changes_and_controls_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fr.json'
            catalog = {'language':'fr', 'name':'Français', 'country':'FR', 'messages':{'Host {host}':'Hôte\u00a0: {host}'}}
            path.write_text(json.dumps(catalog))
            i18n.read_catalog(path)
            for target in ('Hôte {host.__class__}', 'Hôte {host:999999999}', 'Hôte {other}', 'Hôte\u202e {host}'):
                catalog['messages']['Host {host}'] = target
                path.write_text(json.dumps(catalog))
                with self.subTest(target=target), self.assertRaises(ValueError): i18n.read_catalog(path)

    def test_broken_catalog_does_not_prevent_other_languages_loading(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(i18n, 'ROOT', Path(folder)):
            (Path(folder) / 'xx.json').write_text('not valid json')
            self.assertEqual(i18n.catalogs(), {})
            self.assertEqual(i18n.set_language('xx'), 'en')
            self.assertIn(('en', 'English', 'GB'), i18n.available_languages())

    def test_a_new_language_is_discovered_without_changing_a_code_registry(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(i18n, 'ROOT', Path(folder)):
            (Path(folder) / 'zz.json').write_text(json.dumps({'language': 'zz', 'name': 'Example language', 'country': 'GB', 'messages': {'Play': 'Example play'}}))
            self.assertIn(('zz', 'Example language', 'GB'), i18n.available_languages())
            self.assertEqual(i18n.set_language('zz'), 'zz')
            self.assertEqual(i18n.tr('Play'), 'Example play')

    def test_language_selector_and_standard_buttons_in_each_language(self):
        from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel
        from app import TrayApp, Settings
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}), patch.object(TrayApp, 'refresh'):
            window = TrayApp(app)
            window.timer.stop()
            window.clock.stop()
            for code, _, _ in i18n.available_languages():
                i18n.set_language(code)
                settings = Settings({**window.config, 'language': code}, window)
                settings.show()
                app.processEvents()
                self.assertEqual(settings.fields['language'].currentData(), code)
                buttons = settings.findChild(QDialogButtonBox)
                self.assertEqual(buttons.button(buttons.StandardButton.Save).text(), i18n.tr('Save'))
                for label in settings.findChildren(QLabel):
                    if not label.wordWrap():
                        self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width() + 3, (code, label.text()))
                settings.hide()
                settings.deleteLater()
            window.hide()
            window.tray.hide()


if __name__ == '__main__': unittest.main()
