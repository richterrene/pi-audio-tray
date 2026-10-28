"""Fixed stereo EQ graph and bounded, data-only receiver settings."""
import json
import math
from functools import lru_cache
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


SAMPLE_RATE = 48000
# Partition the whole Nyquist interval. Each peaking section is monotonic on
# either side of its centre, so interval maxima bound the continuous response;
# this is not just a sampled-peak estimate. Cut sections never increase gain.
_RESPONSE_POINTS = tuple(sorted({0.0, SAMPLE_RATE / 2, *FREQUENCIES,
    *(20 * 2 ** (i / 48) for i in range(500) if 20 * 2 ** (i / 48) < SAMPLE_RATE / 2)}))


@lru_cache(maxsize=256)
def _headroom(gains):
    positive = sum(max(0, gain) for gain in gains)
    if positive == 0:
        return 0
    upper = [0.0] * (len(_RESPONSE_POINTS) - 1)
    for centre, gain in zip(FREQUENCIES, gains):
        if gain <= 0:
            continue
        w0 = 2 * math.pi * centre / SAMPLE_RATE
        alpha, cosine, amplitude = math.sin(w0) / 2, math.cos(w0), 10 ** (gain / 40)
        values = []
        for frequency in _RESPONSE_POINTS:
            w = 2 * math.pi * frequency / SAMPLE_RATE
            delta, sine = math.cos(w) - cosine, math.sin(w)
            numerator = delta * delta + (alpha * amplitude * sine) ** 2
            denominator = delta * delta + (alpha / amplitude * sine) ** 2
            values.append(10 * math.log10(numerator / denominator))
        for i, (low, high) in enumerate(zip(_RESPONSE_POINTS, _RESPONSE_POINTS[1:])):
            upper[i] += max(values[i], values[i + 1], gain if low <= centre <= high else 0.0)
    # Round upwards with a small implementation/roundoff margin, never attenuate
    # more than the previous sum-of-boosts bound. The sample clamp remains.
    return min(positive, math.ceil(max(upper) + 0.1))


def headroom(value):
    """Continuous interval upper bound for the fixed 48 kHz, Q=1 cascade."""
    return _headroom(tuple(validate(value)['gains']))


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
    common = {'audio.position': ['FL', 'FR'], 'audio.channels': 2, 'audio.rate': SAMPLE_RATE,
              'node.link-group': 'pi_audio.dsp', 'state.restore-props': False,
              'state.default-volume': 1.0}
    args = {'node.description': 'Pi Audio Equalizer', 'media.name': 'Pi Audio Equalizer',
            'audio.channels': 2, 'audio.position': ['FL', 'FR'], 'audio.rate': SAMPLE_RATE,
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
