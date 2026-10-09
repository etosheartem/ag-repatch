"""Non-blocking file lock shared by the GUI, CLI and privileged helper."""
import os


def lock_file(file):
    if os.name == 'nt':
        import msvcrt
        file.seek(0)
        # All writers lock byte zero before reading or modifying the file.
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        file.seek(0)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def open_for_write(path):
    """On Windows deny competing write/delete opens, including truncation."""
    if os.name != 'nt':
        return open(path, 'r+b')
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(os.fspath(path), 0x80000000 | 0x40000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except Exception:
        close(handle)
        raise
    try:
        return os.fdopen(fd, 'r+b')
    except Exception:
        os.close(fd)
        raise
