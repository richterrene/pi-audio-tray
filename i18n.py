"""Bundled, data-only language catalogs. No external translation service."""
import json
from pathlib import Path
import re
from string import Formatter
import sys
import unicodedata

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'translations'
_language = 'en'
_messages = {}
_qt_translator = None


def placeholders(text):
    return sorted(field for _, field, _, _ in Formatter().parse(text) if field is not None)


def format_signature(text):
    return sorted((field, spec, conversion or '') for _, field, spec, conversion in Formatter().parse(text) if field is not None)


def read_catalog(path):
    if path.stat().st_size > 512 * 1024:
        raise ValueError('Translation catalog exceeds size limit')
    catalog = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(catalog, dict) or set(catalog) != {'language', 'name', 'country', 'messages'}:
        raise ValueError('Invalid translation catalog fields')
    if not isinstance(catalog['language'], str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?', catalog['language']):
        raise ValueError('Invalid translation language code')
    if path.stem != catalog['language']:
        raise ValueError('Catalog filename and language code differ')
    if not isinstance(catalog['country'], str) or not re.fullmatch('[A-Z]{2}', catalog['country']):
        raise ValueError('Invalid flag country code')
    if not isinstance(catalog['name'], str) or not catalog['name'] or len(catalog['name']) > 80 or any(not c.isprintable() or unicodedata.category(c) == 'Cf' for c in catalog['name']):
        raise ValueError('Invalid language name')
    if not isinstance(catalog['messages'], dict):
        raise ValueError('Invalid translation messages')
    for source, target in catalog['messages'].items():
        if not isinstance(source, str) or not isinstance(target, str) or not source or not target:
            raise ValueError('Translations must contain non-empty strings')
        if any(unicodedata.category(c) in ('Cf', 'Cs') or (unicodedata.category(c) == 'Cc' and c not in '\n\t') for c in target):
            raise ValueError('Translation contains control characters')
        if format_signature(source) != format_signature(target):
            raise ValueError('Translation changes formatting placeholders')
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', field) for field in placeholders(target)):
            raise ValueError('Only named simple placeholders are allowed')
    return catalog


def catalogs():
    result = {}
    for path in sorted(ROOT.glob('*.json')):
        try:
            catalog = read_catalog(path)
            result[catalog['language']] = catalog
        except (OSError, ValueError, TypeError):
            continue  # One broken contributed file must not prevent startup.
    return result


def available_languages():
    data = catalogs()
    data.setdefault('en', {'name': 'English', 'country': 'GB'})
    return [(code, item['name'], item['country']) for code, item in sorted(data.items())]


def set_language(code):
    global _language, _messages, _qt_translator
    data = catalogs().get(code)
    _language = code if data else 'en'
    _messages = data['messages'] if data else {}
    # Qt supplies translations for standard Save/Cancel/Yes/No buttons.
    from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
    app = QCoreApplication.instance()
    if app:
        if _qt_translator is not None:
            app.removeTranslator(_qt_translator)
            _qt_translator.deleteLater()
        translator = QTranslator(app)
        if translator.load('qtbase_' + _language.replace('-', '_'), QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
        _qt_translator = translator
    return _language


def tr(source, **values):
    """Translate a fixed source template; device/music data stays in values."""
    target = _messages.get(source, source)
    return target.format(**values) if values else target


def translate_standard_buttons(box):
    """Provide consistent basic buttons even where Qt has no locale file."""
    for key, source in (('Save', 'Save'), ('Cancel', 'Cancel'), ('Yes', 'Yes'), ('No', 'No'), ('Ok', 'OK'), ('Close', 'Close')):
        role = getattr(box.StandardButton, key, None)
        button = box.button(role) if role is not None else None
        if button is not None:
            button.setText(tr(source))
