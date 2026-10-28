"""Fixed stereo EQ graph and bounded, data-only receiver settings."""
import json
import re

FREQUENCIES = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
PRESETS = {
    'flat': (0,) * 10,
    'warm': (2, 1, 0, -1, -1, -1, -2, -2, -2, -2),
    'bass': (3, 2, 1, 0, 0, 0, -1, -1, 0, 0),
    'speech': (-4, -3, -2, -1, 1, 2, 2, 1, -2, -3),
    'soft_treble': (0, 0, 0, 0, 0, -1, -2, -3, -4, -4),
}
INPUT = 'pi_audio.dsp.input'
OUTPUT = 'pi_audio.dsp.output'
UNIT = 'pi-audio-dsp.service'


def validate(value):
    if not isinstance(value, dict) or set(value) != {'enabled', 'target', 'gains', 'preamp'}:
        raise ValueError('Invalid equalizer settings')
    if type(value['enabled']) is not bool:
        raise ValueError('Invalid equalizer switch')
    if not isinstance(value['target'], str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,199}', value['target']) or value['target'].startswith('pi_audio.'):
        raise ValueError('Select a supported physical audio output')
    if not isinstance(value['gains'], list) or len(value['gains']) != len(FREQUENCIES) or any(type(x) is not int or not -6 <= x <= 6 for x in value['gains']):
        raise ValueError('Equalizer bands must be between -6 and +6 dB')
    if type(value['preamp']) is not int or not -12 <= value['preamp'] <= 0:
        raise ValueError('Preamp must be between -12 and 0 dB')
    return {**value, 'gains': value['gains'].copy()}


def headroom(value):
    """Conservative cascade bound: compensate the sum of positive band gains."""
    value = validate(value)
    return sum(max(0, x) for x in value['gains'])


def configuration(value, *, bypass=None):
    value = validate(value)
    attenuation = value['preamp'] - headroom(value)
    nodes = [{'type': 'builtin', 'name': 'preamp', 'label': 'linear',
              'control': {'Mult': 10 ** (attenuation / 20), 'Add': 0}}]
    nodes.extend({'type': 'builtin', 'name': 'band' + str(i), 'label': 'bq_peaking',
                  'control': {'Freq': frequency, 'Q': 1.0, 'Gain': gain}}
                 for i, (frequency, gain) in enumerate(zip(FREQUENCIES, value['gains'])))
    nodes.append({'type': 'builtin', 'name': 'guard', 'label': 'clamp',
                  'control': {'Min': -1.0, 'Max': 1.0}})
    names = [node['name'] for node in nodes]
    graph = {'nodes': nodes,
             'links': [{'output': a + ':Out', 'input': b + ':In'} for a, b in zip(names, names[1:])],
             'inputs': ['preamp:In'], 'outputs': ['guard:Out']}
    common = {'audio.position': ['FL', 'FR'], 'audio.channels': 2, 'audio.rate': 48000,
              'node.link-group': 'pi_audio.dsp', 'state.restore-props': False,
              'state.default-volume': 1.0}
    args = {'node.description': 'Pi Audio Equalizer', 'media.name': 'Pi Audio Equalizer',
            'audio.channels': 2, 'audio.position': ['FL', 'FR'], 'audio.rate': 48000,
            'filter.graph': graph,
            'capture.props': {**common, 'node.name': INPUT, 'media.class': 'Audio/Sink',
                'node.virtual': True, 'priority.session': -10000,
                'filter.smart': True, 'filter.smart.name': 'pi_audio.dsp',
                'filter.smart.targetable': False,
                'filter.smart.disabled': not value['enabled'] if bypass is None else bypass,
                'filter.smart.target': {'node.name': value['target']}},
            'playback.props': {**common, 'node.name': OUTPUT, 'node.passive': True,
                'node.dont-fallback': True, 'node.linger': True,
                'stream.dont-remix': True}}
    config = {'context.properties': {'core.name': 'pi-audio-dsp', 'core.daemon': False, 'log.level': 1},
              'context.spa-libs': {'audio.convert.*': 'audioconvert/libspa-audioconvert',
                                   'support.*': 'support/libspa-support'},
              'context.modules': [
                  {'name': 'libpipewire-module-rt', 'flags': ['ifexists', 'nofail']},
                  {'name': 'libpipewire-module-protocol-native'},
                  {'name': 'libpipewire-module-client-node'},
                  {'name': 'libpipewire-module-adapter'},
                  {'name': 'libpipewire-module-filter-chain', 'args': args}]}
    return json.dumps(config, indent=2) + '\n'
