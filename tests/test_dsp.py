"""EQ data bounds, graph isolation, rollback and explicit desktop controls."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import dsp
import dsp_config as config

ROOT = Path(__file__).resolve().parents[1]


def settings(**changes):
    return {'enabled': True, 'target': 'alsa_output.example.stereo', 'gains': [0]*10, 'preamp': 0, **changes}


def status(**changes):
    return {'protocol': 1, 'installed': True, 'supported': True, 'state': 'inactive',
            'settings': settings(enabled=False), 'configuration_error': False,
            'outputs': [{'name': 'alsa_output.example.stereo', 'description': 'Example headphones', 'default': True}],
            'target_available': True, 'active': False, 'processing': False, 'headroom_db': 0, **changes}


class GraphPlan(unittest.TestCase):
    def test_rejects_unbounded_gain_injection_and_noninteger_controls(self):
        invalid = [settings(gains=[0]*9), settings(gains=[True]*10), settings(gains=[float('nan')]*10),
                   settings(gains=[7]*10), settings(preamp=1), settings(preamp=-13),
                   settings(target='pi_audio.dsp.input'), settings(target='sink" } plugin = evil'),
                   settings(enabled=1), {**settings(), 'plugin': '/tmp/untrusted.so'}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError): config.validate(value)

    def test_fixed_builtins_and_physical_target_never_change_default_or_load_plugins(self):
        value=json.loads(config.configuration(settings()))
        self.assertFalse(value['context.properties']['core.daemon'])
        args=value['context.modules'][-1]['args']
        self.assertTrue(all(n['type']=='builtin' and 'plugin' not in n for n in args['filter.graph']['nodes']))
        self.assertEqual(args['capture.props']['filter.smart.target'],{'node.name':'alsa_output.example.stereo'})
        self.assertFalse(args['capture.props']['filter.smart.targetable'])
        self.assertEqual(args['capture.props']['node.link-group'],args['playback.props']['node.link-group'])
        self.assertTrue(args['playback.props']['node.dont-fallback'])
        self.assertFalse(args['playback.props']['state.restore-props'])
        self.assertEqual(args['audio.rate'],48000)
        self.assertEqual(value['context.modules'][-1]['args']['filter.graph']['nodes'][-1]['control'], {'Min':-1.0,'Max':1.0})

    def test_headroom_compensates_all_positive_gains_and_preamp_only_attenuates(self):
        value=settings(gains=[6,-6,3,0,0,0,0,0,0,0],preamp=-2)
        self.assertEqual(config.headroom(value),9)
        graph=json.loads(config.configuration(value))['context.modules'][-1]['args']['filter.graph']
        self.assertAlmostEqual(graph['nodes'][0]['control']['Mult'],10**(-11/20))
        self.assertEqual(json.loads(config.configuration(settings()))['context.modules'][-1]['args']['filter.graph']['nodes'][0]['control']['Mult'],1)
        for preset in config.PRESETS.values(): config.validate(settings(gains=list(preset)))

    def test_status_schema_rejects_contradictions_and_unsafe_output_data(self):
        self.assertEqual(dsp.validate_status(status()),status())
        for value in (status(headroom_db=True),status(state=[]),status(active=True),status(processing=True),
                      status(target_available=False),status(outputs=[{'name':'sink','description':'bad\ntext','default':True}]),
                      status(outputs=status()['outputs']*2)):
            with self.subTest(value=value), self.assertRaises(ValueError): dsp.validate_status(value)


class Transport(unittest.TestCase):
    def test_bad_settings_never_connect_and_valid_settings_use_stdin(self):
        with patch.object(dsp,'connect') as connect,self.assertRaises(ValueError):
            dsp.request({},'configure',settings(preamp=4))
        connect.assert_not_called()
        client=Mock()
        with patch.object(dsp,'connect',return_value=client),patch.object(dsp,'run',return_value=json.dumps(status())) as run:
            dsp.request({},'configure',settings())
        self.assertEqual(run.call_args.args[1],dsp.HELPER+' configure')
        self.assertEqual(json.loads(run.call_args.kwargs['input_data']),settings())
        client.close.assert_called_once()

    def test_preinstall_status_is_read_only_source_and_bypass_has_no_payload(self):
        for action in ('status','bypass'):
            client=Mock()
            with patch.object(dsp,'connect',return_value=client),patch.object(dsp,'run',return_value=json.dumps(status())) as run:
                dsp.request({},action)
            if action=='status':
                self.assertEqual(run.call_args.args[1],'/usr/bin/python3 -I - status')
                self.assertIn('Normal-user EQ controls',run.call_args.kwargs['input_data'])
            else:
                self.assertEqual(run.call_args.args[1],dsp.HELPER+' bypass')
                self.assertIsNone(run.call_args.kwargs['input_data'])
            client.close.assert_called_once()

    def test_install_uses_exact_manifest_and_secret_free_plan(self):
        with patch.object(dsp.provision,'administrator_install') as install:
            dsp.install({},'example-only',print)
        self.assertEqual(install.call_args.args[1],{})
        self.assertEqual(install.call_args.args[4:],('install-dsp.py',dsp.PI_FILES,dsp.TOP_FILES))
        for path in dsp.PI_FILES: self.assertTrue((ROOT/'pi'/path).is_file())
        for path in dsp.TOP_FILES: self.assertTrue((ROOT/path).is_file())


@unittest.skipIf(os.name!='posix','POSIX receiver private files')
class Runtime(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('dsp_receiver_test',ROOT/'pi_dsp.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        m=self.module
        m.DIRECTORY=self.base/'private';m.SETTINGS=m.DIRECTORY/'dsp.json';m.GRAPH=m.DIRECTORY/'dsp.conf'
        m.HELPER=self.base/'helper';m.HELPER.touch();m.UNIT_FILE=self.base/'unit';m.UNIT_FILE.touch()
        self.commands=[]
        self.patches=[patch.object(m,'plan',return_value=config),patch.object(m,'supported',return_value=True),
                      patch.object(m,'graph',return_value=[]),patch.object(m,'outputs',return_value=status()['outputs']),
                      patch.object(m,'command',side_effect=self.command)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def command(self,*args,**kwargs):
        self.commands.append(args)
        return types.SimpleNamespace(stdout='enabled' if 'is-enabled' in args else 'active',returncode=0)

    def test_private_preferences_no_default_volume_commands_and_explicit_bypass(self):
        m=self.module
        with patch.object(m,'status',return_value=status()):m.configure(settings(enabled=False))
        self.assertEqual(m.DIRECTORY.stat().st_mode&0o777,0o700)
        self.assertEqual(m.SETTINGS.stat().st_mode&0o777,0o600)
        self.assertEqual(m.GRAPH.stat().st_mode&0o777,0o600)
        self.assertIn(('/usr/bin/systemctl','--user','disable','--now',m.UNIT),self.commands)
        self.assertFalse(any('set-default' in c or 'set-volume' in c for c in self.commands))

    def test_start_failure_restores_previous_files_and_service_enablement(self):
        m=self.module
        m.write_file(m.SETTINGS,b'{"previous":"settings"}');m.write_file(m.GRAPH,b'previous graph')
        before=m.read_file(m.SETTINGS)
        def fail(*args,**kwargs):
            if 'restart' in args:raise RuntimeError('fixture startup failure')
            return self.command(*args,**kwargs)
        with patch.object(m,'command',side_effect=fail),self.assertRaises(RuntimeError):m.configure(settings())
        self.assertEqual(m.read_file(m.SETTINGS),before)
        self.assertEqual(m.read_file(m.GRAPH),b'previous graph')
        self.assertIn(('/usr/bin/systemctl','--user','start',m.UNIT),self.commands)

    def test_bypass_still_stops_service_with_invalid_preferences(self):
        m=self.module;m.write_file(m.SETTINGS,b'not valid JSON')
        with patch.object(m,'status',return_value=status(configuration_error=True,settings=None,target_available=False)):
            m.bypass()
        self.assertEqual(m.read_file(m.SETTINGS),b'not valid JSON')
        self.assertIn(('/usr/bin/systemctl','--user','disable','--now',m.UNIT),self.commands)

    def test_missing_target_is_rejected_before_mutations(self):
        with patch.object(self.module,'outputs',return_value=[]),self.assertRaises(ValueError):self.module.configure(settings())
        self.assertFalse(self.module.SETTINGS.exists())
        self.assertEqual(self.commands,[])

    def test_private_reader_rejects_symlinks_and_fifos_without_blocking(self):
        m=self.module;m.secure_directory()
        target=self.base/'target';target.write_bytes(b'{}');target.chmod(0o600)
        link=m.DIRECTORY/'link';link.symlink_to(target)
        with self.assertRaises(OSError):m.read_file(link)
        fifo=m.DIRECTORY/'fifo';os.mkfifo(fifo,0o600)
        with self.assertRaises(ValueError):m.read_file(fifo)


class Dialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_per_pi_capture_preset_edits_are_local_and_no_polling_timer(self):
        from dsp_ui import DspDialog
        from PySide6.QtCore import QTimer
        connection={'host':'audio-pi.local','username':'audio'}
        dialog=DspDialog(connection);connection['host']='another.local'
        with patch.object(dsp,'request') as request:
            dialog.show_status(status());dialog.controls()
            dialog.preset.setCurrentIndex(dialog.preset.findData('warm'))
            self.assertEqual([s.value() for s in dialog.sliders],list(config.PRESETS['warm']))
            dialog.sliders[0].setValue(4)
            self.assertEqual(dialog.preset.currentData(),'custom')
            request.assert_not_called()
        self.assertEqual(dialog.config['host'],'audio-pi.local')
        self.assertEqual(dialog.findChildren(QTimer),[])
        dialog.deleteLater()


if __name__=='__main__':unittest.main()


@unittest.skipIf(os.name != 'posix', 'POSIX receiver installer')
class Installer(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('dsp_install_test', ROOT/'pi/install-dsp.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        m = self.module
        m.STATE = self.base/'state'; m.STATE.mkdir()
        m.UNIT_FILE = self.base/'units/pi-audio-dsp.service'
        m.HELPER = self.base/'helpers/pi-dsp'
        feature = self.base/'feature'; feature.touch()
        m.REQUIRED_FILES = (str(feature),)
        self.snapshot = self.base/'bundle'; self.snapshot.mkdir()
        for name in ('pi_dsp.py', 'dsp_config.py', 'pi-audio-dsp.service'):
            source = ROOT/('pi' if name.endswith('.service') else '')/name
            (self.snapshot/name).write_text(source.read_text())
        self.receiver = Mock()
        self.account = types.SimpleNamespace(pw_name='audio')

    def deploy(self):
        self.module.deploy(self.receiver, self.account, 1000, self.snapshot)

    def test_fresh_deployment_writes_only_fixed_files_and_never_starts_audio(self):
        self.deploy()
        m = self.module
        writes = self.receiver.write.call_args_list
        self.assertEqual([(c.args[0], c.args[2]) for c in writes], [
            (m.STATE/'owner', 0o600), (m.HELPER, 0o755),
            (m.HELPER.with_name('dsp_config.py'), 0o644), (m.UNIT_FILE, 0o644)])
        self.assertEqual([c.args[0] for c in self.receiver.public_dir.call_args_list],
                         [m.UNIT_FILE.parent, m.HELPER.parent])
        commands = [c.args for c in self.receiver.run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertIn('is-active', commands[0]); self.assertIn('daemon-reload', commands[1])
        self.assertFalse(any('start' in c or 'restart' in c or 'enable' in c for c in commands))

    def test_repair_bypasses_owned_filter_without_restarting_players(self):
        m = self.module
        (m.STATE/'owner').write_text('audio\n')
        m.UNIT_FILE.parent.mkdir(); m.UNIT_FILE.touch()
        self.deploy()
        commands = [c.args for c in self.receiver.run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[1][-5:], ('/usr/bin/systemctl', '--user', 'disable', '--now', 'pi-audio-dsp.service'))
        self.assertFalse(any('restart' in c or 'set-default' in c or 'set-volume' in c for c in commands))

    def test_missing_features_foreign_install_and_different_owner_refuse_mutations(self):
        m = self.module
        for case in ('features', 'foreign', 'owner'):
            with self.subTest(case=case):
                self.receiver.reset_mock()
                m.REQUIRED_FILES = (str(self.base/'absent'),) if case == 'features' else ()
                if case == 'foreign':
                    m.HELPER.parent.mkdir(); m.HELPER.touch()
                if case == 'owner':
                    (m.STATE/'owner').write_text('other-account\n')
                with self.assertRaises(ValueError): self.deploy()
                self.receiver.write.assert_not_called()
                self.receiver.public_dir.assert_not_called()
                self.assertEqual(self.receiver.run.call_count, 1)
