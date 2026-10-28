#!/usr/bin/env python3
"""Opt-in live graph test using only temporary null output and synthetic audio.

No real music, microphone, default-device writes or hardware playback. Run as
the receiver's audio user with --allow-null-probe; needs installed PipeWire tools.
"""
import array
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
import wave


def command(*args):
    return subprocess.check_output(args, text=True, timeout=12)


def graph():
    value = command('/usr/bin/pw-dump')
    if len(value) > 4 * 1024**2:
        raise ValueError('Graph too large')
    return json.loads(value)


def props(node):
    return (node.get('info') or {}).get('props') or {}


def nodes():
    return {props(x).get('node.name'): x for x in graph() if x.get('type') == 'PipeWire:Interface:Node'}


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)


def cpu_ticks(pid):
    data = Path('/proc') / str(pid) / 'stat'
    fields = data.read_text().rsplit(')', 1)[1].split()
    return int(fields[11]) + int(fields[12])


def probe(module, gains=None, tone=1000, expected_gain=-6):
    requested_gains = None if gains is None else tuple(gains)
    prefix = 'pi_audio_probe_' + uuid.uuid4().hex
    target = prefix + '.target'
    original = command('/usr/bin/wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@').splitlines()[0]
    children = []
    observed = []
    with tempfile.TemporaryDirectory(prefix='pi-audio-null-dsp-') as folder:
        directory = Path(folder)
        logs = []
        def spawn(label, argv):
            path = directory / (label + '.log')
            stream = path.open('wb')
            logs.append((label, path, stream))
            child = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=stream)
            children.append(child)
            return child
        try:
            null_props = {'factory.name': 'support.null-audio-sink', 'node.name': target,
                'media.class': 'Audio/Sink', 'node.virtual': True, 'node.driver': True,
                'priority.session': -20000, 'priority.driver': -20000,
                'audio.channels': 2, 'audio.position': ['FL', 'FR'], 'audio.rate': 48000,
                'state.restore-props': False, 'state.default-volume': 1.0}
            null = spawn('null', ['/usr/bin/pw-cli', '--monitor', 'create-node', 'adapter', json.dumps(null_props)])
            deadline = time.monotonic() + 5
            while target not in nodes():
                if time.monotonic() >= deadline or null.poll() is not None:
                    raise RuntimeError('Temporary null output could not be created')
                time.sleep(.1)
            rates = (44100, 48000)
            measurements = []
            for rate in rates:
                source = directory / ('sine-' + str(rate) + '.wav')
                samples = array.array('h', (round(.12 * 32767 * math.sin(2 * math.pi * tone * i / rate))
                                           for i in range(rate * 16) for channel in range(2)))
                if sys.byteorder != 'little': samples.byteswap()
                with wave.open(str(source), 'wb') as output:
                    output.setnchannels(2); output.setsampwidth(2); output.setframerate(rate)
                    output.writeframes(samples.tobytes())
                settings = {'enabled': True, 'target': target, 'gains': [0] * 10, 'preamp': 0}
                if requested_gains is None:
                    settings['gains'][5] = -6
                else:
                    settings['gains'] = list(requested_gains)
                config = json.loads(module.configuration(settings))
                args = config['context.modules'][-1]['args']
                for key, suffix in (('capture.props', '.input'), ('playback.props', '.output')):
                    args[key]['node.name'] = prefix + suffix
                    args[key]['node.link-group'] = prefix
                args['capture.props']['filter.smart.name'] = prefix
                # Match the production serializer. The installed PipeWire 1.4
                # parser rejects the equivalent compact module-array document.
                path = directory / 'filter.conf'
                path.write_text(json.dumps(config, indent=2) + '\n')
                path.chmod(0o600)
                dsp = spawn('filter-' + str(rate), ['/usr/bin/pipewire', '-c', str(path)])
                time.sleep(.7)
                if dsp.poll() is not None: raise RuntimeError('DSP process exited')
                observed.append({'phase': 'before-play', 'nodes': [
                    {k: props(x).get(k) for k in ('node.name', 'media.class', 'node.link-group', 'filter.smart.target', 'node.linger')}
                    for x in nodes().values() if str(props(x).get('node.name', '')).startswith((prefix, 'pi_audio.dsp'))]})
                record = directory / ('record-' + str(rate) + '.wav')
                safe = {'node.dont-fallback': True, 'state.restore-props': False,
                        'state.default-volume': 1.0, 'application.name': 'Pi Audio DSP probe'}
                # A normal sink-monitor request is smart-filtered to the input
                # monitor (pre-EQ). Connect this synthetic-only capture directly
                # to the null target's monitor ports to measure post-EQ audio.
                recorder = spawn('record-' + str(rate), ['/usr/bin/pw-record', '--target', '0', '--rate', '48000',
                    '--channels', '2', '--format', 's16', '-P', json.dumps({**safe, 'stream.capture.sink': True,
                    'node.autoconnect': False, 'node.name': prefix + '.record'}), str(record)])
                time.sleep(.5)
                current = graph()
                by_name = {props(x).get('node.name'): x for x in current if x.get('type') == 'PipeWire:Interface:Node'}
                target_id, record_id = by_name[target]['id'], by_name[prefix + '.record']['id']
                def port(identifier, channel, direction):
                    found = [x['id'] for x in current if x.get('type') == 'PipeWire:Interface:Port'
                             and props(x).get('node.id') == identifier and props(x).get('audio.channel') == channel
                             and props(x).get('port.direction') == direction]
                    if len(found) != 1: raise RuntimeError('Ambiguous synthetic monitor port')
                    return found[0]
                for channel in ('FL', 'FR'):
                    command('/usr/bin/pw-link', str(port(target_id, channel, 'out')), str(port(record_id, channel, 'in')))
                player = spawn('play-' + str(rate), ['/usr/bin/pw-play', '--target', target, '-P', json.dumps({**safe,
                    'node.name': prefix + '.source', 'node.linger': True}), str(source)])
                time.sleep(1)
                snapshot = graph()
                named = {props(x).get('node.name'): x for x in snapshot if x.get('type') == 'PipeWire:Interface:Node'}
                required = [target, prefix + '.source', prefix + '.input', prefix + '.output', prefix + '.record']
                if any(name not in named for name in required):
                    raise RuntimeError('Probe nodes missing: ' + str([name for name in required if name not in named]))
                edges = {(x['info']['output-node-id'], x['info']['input-node-id']) for x in snapshot
                         if x.get('type') == 'PipeWire:Interface:Link'}
                ids = {name: named[name]['id'] for name in required}
                expected = {(ids[prefix+'.source'], ids[prefix+'.input']),
                            (ids[prefix+'.output'], ids[target]), (ids[target], ids[prefix+'.record'])}
                if not expected <= edges or (ids[prefix+'.source'], ids[target]) in edges:
                    raise RuntimeError('Unexpected DSP routing; expected='+str(sorted(expected))+' actual='+str(sorted(e for e in edges if e[0] in ids.values() or e[1] in ids.values()))+' ids='+str(ids))
                start, ticks = time.monotonic(), cpu_ticks(dsp.pid)
                time.sleep(2)
                usage = 100 * (cpu_ticks(dsp.pid) - ticks) / os.sysconf('SC_CLK_TCK') / (time.monotonic() - start)
                stop(recorder)
                with wave.open(str(record), 'rb') as capture:
                    raw = capture.readframes(capture.getnframes())
                values = array.array('h', raw)
                if sys.byteorder != 'little': values.byteswap()
                # Exclude start-up; measure a complete one-second central window.
                gains = []
                for channel in (0, 1):
                    samples = values[48000 * 2 + channel:48000 * 4:2]
                    if len(samples) != 48000: raise RuntimeError('Synthetic capture incomplete')
                    rms = math.sqrt(statistics.fmean(x*x for x in samples))
                    gain = 20 * math.log10(rms / (.12 * 32767 / math.sqrt(2))) if rms else -999
                    if abs(gain - expected_gain) > .6:
                        raise RuntimeError('Measured stereo EQ response is incorrect')
                    gains.append(round(gain, 2))
                # Inspect only this probe's filter counters, never report other
                # clients or track metadata from pw-top.
                top = command('/usr/bin/pw-top', '-b', '-n', '2')
                errors = {}
                for line in top.splitlines():
                    fields = line.split()
                    if len(fields) >= 10 and fields[1] in {str(ids[prefix+'.input']), str(ids[prefix+'.output'])}:
                        if fields[8].isdigit():
                            errors[fields[1]] = int(fields[8])
                if len(errors) != 2: raise RuntimeError('DSP xrun counters unavailable')
                if any(errors.values()): raise RuntimeError('DSP xrun counters are nonzero')
                # A stable output name must reconnect without restarting DSP or
                # changing the physical default. Only our null sink is removed.
                stop(null)
                time.sleep(.3)
                if dsp.poll() is not None: raise RuntimeError('DSP exited during target loss')
                null = spawn('null-reconnected-' + str(rate), ['/usr/bin/pw-cli', '--monitor', 'create-node', 'adapter', json.dumps(null_props)])
                deadline = time.monotonic() + 5
                while True:
                    after = graph()
                    found = {props(x).get('node.name'): x['id'] for x in after if x.get('type') == 'PipeWire:Interface:Node'}
                    links = {(x['info']['output-node-id'], x['info']['input-node-id']) for x in after if x.get('type') == 'PipeWire:Interface:Link'}
                    target_id = found.get(target)
                    if target_id is not None and (ids[prefix+'.output'], target_id) in links and (ids[prefix+'.source'], ids[prefix+'.input']) in links:
                        ids[target] = target_id
                        break
                    if time.monotonic() >= deadline: raise RuntimeError('Stable-name output hotplug did not recover')
                    time.sleep(.1)
                # Process death must return the source directly to the null output.
                stop(dsp)
                time.sleep(.7)
                after = graph()
                edges = {(x['info']['output-node-id'], x['info']['input-node-id']) for x in after if x.get('type') == 'PipeWire:Interface:Link'}
                if (ids[prefix+'.source'], ids[target]) not in edges:
                    raise RuntimeError('Automatic bypass did not recover after DSP exit')
                stop(player)
                measurements.append({'input_rate': rate, 'measured_stereo_gain_db': gains, 'dsp_xruns': sum(errors.values()), 'hotplug_recovered': True,
                                     'dsp_cpu_one_core_percent': round(usage, 2), 'failure_bypass': True})
                if command('/usr/bin/wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@').splitlines()[0] != original:
                    raise RuntimeError('Default output changed during isolated probe')
            return measurements
        except Exception as exc:
            details = {label: path.read_text(errors='replace')[-2000:] for label, path, _ in logs}
            raise RuntimeError(str(exc) + '; observations: ' + json.dumps(observed) + '; probe diagnostics: ' + json.dumps(details)) from None
        finally:
            for child in reversed(children): stop(child)
            for _, _, stream in logs: stream.close()
            if command('/usr/bin/wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@').splitlines()[0] != original:
                raise RuntimeError('Default output was not preserved')


def main():
    if sys.argv[1:] != ['--allow-null-probe'] or os.geteuid() == 0:
        raise SystemExit('Run as the audio user with --allow-null-probe; this creates temporary null-output nodes')
    source = Path(__file__).resolve().parents[1] / 'dsp_config.py'
    spec = importlib.util.spec_from_file_location('dsp_config', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    print(json.dumps(probe(module)))


if __name__ == '__main__': main()
