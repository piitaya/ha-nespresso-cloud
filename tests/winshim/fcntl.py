"""Windows stand-in for fcntl.

Home Assistant imports fcntl only to lock its config directory when the real
server starts, which tests never do. scripts/test.py puts this directory on
the path on Windows only.
"""

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(fd, operation):
    pass
