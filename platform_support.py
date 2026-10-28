"""Private local storage, single-instance locking and opt-in login autostart."""
import errno
import functools
import os
import stat
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile


AUTOSTART_LABEL = 'org.piaudiotray.desktop'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'Pi Audio Tray'


def autostart_path():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/LaunchAgents' / (AUTOSTART_LABEL + '.plist')
    if sys.platform.startswith('linux'):
        root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        if not root.is_absolute():
            raise ValueError('XDG_CONFIG_HOME must be an absolute path')
        return root / 'autostart/pi-audio-tray.desktop'
    raise OSError('Autostart is unavailable on this platform')


def autostart_enabled():
    """Read OS registration rather than a potentially stale app preference."""
    if sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, kind = winreg.QueryValueEx(key, RUN_NAME)
                return kind == winreg.REG_SZ and bool(value)
        except FileNotFoundError:
            return False
    path = autostart_path()
    reject_link(path, missing_ok=True)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
            raise ValueError('Autostart entry must be a small regular file')
        data = path.read_bytes()
    except FileNotFoundError:
        return False
    if sys.platform == 'darwin':
        config = plistlib.loads(data)
        if not isinstance(config, dict):
            raise ValueError('Invalid autostart property list')
        return config.get('Label') == AUTOSTART_LABEL and bool(config.get('RunAtLoad'))
    lines = data.decode('utf-8').splitlines()
    return '[Desktop Entry]' in lines and 'Hidden=true' not in lines and any(line.startswith('Exec=') for line in lines)


def autostart_command():
    """Use persistent external executable paths, never a mounted AppImage path."""
    if sys.platform.startswith('linux') and os.environ.get('APPIMAGE'):
        command = [os.environ['APPIMAGE']]
    elif getattr(sys, 'frozen', False):
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().with_name('app.py'))]
    for argument in command:
        if not Path(argument).is_absolute() or any(c in argument for c in '\r\n\0'):
            raise ValueError('Autostart requires absolute executable paths without control characters')
        if not Path(argument).is_file():
            raise ValueError('Move the application to a permanent location before enabling autostart')
    if sys.platform == 'darwin' and any('/AppTranslocation/' in arg or arg.startswith('/Volumes/') for arg in command):
        raise ValueError('Move the app into Applications and reopen it before enabling autostart')
    return command + ['--autostart']


def _desktop_argument(argument):
    # Desktop Entry escaping has two layers: string-value and Exec quoting.
    argument = argument.replace('%', '%%')
    argument = ''.join('\\' + c if c in '\\"`$' else c for c in argument)
    return ('"' + argument + '"').replace('\\', '\\\\')


def set_autostart(enabled):
    """Change only this account's app entry. Never launch, stop or elevate."""
    command = autostart_command() if enabled else None
    if sys.platform == 'win32':
        import winreg
        if enabled:
            value = subprocess.list2cmdline(command)
            if len(value) > 260:
                raise ValueError('Application path exceeds Windows login command limit; move it to a shorter path')
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, value)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
        return
    path = autostart_path()
    reject_link(path.parent, missing_ok=True)
    reject_link(path, missing_ok=True)
    if not enabled:
        path.unlink(missing_ok=True)
        return
    if sys.platform == 'darwin':
        contents = plistlib.dumps({'Label': AUTOSTART_LABEL, 'ProgramArguments': command,
                                  'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua'})
    else:
        contents = ('[Desktop Entry]\nType=Application\nName=Pi Audio Tray\n'
                    'Comment=Monitor and control your Pi audio receiver\n'
                    'Exec=' + ' '.join(_desktop_argument(arg) for arg in command) + '\n'
                    'Icon=pi-audio-tray\nTerminal=false\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pi-audio-autostart-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(contents)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reject_link(path, missing_ok=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise OSError('Refusing a symbolic link or Windows reparse point')


@functools.lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes as w
    from types import SimpleNamespace
    # Never search the application directory/PATH for these system DLLs.
    adv = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    ptr = ctypes.c_void_p
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [ptr]
    kernel.LocalFree.restype = ptr
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ptr, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD,
        ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(w.DWORD)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    adv.GetSecurityDescriptorDacl.argtypes = [ptr, ctypes.POINTER(w.BOOL), ctypes.POINTER(ptr), ctypes.POINTER(w.BOOL)]
    adv.GetSecurityDescriptorDacl.restype = w.BOOL
    adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ptr, ptr, ptr, ptr]
    adv.SetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ptr, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.POINTER(w.DWORD)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
    adv.GetAclInformation.argtypes = [ptr, ptr, w.DWORD, ctypes.c_int]
    adv.GetAclInformation.restype = w.BOOL
    adv.GetAce.argtypes = [ptr, w.DWORD, ctypes.POINTER(ptr)]
    adv.GetAce.restype = w.BOOL
    return SimpleNamespace(c=ctypes, w=w, adv=adv, kernel=kernel)


def _sid_string(sid):
    api = _windows_api()
    value = api.w.LPWSTR()
    if not api.adv.ConvertSidToStringSidW(sid, api.c.byref(value)):
        raise api.c.WinError(api.c.get_last_error())
    try:
        return value.value
    finally:
        api.kernel.LocalFree(api.c.cast(value, api.c.c_void_p))


