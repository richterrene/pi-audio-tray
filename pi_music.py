#!/usr/bin/python3 -I
"""Bounded, unprivileged MPD bridge. Receives one JSON request on stdin."""
import json
import os
from pathlib import PurePosixPath
import socket
import sys
import time

LIMIT = 100
MAX_RESPONSE = 4 * 1024 * 1024


def quote(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid MPD argument')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def music_path(value):
    quote(value)
    if value.startswith('/') or ':' in value or '..' in PurePosixPath(value).parts or '\\' in value:
        raise ValueError('Only relative library paths are allowed')
    return value


class MPD:
    def __enter__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect('/run/pi-audio-mpd/socket')
        self.stream = self.sock.makefile('rb')
        if not self.stream.readline(256).startswith(b'OK MPD '):
            self.__exit__(None, None, None)
            raise RuntimeError('Invalid MPD greeting')
        return self

    def __exit__(self, *args):
        self.stream.close()
        self.sock.close()

    def command(self, name, *args):
        # Names are literals selected below; no caller-controlled protocol commands.
        self.sock.sendall((name + ''.join(' ' + quote(str(a)) for a in args) + '\n').encode())
        rows, total, deadline = [], 0, time.monotonic() + 10
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('MPD response timed out')
            raw = self.stream.readline(16385)
            total += len(raw)
            if not raw or len(raw) > 16384 or total > MAX_RESPONSE:
                raise RuntimeError('MPD response exceeded its limit or disconnected')
            line = raw.decode('utf-8', errors='replace').rstrip('\n')
            if line == 'OK':
                return rows
            if line.startswith('ACK '):
                raise RuntimeError('MPD rejected this operation')
            key, sep, value = line.partition(': ')
            if not sep:
                raise RuntimeError('Invalid MPD response')
            rows.append((key, value))


def records(rows):
    result = []
    for key, value in rows:
        if key in ('file', 'directory', 'playlist'):
            result.append({key: value})
        elif result and key in ('Title', 'Artist', 'Album', 'Track', 'duration', 'Time', 'Id', 'Pos'):
            result[-1][key] = value[:1024]
    return result


def dispatch(mpd, request):
    if not isinstance(request, dict) or set(request) - {'action', 'path', 'offset', 'value'}:
        raise ValueError('Invalid music request')
    action = request.get('action')
    offset = request.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('Invalid page')
    if action == 'browse':
        path = music_path(request.get('path', ''))
        items = records(mpd.command('lsinfo', path))
        items = [x for x in items if 'playlist' not in x and x.get('directory') != '_Export']
        items.sort(key=lambda x: ('directory' not in x, (x.get('directory') or x.get('file', '')).casefold()))
        return {'items': items[offset:offset + LIMIT], 'more': len(items) > offset + LIMIT}
    if action == 'queue':
        return {'items': records(mpd.command('playlistinfo', f'{offset}:{offset + LIMIT}')),
                'more': int(dict(mpd.command('status')).get('playlistlength', '0')) > offset + LIMIT}
    if action == 'status':
        status = dict(mpd.command('status'))
        current = records(mpd.command('currentsong'))
        return {'status': {k: status[k] for k in ('state', 'volume', 'elapsed', 'duration', 'playlistlength') if k in status},
                'current': current[0] if current else {}}
    if action in ('add', 'play_file'):
        path = music_path(request.get('path', ''))
        if not path:
            raise ValueError('Select a track or folder')
        if action == 'play_file':
            song_id = dict(mpd.command('addid', path))['Id']
            mpd.command('playid', song_id)
        else:
            mpd.command('add', path)
    elif action in ('play', 'pause', 'stop', 'next', 'previous'):
        mpd.command(action, *(['1'] if action == 'pause' else []))
    elif action in ('play_id', 'volume'):
        value = request.get('value')
        if type(value) is not int or not 0 <= value <= (100 if action == 'volume' else 2**31 - 1):
            raise ValueError('Invalid numeric value')
        mpd.command('setvol' if action == 'volume' else 'playid', value)
    elif action == 'update':
        mpd.command('update')
    elif action == 'clear':
        mpd.command('clear')
    else:
        raise ValueError('Unknown music operation')
    return {'ok': True}


def main():
    if os.geteuid() == 0:
        raise RuntimeError('Run music control as the audio user, never root')
    raw = sys.stdin.buffer.readline(8193)
    if len(raw) > 8192 or not raw.endswith(b'\n'):
        raise ValueError('Music request exceeded its limit')
    request = json.loads(raw)
    with MPD() as mpd:
        result = dispatch(mpd, request)
    data = json.dumps(result, ensure_ascii=True)
    if len(data) > 120 * 1024:
        raise RuntimeError('Music page too large')
    print(data)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)[:256]}))
        sys.exit(1)
