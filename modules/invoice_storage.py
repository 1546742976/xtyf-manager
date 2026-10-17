"""Serialize invoice file/JSON operations across threads and local processes."""
import errno
import os
import threading
import time


class InvoiceFileLock:
    """Reentrant per thread; the OS releases the file lock after process death.

    Keep this lock file in place: deleting it while the service runs would let
    processes lock different inodes. All writers must use the same local path.
    """

    def __init__(self, path):
        self.path = path
        self._reset()
        if hasattr(os, 'register_at_fork'):
            os.register_at_fork(after_in_child=self._after_fork)

    def _reset(self):
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._file = None

    def _after_fork(self):
        if self._file is not None:
            self._file.close()
        self._reset()

    def acquire(self, blocking=True, timeout=None):
        """Acquire the lock; timeout bounds the wait when blocking.

        A deadline makes contention answerable: callers get False back and can
        return a retryable error instead of leaving a worker thread parked
        forever behind a stuck holder.
        """
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        if blocking and deadline is not None:
            acquired = self._thread_lock.acquire(blocking=True, timeout=max(0.0, deadline - time.monotonic()))
        else:
            acquired = self._thread_lock.acquire(blocking=blocking)
        if not acquired:
            return False
        try:
            if self._depth:
                self._depth += 1
                return True
            stream = open(self.path, 'a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    if os.fstat(stream.fileno()).st_size == 0:
                        stream.write(b'\0')
                        stream.flush()
                    while True:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError as exc:
                            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                                raise
                            if not blocking:
                                stream.close()
                                self._thread_lock.release()
                                return False
                            if deadline is not None and time.monotonic() >= deadline:
                                stream.close()
                                self._thread_lock.release()
                                return False
                            time.sleep(0.05)
                else:
                    import fcntl
                    if deadline is None:
                        try:
                            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                        except BlockingIOError:
                            stream.close()
                            self._thread_lock.release()
                            return False
                    else:
                        while True:
                            try:
                                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                break
                            except BlockingIOError:
                                if time.monotonic() >= deadline:
                                    stream.close()
                                    self._thread_lock.release()
                                    return False
                                time.sleep(0.05)
            except BaseException:
                stream.close()
                raise
            self._file = stream
            self._depth = 1
            return True
        except BaseException:
            self._thread_lock.release()
            raise

    def release(self):
        # A nested RLock acquisition checks thread ownership before touching state.
        if not self._thread_lock.acquire(blocking=False):
            raise RuntimeError('Invoice lock is owned by another thread')
        try:
            if not self._depth:
                raise RuntimeError('Invoice lock is not held')
            try:
                self._depth -= 1
                if not self._depth:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            self._file.seek(0)
                            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self._file, fcntl.LOCK_UN)
                    finally:
                        self._file.close()
                        self._file = None
            finally:
                self._thread_lock.release()
        finally:
            self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
