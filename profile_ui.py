"""Manage connection profiles without changing any remote Pi."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidgetItem, QPushButton, QVBoxLayout
from i18n import tr
import profiles
from ui_style import get_text, ActivatedListWidget


class ProfilesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.selected = None
        self.setWindowTitle(tr('Manage Pis'))
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        note = QLabel(tr('Save a separate SSH connection for each Pi. One Pi is active at a time.\nRemoving a profile does not change the Pi or delete SSH keys.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = ActivatedListWidget()
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemActivated.connect(lambda _: self.use_selected())
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.add_button = QPushButton(tr('Add Pi…'))
        self.rename_button = QPushButton(tr('Rename…'))
        self.remove_button = QPushButton(tr('Remove profile…'))
        for button, callback in ((self.add_button, self.add), (self.rename_button, self.rename), (self.remove_button, self.remove)):
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        close = QPushButton(tr('Close'))
        close.clicked.connect(self.reject)
        self.use_button = QPushButton(tr('Use selected Pi'))
        self.use_button.clicked.connect(self.use_selected)
        row.addWidget(close)
        row.addWidget(self.use_button)
        layout.addLayout(row)
        self.reload()

    def reload(self, selected=None):
        self.data = profiles.load()
        self.list.clear()
        for profile in self.data['profiles']:
            label = profile['name'] + (tr(' (active)') if profile['id'] == self.data['active'] else '')
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, profile['id'])
            self.list.addItem(item)
            if profile['id'] == (selected or self.data['active']):
                self.list.setCurrentItem(item)
        self.add_button.setEnabled(len(self.data['profiles']) < profiles.MAX_PROFILES)
        self.selection_changed()

    def identifier(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selection_changed(self, *args):
        identifier = self.identifier()
        self.rename_button.setEnabled(identifier is not None)
        self.remove_button.setEnabled(identifier is not None and identifier != self.data['active'])
        self.use_button.setEnabled(identifier is not None)

    def perform(self, operation):
        try:
            identifier = operation()
            self.reload(identifier)
        except (OSError, ValueError, TypeError) as exc:
            from app import plain_warning
            plain_warning(self, tr('Pi profiles'), str(exc))

    def add(self):
        name, accepted = get_text(self, tr('Add Pi'), tr('Profile name'), text=tr('New Pi'))
        if accepted:
            self.perform(lambda: profiles.add(name))

    def rename(self):
        identifier = self.identifier()
        current = next(row for row in self.data['profiles'] if row['id'] == identifier)
        name, accepted = get_text(self, tr('Rename Pi profile'), tr('Profile name'), text=current['name'])
        if accepted:
            def operation():
                profiles.rename(identifier, name)
                return identifier
            self.perform(operation)

    def remove(self):
        from app import plain_question
        from PySide6.QtWidgets import QMessageBox
        identifier = self.identifier()
        if not identifier or identifier == self.data['active']:
            return
        current = next(row for row in self.data['profiles'] if row['id'] == identifier)
        if plain_question(self, tr('Remove Pi profile?'), tr('Remove the saved connection for {name}? This does not change the Pi or delete SSH keys.', name=current['name'])) == QMessageBox.StandardButton.Yes:
            self.perform(lambda: profiles.remove(identifier))

    def use_selected(self):
        self.selected = self.identifier()
        if self.selected:
            self.accept()
