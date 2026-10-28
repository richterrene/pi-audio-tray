"""Per-Pi equalizer: explicit apply/bypass, no hidden polling or live slider writes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout)

import dsp
import dsp_config
from core import display_text
from i18n import tr
from maintenance_ui import MaintenanceDialog


class DspDialog(MaintenanceDialog):
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Equalizer'))
        self.resize(780, 590)
        self.updating = False
        self.enabled = QCheckBox(tr('Enable equalizer'))
        self.body.addWidget(self.enabled)
        form = QFormLayout()
        self.output = QComboBox()
        self.preset = QComboBox()
        for label, key in ((tr('Flat'), 'flat'), (tr('Warm'), 'warm'),
                           (tr('Bass lift'), 'bass'), (tr('Speech'), 'speech'),
                           (tr('Soft treble'), 'soft_treble'), (tr('Custom'), 'custom')):
            self.preset.addItem(label, key)
        self.preamp = QSpinBox()
        self.preamp.setRange(-12, 0)
        self.preamp.setSuffix(' dB')
        form.addRow(tr('Physical stereo output'), self.output)
        form.addRow(tr('Preset'), self.preset)
        form.addRow(tr('Preamp'), self.preamp)
        self.body.addLayout(form)
        bands = QHBoxLayout()
        self.sliders, self.levels = [], []
        for frequency in dsp_config.FREQUENCIES:
            column = QVBoxLayout()
            title = QLabel((str(frequency / 1000).rstrip('0').rstrip('.') + 'k') if frequency >= 1000 else str(frequency))
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setAccessibleName(tr('{frequency} Hz gain', frequency=frequency))
            slider.setRange(-6, 6)
            slider.setTickInterval(3)
            slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
            slider.setMinimumHeight(140)
            row = QHBoxLayout()
            row.addStretch(); row.addWidget(slider); row.addStretch()
            level = QLabel('0 dB')
            level.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(title)
            column.addLayout(row)
            column.addWidget(level)
            bands.addLayout(column)
            self.sliders.append(slider)
            self.levels.append(level)
            slider.valueChanged.connect(self.edit_band)
        self.body.addLayout(bands)
        self.headroom = QLabel()
        self.headroom.setMinimumHeight(30)
        self.headroom.setWordWrap(True)
        self.body.addWidget(self.headroom)
        note = QLabel(tr('Applies to audio routed to the selected stereo output. Boosts add conservative automatic headroom and may lower loudness. Apply briefly reconnects the filter; bypass preserves the physical output and master volume.'))
        note.setWordWrap(True)
        self.body.addWidget(note)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.apply_button = QPushButton(tr('Apply equalizer'))
        self.bypass_button = QPushButton(tr('Bypass now'))
        self.close_button = QPushButton(tr('Close'))
        for button in (self.refresh_button, self.apply_button, self.bypass_button, self.close_button):
            row.addWidget(button)
        self.body.addLayout(row)
        self.preset.currentIndexChanged.connect(self.choose_preset)
        self.preamp.valueChanged.connect(self.update_headroom)
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button.clicked.connect(self.apply)
        self.bypass_button.clicked.connect(lambda: self.start(lambda _: dsp.request(self.config, 'bypass'), self.show_status))
        self.close_button.clicked.connect(self.reject)
        self.update_headroom()
        self.controls()

    def controls(self):
        if not hasattr(self, 'apply_button'):
            return
        idle = self.worker is None
        supported = bool(self.snapshot and self.snapshot['supported'])
        ready = bool(idle and supported and self.snapshot['installed'])
        for widget in (self.enabled, self.output, self.preset, self.preamp, *self.sliders):
            widget.setEnabled(ready)
        for widget in (self.refresh_button, self.close_button):
            widget.setEnabled(idle)
        self.apply_button.setEnabled(ready and bool(self.output.currentData()))
        self.bypass_button.setEnabled(bool(idle and self.snapshot and self.snapshot['installed']))

    def update_headroom(self):
        for slider, label in zip(self.sliders, self.levels):
            label.setText(f'{slider.value():+d} dB' if slider.value() else '0 dB')
        protection = dsp_config.headroom({'enabled': False, 'target': 'preview',
            'gains': [s.value() for s in self.sliders], 'preamp': self.preamp.value()})
        self.show_headroom(protection)

    def show_headroom(self, protection):
        self.headroom.setText(tr('Automatic headroom: −{gain} dB · Effective preamp: {preamp} dB',
                                 gain=protection, preamp=self.preamp.value() - protection))

    def edit_band(self):
        if not self.updating:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData('custom'))
            self.preset.blockSignals(False)
        self.update_headroom()

    def choose_preset(self):
        if self.updating or self.preset.currentData() == 'custom':
            return
        self.updating = True
        try:
            for slider, gain in zip(self.sliders, dsp_config.PRESETS[self.preset.currentData()]):
                slider.setValue(gain)
        finally:
            self.updating = False
        self.update_headroom()

    def refresh(self):
        self.start(lambda _: dsp.request(self.config), self.show_status)

    def show_status(self, value):
        self.snapshot = value
        self.updating = True
        try:
            saved = value['settings']
            selected = saved['target'] if saved else ''
            self.output.clear()
            for output in value['outputs']:
                self.output.addItem(display_text(output['description']), output['name'])
                if output['name'] == selected or (not selected and output['default']):
                    self.output.setCurrentIndex(self.output.count() - 1)
            if selected and self.output.findData(selected) < 0:
                self.output.addItem(tr('Unavailable: {output}', output=selected), selected)
                self.output.setCurrentIndex(self.output.count() - 1)
            self.enabled.setChecked(bool(saved and saved['enabled']))
            gains = saved['gains'] if saved else [0] * 10
            for slider, gain in zip(self.sliders, gains):
                slider.setValue(gain)
            self.preamp.setValue(saved['preamp'] if saved else 0)
            preset = next((k for k, v in dsp_config.PRESETS.items() if list(v) == gains), 'custom')
            self.preset.setCurrentIndex(self.preset.findData(preset))
        finally:
            self.updating = False
        self.update_headroom()
        if not value['installed']:
            text = (tr('Install the equalizer controls first. Installation leaves EQ bypassed.')
                    + '\n' + tr('Maintenance') + ' → ' + tr('Check / repair Pi components…'))
        elif value['configuration_error']:
            text = tr('Saved EQ settings could not be read. Bypass is available; applying new settings replaces the invalid preferences.')
        elif value['active']:
            text = tr('Equalizer enabled. Stereo processing runs on the Pi; the hardware default and master volume are unchanged.')
        elif saved and saved['enabled'] and not value['target_available']:
            text = tr('The selected output is unavailable. Other outputs bypass EQ; reconnect it or select another output.')
        else:
            text = tr('Equalizer bypassed. Playback uses the normal output path.')
        if not value['supported']:
            text = tr('This receiver does not have the supported PipeWire smart-filter components.')
        if saved and value['headroom_db'] != dsp_config.headroom(saved):
            self.show_headroom(value['headroom_db'])
            text += '\n' + tr('Update the Pi equalizer through Pi components to use the improved headroom calculation.')
        self.progress(text)

    def apply(self):
        settings = {'enabled': self.enabled.isChecked(), 'target': self.output.currentData(),
                    'gains': [s.value() for s in self.sliders], 'preamp': self.preamp.value()}
        try:
            dsp_config.validate(settings)
        except ValueError as exc:
            self.progress(str(exc)); return
        self.start(lambda _: dsp.request(self.config, 'configure', settings), self.show_status)


class DspInstallDialog(MaintenanceDialog):
    """Administrator-only installation workflow, reached through Pi components."""
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Install / repair equalizer…'))
        self.resize(610, 260)
        self.recheck = False
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow(tr('Pi sudo password (not saved)'), self.password)
        self.body.addLayout(form)
        row = QHBoxLayout()
        self.install_button = QPushButton(tr('Install / repair equalizer…'))
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.close_button = QPushButton(tr('Close'))
        for button in (self.install_button, self.refresh_button, self.close_button):
            row.addWidget(button)
        self.body.addLayout(row)
        self.install_button.clicked.connect(self.install)
        self.refresh_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def controls(self):
        idle = self.worker is None
        for widget in (self.password, self.refresh_button, self.close_button):
            widget.setEnabled(idle)
        self.install_button.setEnabled(bool(idle and self.snapshot and self.snapshot['supported']))

    def refresh(self):
        self.start(lambda _: dsp.request(self.config), self.show_status)

    def show_status(self, value):
        self.snapshot = value
        if not value['supported']:
            text = tr('This receiver does not have the supported PipeWire smart-filter components.')
        elif not value['installed']:
            text = tr('Install the equalizer controls first. Installation leaves EQ bypassed.')
        else:
            text = tr('Equalizer') + ': ' + tr(value['state'])
        self.progress(text)

    def install(self):
        password = self.password.text()
        self.password.clear()
        if not self.confirm(tr('Install or repair the fixed equalizer helper and user service on this Pi? EQ will be bypassed, saved settings retained, and existing players kept running. No extra root runtime permissions or network listeners are added.')):
            return
        def completed(_):
            self.recheck = True
        self.start(lambda progress: dsp.install(self.config, password, progress), completed)

    def finished_task(self):
        super().finished_task()
        if self.recheck:
            self.recheck = False
            self.refresh()

    def failure(self, exc):
        self.recheck = False
        super().failure(exc)

    def reject(self):
        if not self.worker:
            self.password.clear()
        super().reject()
