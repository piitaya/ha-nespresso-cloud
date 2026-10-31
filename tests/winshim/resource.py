"""Windows stand-in for resource.

Home Assistant imports resource only to raise the open-file limit when the
real server starts, which tests never do. scripts/test.py puts this directory
on the path on Windows only.
"""

RLIMIT_NOFILE = 7

_limits = {RLIMIT_NOFILE: (8192, 8192)}


def getrlimit(which):
    return _limits.get(which, (8192, 8192))


def setrlimit(which, limits):
    _limits[which] = tuple(limits)
