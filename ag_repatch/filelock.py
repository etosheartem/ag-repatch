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
