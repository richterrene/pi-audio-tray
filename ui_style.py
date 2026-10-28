"""Small vector symbols and stable, palette-aware desktop presentation."""
import html
from pathlib import Path
import sys
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QPainter, QPixmap, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QProxyStyle, QPushButton, QSizePolicy, QStyle, QStyleFactory)
from i18n import tr, translate_standard_buttons


def plain_tooltip(text):
    return '<qt>' + html.escape(str(text)).replace('\n', '<br>') + '</qt>'


class ScrollableMenuStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class ScreenMenu(QMenu):
    """Compact menus also remain usable on small/high-DPI work areas."""
    def __init__(self, title='', parent=None):
        super().__init__(title, parent)
        # Own a fresh style, never QApplication's shared style object.
        self._scroll_style = ScrollableMenuStyle(QStyleFactory.create('Fusion'))
        self._scroll_style.setParent(self)
        self.setStyle(self._scroll_style)
        self.setToolTipsVisible(True)
        self.aboutToShow.connect(self.fit_to_screen)
        self.fit_to_screen()

    def fit_to_screen(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.setMaximumHeight(max(1, area.height() - 24))
            self.setMaximumWidth(max(1, area.width() - 24))

    def submenu(self, title, icon=None):
        menu = ScreenMenu(title, self)
        if icon is not None:
            menu.setIcon(icon)
        self.addMenu(menu)
        return menu


class LocalizedButtonBox(QDialogButtonBox):
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            translate_standard_buttons(self)


class LocalizedMessageBox(QMessageBox):
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            translate_standard_buttons(self)


class LocalizedInputDialog(QInputDialog):
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.setOkButtonText(tr('OK'))
            self.setCancelButtonText(tr('Cancel'))


def get_text(parent, title, label, echo=QLineEdit.EchoMode.Normal, text=''):
    dialog = LocalizedInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextEchoMode(echo)
    dialog.setTextValue(text)
    dialog.setOkButtonText(tr('OK'))
    dialog.setCancelButtonText(tr('Cancel'))
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.textValue(), accepted

PATHS = {
    'receiver': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 15h.01M11 15h.01M15 9h3M15 12h3"/>',
    'music': '<path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="3"/><ellipse cx="17" cy="16" rx="3" ry="3"/>',
    'bluetooth': '<path d="M7 7l10 10-5 4V3l5 4L7 17"/>',
    'audio': '<path d="M4 9h4l5-4v14l-5-4H4zM17 8a6 6 0 010 8M20 5a10 10 0 010 14"/>',
    'devices': '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    'pairing': '<path d="M12 4v16M4 12h16"/><circle cx="12" cy="12" r="9"/>',
    'key': '<circle cx="8" cy="8" r="5"/><path d="M12 12l9 9M16 16l3-3M19 19l3-3"/>',
    'refresh': '<path d="M20 8a8 8 0 00-14-3L3 8m0-5v5h5M4 16a8 8 0 0014 3l3-3m0 5v-5h-5"/>',
    'temperature': '<path d="M9 14V5a3 3 0 016 0v9a5 5 0 11-6 0zM12 8v9"/>',
    'cpu': '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    'load': '<path d="M3 17l5-7 5 4 8-11M17 3h4v4"/>',
    'memory': '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10v4M12 10v4M17 10v4M7 18v3M12 18v3M17 18v3"/>',
    'disk': '<path d="M5 4h14l3 12v4H2v-4zM2 16h20M16 18h.01M19 18h.01"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'settings': '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    'setup': '<path d="M14 4a5 5 0 00-6 6l-6 6a3 3 0 004 4l6-6a5 5 0 006-6l-4 4-3-3z"/>',
    'stop': '<rect x="5" y="5" width="14" height="14" rx="3"/>',
    'power': '<path d="M12 2v10M6 5a9 9 0 1012 0"/>',
}


def flag_icon(country):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    renderer = QSvgRenderer(str(root / 'assets/flags' / (country + '.svg')))
    if not renderer.isValid():
        return QIcon()
    image = QPixmap(48, 32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QIcon(image)


def symbol(name, color=None):
    color = color or QApplication.palette().color(QPalette.ColorRole.WindowText).name()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
           + PATHS[name] + '</g></svg>')
    renderer = QSvgRenderer(svg.encode())
    result = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        result.addPixmap(image)
    return result


class StableLabel(QLabel):
    """Reserve lines before text arrives; long messages remain accessible."""
    def __init__(self, text='', lines=2, parent=None, padding=4):
        super().__init__('', parent)
        self.lines = lines
        self.padding = padding
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(False)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)
        self.update_height()
        self.setText(text)

    def update_height(self):
        self.setFixedHeight(self.fontMetrics().lineSpacing() * self.lines + self.padding)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, 'lines'):
            self.update_height()

    def setText(self, text):
        super().setText(text)
        self.setToolTip(plain_tooltip(text))
        self.setAccessibleDescription(str(text))


class StatusIndicator(QPushButton):
    """Fixed-size, non-animated status; full text is available on hover/click."""
    def __init__(self, text='', parent=None):
        super().__init__('●', parent)
        self.setFixedSize(28, 28)
        self.setAccessibleName(tr('Receiver status details'))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_state('unknown')
        self.setText(text)

    def set_state(self, state):
        if getattr(self, 'state', None) == state:
            return
        colors = {'ok': '#139b7d', 'warning': '#bf8b23', 'error': '#ce5353', 'unknown': '#8c97a8'}
        self.setStyleSheet('QPushButton { border: none; padding: 0; font-size: 22px; color: '
                          + colors.get(state, colors['unknown']) + '; } QPushButton:hover { background: palette(alternate-base); }')
        self.state = state

    def setText(self, text):
        self._message = str(text)
        self.setToolTip(plain_tooltip(self._message))
        self.setAccessibleDescription(self._message)

    def text(self):
        return self._message


STYLE = '''
QWidget { font-size: 13px; }
QFrame#StatusCard { background: palette(base); border: 1px solid palette(mid); border-radius: 12px; }
QLabel#SectionTitle { font-weight: 600; font-size: 14px; }
QLabel#Notice { background: palette(alternate-base); border-radius: 9px; padding: 10px; }
QPushButton { padding: 8px 12px; border: 1px solid palette(mid); border-radius: 8px; background: palette(button); }
QPushButton:hover { border-color: #139b7d; }
QPushButton:pressed { background: palette(midlight); }
QPushButton:focus { border: 2px solid palette(highlight); padding: 7px 11px; }
QPushButton#Primary { color: white; background: #087f68; border-color: #087f68; }
QPushButton#Primary:hover { background: #096d5b; }
QPushButton:disabled, QPushButton#Primary:disabled { color: palette(mid); background: palette(button); border-color: palette(midlight); }
QLineEdit, QSpinBox { padding: 6px; }
'''