@functools.lru_cache(maxsize=1)
def windows_user_sid():
    api = _windows_api()
    token = api.w.HANDLE()
    if not api.adv.OpenProcessToken(api.kernel.GetCurrentProcess(), 0x8, api.c.byref(token)):
        raise api.c.WinError(api.c.get_last_error())
    try:
        size = api.w.DWORD()
        api.adv.GetTokenInformation(token, 1, None, 0, api.c.byref(size))
        if not size.value:
            raise api.c.WinError(api.c.get_last_error())
        buffer = api.c.create_string_buffer(size.value)
        if not api.adv.GetTokenInformation(token, 1, buffer, size, api.c.byref(size)):
            raise api.c.WinError(api.c.get_last_error())
        # TOKEN_USER begins with SID_AND_ATTRIBUTES; its first field is PSID.
        return _sid_string(api.c.cast(buffer, api.c.POINTER(api.c.c_void_p)).contents.value)
    finally:
        api.kernel.CloseHandle(token)


def windows_descriptor(path):
    """Return DACL SDDL for regression checks; no credentials are read."""
    api = _windows_api()
    descriptor = api.c.c_void_p()
    code = api.adv.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, api.c.byref(descriptor))
    if code:
        raise api.c.WinError(code)
    value = api.w.LPWSTR()
    try:
        if not api.adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, api.c.byref(value), None):
            raise api.c.WinError(api.c.get_last_error())
        return value.value
    finally:
        if value:
            api.kernel.LocalFree(api.c.cast(value, api.c.c_void_p))
        api.kernel.LocalFree(descriptor)


def windows_allowed_sids(path):
    """Inspect actual ACE trustees; SDDL can abbreviate a SID as LA/SY/etc."""
    api = _windows_api()
    descriptor, dacl = api.c.c_void_p(), api.c.c_void_p()
    code = api.adv.GetNamedSecurityInfoW(str(path), 1, 4, None, None, api.c.byref(dacl), None, api.c.byref(descriptor))
    if code:
        raise api.c.WinError(code)
    try:
        class AclSize(api.c.Structure):
            _fields_ = [('count', api.w.DWORD), ('used', api.w.DWORD), ('free', api.w.DWORD)]
        size = AclSize()
        if not dacl or not api.adv.GetAclInformation(dacl, api.c.byref(size), api.c.sizeof(size), 2):
            raise PermissionError('Could not inspect Windows DACL')
        trustees = []
        for index in range(size.count):
            ace = api.c.c_void_p()
            if not api.adv.GetAce(dacl, index, api.c.byref(ace)):
                raise api.c.WinError(api.c.get_last_error())
            if api.c.c_ubyte.from_address(ace.value).value != 0 or api.c.c_ushort.from_address(ace.value + 2).value < 16:
                raise PermissionError('Unexpected access-control entry')
            trustees.append(_sid_string(ace.value + 8))
        return trustees
    finally:
        api.kernel.LocalFree(descriptor)


def windows_protect(path, directory=False):
    reject_link(path)
    api = _windows_api()
    owner, original = api.c.c_void_p(), api.c.c_void_p()
    code = api.adv.GetNamedSecurityInfoW(str(path), 1, 1, api.c.byref(owner), None, None, None, api.c.byref(original))
    if code:
        raise api.c.WinError(code)
    try:
        if _sid_string(owner) not in (windows_user_sid(), 'S-1-5-32-544'):
            raise PermissionError('Local settings belong to another Windows account')
    finally:
        api.kernel.LocalFree(original)
    flags = 'OICI' if directory else ''
    sddl = f'D:P(A;{flags};FA;;;{windows_user_sid()})(A;{flags};FA;;;SY)'
    descriptor = api.c.c_void_p()
    if not api.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, api.c.byref(descriptor), None):
        raise api.c.WinError(api.c.get_last_error())
    try:
        present, defaulted, dacl = api.w.BOOL(), api.w.BOOL(), api.c.c_void_p()
        if not api.adv.GetSecurityDescriptorDacl(descriptor, api.c.byref(present), api.c.byref(dacl), api.c.byref(defaulted)) or not present or not dacl:
            raise PermissionError('Could not construct a private Windows DACL')
        code = api.adv.SetNamedSecurityInfoW(str(path), 1, 4 | 0x80000000, None, None, dacl, None)
        if code:
            raise api.c.WinError(code)
    finally:
        api.kernel.LocalFree(descriptor)


def secure_directory(path):
    if os.name == 'nt':
        windows_protect(path, directory=True)
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError('Configuration directory belongs to another user')
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def private_file(fd, path):
    if os.name == 'nt':
        windows_protect(path)
    else:
        os.fchmod(fd, 0o600)


def open_instance_lock(path):
    reject_link(path, missing_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_uid != os.getuid()):
            raise ValueError('Unsafe instance lock file')
        private_file(fd, path)
        return os.fdopen(fd, 'r+')
    except Exception:
        os.close(fd)
        raise


def lock_instance(file):
    if os.name == 'nt':
        import msvcrt
        if os.fstat(file.fileno()).st_size == 0:
            file.write('0')
            file.flush()
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError('Another Pi Audio Tray instance holds the lock') from None
            raise
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
