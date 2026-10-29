"""One-time receiver setup with explicit plan review and masked sudo authentication."""
from i18n import tr, translate_standard_buttons
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QScrollArea, QWidget,
    QLabel, QLineEdit, QPushButton, QPlainTextEdit, QVBoxLayout, QHBoxLayout)

import provision
from core import UnknownHost, display_text, trust_host
from setup_plan import validate_plan
from ui_style import LocalizedMessageBox as QMessageBox, get_text, exec_dialog, WrappedCheckBox, StableLabel


class Task(QThread):
    result = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.result.emit(self.action(self.progress.emit))
        except Exception as exc:
            self.failed.emit(exc)
        finally:
            self.action = None


class SetupDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.worker = None
        self.checked = False
        self.installed_plan = None
        self.setWindowTitle(tr('Set up a new Pi'))
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        intro = QLabel(tr('Start with Raspberry Pi OS Lite / Debian 13 (64-bit), networking and SSH key access enabled.\n'
                       'This installs services on the selected Pi. It does not flash an SD card.\n'
                       'Use Check / repair Pi components for an existing managed receiver.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.target = StableLabel(display_text(config['username'] + '@' + config['host']), lines=2)
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        outer.insertWidget(0, self.target)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.hostname = QLineEdit(config['host'])
        form.addRow(tr('Pi DNS name (TLS certificate)'), self.hostname)
        self.updates = WrappedCheckBox(tr('System updates daily; reboot at 05:30 only if needed (Pi local time)'))
        self.updates.setChecked(True)
        self.music = WrappedCheckBox(tr('MPD + USB music + UPnP browsing'))
        self.web = WrappedCheckBox(tr('HTTPS phone/web player with remembered login (ports 80 / 443 / 8443)'))
        self.cockpit = WrappedCheckBox(tr('Cockpit management, loopback only (SSH tunnel required)'))
        self.privacy = WrappedCheckBox(tr('Hide other users’ process details (protects Soloist key arguments)'))
        self.privacy.setChecked(True)
        self.shutdown = WrappedCheckBox(tr('Allow the tray app to shut down this Pi'))
        self.shutdown.setChecked(True)
        for widget in (self.updates, self.music, self.web, self.cockpit, self.shutdown, self.privacy):
            form.addRow(widget)
        self.usb = QComboBox()
        form.addRow(tr('FAT32 USB drive (must contain Music/)'), self.usb)
        self.resume = WrappedCheckBox(tr('Retry an incomplete wizard installation with the same choices'))
        form.addRow(self.resume)
        self.terms = WrappedCheckBox(tr('Download Soloist directly from Spotify; I have reviewed its terms'))
        form.addRow(self.terms)
        terms = QLabel('<a href="https://developer.spotify.com/documentation/soloist">' + tr('Spotify Soloist documentation and terms') + '</a>')
        terms.setOpenExternalLinks(True)
        form.addRow(terms)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(tr('Leave empty only if this account already has administrator sudo'))
        form.addRow(tr('Pi sudo password (not saved)'), self.password)
        layout.addLayout(form)
        self.web.setEnabled(False)
        self.usb.setEnabled(False)
        self.music.toggled.connect(self.music_changed)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(160)
        self.log.setMinimumHeight(64)
        self.log.setMaximumHeight(96)
        outer.addWidget(self.log)
        self.check = QPushButton(tr('1. Check Pi'))
        self.install_button = QPushButton(tr('2. Review and install…'))
        self.install_button.setEnabled(False)
        self.close_button = QPushButton(tr('Close'))
        for button in (self.check, self.install_button):
            layout.addWidget(button)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(self.close_button)
        outer.addLayout(close_row)
        self.check.clicked.connect(self.preflight)
        self.components_button = QPushButton(tr('Check / repair existing Pi components…'))
        layout.addWidget(self.components_button)
        self.components_button.clicked.connect(self.components)
        self.install_button.clicked.connect(self.install)
        self.close_button.clicked.connect(self.reject)
        audio = QFormLayout()
        audio.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.outputs = QComboBox()
        self.output_refresh = QPushButton(tr('Refresh audio outputs'))
        self.output_select = QPushButton(tr('Use output'))
        self.key_button = QPushButton(tr('Set Soloist API key…'))
        self.key_button.setEnabled(False)
        layout.addWidget(self.key_button)
        self.key_button.clicked.connect(self.set_key)
        self.output_refresh.setEnabled(False)
        self.output_select.setEnabled(False)
        audio.addRow(self.outputs)
        audio.addRow(self.output_refresh)
        audio.addRow(self.output_select)
        layout.addLayout(audio)
        layout.addStretch()
        self.output_refresh.clicked.connect(lambda: self.start(lambda _: provision.audio_outputs(self.config), self.show_outputs))
        self.output_select.clicked.connect(lambda: self.start(lambda _: provision.select_output(self.config, self.outputs.currentData()), lambda _: self.append(tr('Default audio output saved by WirePlumber.'))))
        for combo in (self.usb, self.outputs):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        order = (self.hostname, self.updates, self.music, self.web, self.cockpit,
                 self.shutdown, self.privacy, self.usb, self.resume, self.terms,
                 self.password, self.check, self.install_button,
                 self.components_button, self.key_button, self.outputs,
                 self.output_refresh, self.output_select, self.close_button)
        for before, after in zip(order, order[1:]):
            QWidget.setTabOrder(before, after)
        area = self.screen().availableGeometry()
        self.resize(min(680, max(1, area.width() - 32)),
                    min(760, max(1, area.height() - 48)))

    def append(self, text):
        self.log.appendPlainText(display_text(text, 4096))

    def music_changed(self, enabled):
        self.web.setEnabled(enabled)
        self.usb.setEnabled(enabled)
        if not enabled:
            self.web.setChecked(False)

    def start(self, action, callback):
        if self.worker:
            return
        for button in (self.check, self.install_button, self.close_button, self.output_refresh, self.output_select, self.key_button, self.components_button):
            button.setEnabled(False)
        self.set_options(False)
        worker = Task(action, self)
        self.worker = worker
        worker.progress.connect(self.append)
        worker.result.connect(callback)
        worker.failed.connect(self.failure)
        worker.finished.connect(self.finished_task)
        worker.start()

    def set_options(self, enabled):
        for control in (self.hostname, self.updates, self.music, self.web, self.cockpit, self.shutdown, self.privacy, self.usb, self.resume, self.terms, self.password):
            control.setEnabled(enabled)
        if enabled:
            self.music_changed(self.music.isChecked())

    def finished_task(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.close_button.setEnabled(True)
        self.components_button.setEnabled(True)
        self.check.setEnabled(self.installed_plan is None)
        self.install_button.setEnabled(self.checked and self.installed_plan is None)
        self.set_options(self.installed_plan is None)
        self.key_button.setEnabled(self.installed_plan is not None)
        self.output_refresh.setEnabled(self.installed_plan is not None)
        self.output_select.setEnabled(self.installed_plan is not None and self.outputs.count() > 0)

    def failure(self, exc):
        if isinstance(exc, UnknownHost):
            box = QMessageBox(QMessageBox.Icon.Question, tr('Verify SSH host key'), '', parent=self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(tr('{host}\n{fingerprint}\n\nVerify this fingerprint on the Pi before trusting it.', host=display_text(exc.hostname), fingerprint=exc.fingerprint))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            translate_standard_buttons(box)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if exec_dialog(box) == QMessageBox.StandardButton.Yes:
                try:
                    trust_host(exc)
                except (OSError, ValueError) as error:
                    self.append(str(error))
                else:
                    self.append(tr('Host key saved. Click Check Pi again.'))
        else:
            self.append(str(exc))

    def preflight(self):
        self.checked = False
        self.start(lambda _: provision.preflight(self.config), self.inventory)

    def inventory(self, facts):
        self.append(' · '.join(str(facts.get(k, '')) for k in ('model', 'os', 'architecture', 'user')))
        reasons = facts.get('reasons', [])
        for reason in reasons:
            self.append(reason)
        # Partial retry does not waive platform checks; administrator side rechecks state.
        self.checked = facts['supported'] or (len(reasons) == 1 and reasons[0].startswith('Existing receiver'))
        self.needs_resume = not facts['supported']
        self.usb.clear()
        def visit(devices, usb=False):
            for device in devices:
                is_usb = usb or device.get('tran') == 'usb'
                if is_usb and device.get('fstype') == 'vfat' and device.get('uuid') and not any(p == '/' or (p or '').startswith('/boot') for p in device.get('mountpoints', [])):
                    self.usb.addItem(display_text(str(device['name']) + ' · ' + str(device['uuid'])), str(device['uuid']))
                visit(device.get('children', []), is_usb)
        visit(facts.get('disks', {}).get('blockdevices', []))
        self.append(tr('Base: Soloist + PipeWire Bluetooth sink + status helpers. Pairing starts disabled. Soloist updates daily.'))
        if self.checked:
            self.append(tr('Choose components, review the plan, then install. Keep this window open until setup finishes.'))

    def install(self):
        try:
            if not self.terms.isChecked():
                raise ValueError(tr('Review Spotify Soloist terms and confirm before downloading'))
            if self.needs_resume and not self.resume.isChecked():
                raise ValueError(tr('An existing setup cannot be overwritten. Retry is only for an incomplete installation made by this wizard.'))
            plan = validate_plan({'music': self.music.isChecked(), 'web': self.web.isChecked(), 'updates': self.updates.isChecked(), 'cockpit': self.cockpit.isChecked(), 'shutdown': self.shutdown.isChecked(), 'privacy': self.privacy.isChecked(), 'usb_uuid': (self.usb.currentData() or '') if self.music.isChecked() else '', 'hostname': self.hostname.text().strip()})
            password = self.password.text()
            self.password.clear()
            text = (tr('Install on ') + display_text(self.config['username'] + '@' + self.config['host']) + tr('?\n\n'
                'Soloist, Bluetooth audio receiver, monitoring and daily Soloist updates.\n') +
                '\n'.join(name + ': ' + (tr('yes') if plan[key] else tr('no')) for key, name in [('music', 'MPD / USB / UPnP'), ('web', tr('HTTPS web player')), ('updates', tr('System updates + conditional 05:30 reboot')), ('cockpit', tr('Loopback Cockpit')), ('shutdown', tr('Tray shutdown permission')), ('privacy', tr('Process privacy (hidepid=2)'))]) +
                tr('\n\nUSB filesystem: ') + (plan['usb_uuid'] or tr('none')) + tr('\nHostname: ') + plan['hostname'] +
                tr('\n\nAdministrator installation changes packages, services and restricted sudo rules. No disk is formatted. No playback starts. Partial changes are preserved if setup fails. Audio levels are left unchanged; lower your amplifier volume before first playback.'))
            box = QMessageBox(QMessageBox.Icon.Question, tr('Review installation plan'), '', parent=self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(text)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            translate_standard_buttons(box)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if exec_dialog(box) != QMessageBox.StandardButton.Yes:
                return
            self.start(lambda progress: provision.install(self.config, plan, password, progress), lambda _: self.completed(plan))
        except (ValueError, TypeError) as exc:
            self.append(str(exc))

    def completed(self, plan):
        self.installed_plan = plan
        self.append(tr('Setup complete. Refresh audio outputs and select the connected DAC / HDMI / headphones. Then close this window and set your Soloist key in Settings.'))
        if plan['web']:
            self.append(tr('HTTPS URL: https://') + plan['hostname'] + tr('/ — login and CA are in ~/.config/pi-audio-tray/music-web-login.txt and music-ca.crt on the Pi. See docs/NEW_PI_SETUP.md for secure retrieval and certificate trust.'))
        if plan['cockpit']:
            self.append(tr('Cockpit: create an SSH tunnel with -L 9090:127.0.0.1:9090, then open http://localhost:9090. It is not exposed on the LAN.'))

    def set_key(self):
        from core import store_soloist_key
        key, accepted = get_text(self, tr('Spotify Soloist API key'),
            tr('Paste your personal key from Spotify’s Soloist dashboard. It is sent only to the Pi.'),
            QLineEdit.EchoMode.Password)
        if accepted and key.strip():
            self.start(lambda _: store_soloist_key(self.config, key.strip()),
                       lambda _: self.append(tr('Soloist key saved privately on the Pi; service started. Select Pi Audio in Spotify.')))

    def components(self):
        if self.worker:
            return
        from component_ui import ComponentsDialog
        dialog = ComponentsDialog(self.config, self)
        exec_dialog(dialog, dialog.check)
        self.checked = False  # Any component changes invalidate a prior fresh-install probe.
        self.install_button.setEnabled(False)

    def show_outputs(self, outputs):
        self.outputs.clear()
        for identifier, name in outputs:
            self.outputs.addItem(display_text(name), identifier)
        if not outputs:
            self.append(tr('No audio sink yet. Check hardware and Raspberry Pi OS audio overlays, then refresh.'))

    def reject(self):
        if self.worker:
            return  # Do not terminate administrator package/service operations mid-flight.
        self.password.clear()
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            self.password.clear()
            super().closeEvent(event)
