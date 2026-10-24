"""A thread-safe view of the data package (owner: Amine).

The kit's DataPackage keeps one SQLite connection; the Live Final plays up to 10 cases at once in threads, and
concurrent use of one connection corrupts results ("bad parameter or other API misuse", rows from the wrong query).
Every call goes through one lock; the lazy ID index is built once up front. Queries take milliseconds, so the
lock costs nothing that matters next to a supplier's reply.
"""

import threading


class ThreadSafePackage:
    def __init__(self, package):
        self._package = package
        self._lock = threading.RLock()
        with self._lock:
            package.lookup("")  # build the ID index once, before threads share it

    def query(self, sql, params=(), limit=None):
        with self._lock:
            return self._package.query(sql, params) if limit is None else self._package.query(sql, params, limit)

    def lookup(self, ref):
        with self._lock:
            return self._package.lookup(ref)

    def document(self, ref):
        with self._lock:
            return self._package.document(ref)

    def ids(self, prefix):
        with self._lock:
            return self._package.ids(prefix)

    def __getattr__(self, name):  # facts, root, release, ...: read-only attributes
        return getattr(self._package, name)


def thread_safe(package):
    """Wrap a package once; None stays None."""
    if package is None or isinstance(package, ThreadSafePackage):
        return package
    return ThreadSafePackage(package)
