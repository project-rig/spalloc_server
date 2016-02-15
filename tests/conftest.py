import pytest

import threading

from collections import deque


@pytest.fixture
def MockABC(monkeypatch):  # pragma: no cover
    """This test fixture mocks-out the AsyncBMPController class with a shim
    which immediately calls the supplied callbacks via another thread (to
    ensure no inter-thread calling issues).
    """
    class MockAsyncBMPController():

        running_theads = 0

        num_created = 0

        def __init__(self, hostname, on_thread_start=None):
            MockAsyncBMPController.num_created += 1

            self.hostname = hostname
            self.on_thread_start = on_thread_start

            # A lock which must be held when handling a request (may be used to
            # simulate the machine taking some amount of time to respond)
            self.handler_lock = threading.Lock()

            self._lock = threading.RLock()
            self._event = threading.Event()
            self._request_queue = deque()
            self._stop = False

            self._thread = threading.Thread(
                target=self._run,
                name="MockABC Thread {}".format(self.hostname))
            MockAsyncBMPController.running_theads += 1
            self._thread.start()

            # Protected only by the GIL...
            self.set_power_calls = []
            self.set_link_enable_calls = []

        def _run(self):
            """Background thread, just take things from the request queue and
            call them.
            """
            try:
                if self.on_thread_start is not None:
                    self.on_thread_start()
                while not self._stop:
                    self._event.wait()
                    with self.handler_lock:
                        while True:
                            # Run until no more requests exist
                            with self:
                                if not self._request_queue:
                                    self._event.clear()
                                    break
                                else:
                                    cb = self._request_queue.popleft()

                            # Run callback (while not holding any locks)
                            cb()

                # For purposes of testing, do not allow this thread to exit
                # while the handler_lock is held. This ensures we can mock this
                # thread not exiting while handler_lock exists.
                with self.handler_lock:
                    pass
            finally:
                MockAsyncBMPController.running_theads -= 1

        def __enter__(self):
            self._lock.acquire()

        def __exit__(self, type=None, value=None, traceback=None):
            self._lock.release()

        def set_power(self, board, state, on_done):
            self.set_power_calls.append((board, state, on_done))
            with self._lock:
                assert not self._stop
                self._request_queue.append(on_done)
                self._event.set()

        def set_link_enable(self, board, link, enable, on_done):
            self.set_link_enable_calls.append((board, link, enable, on_done))
            with self._lock:

                assert not self._stop
                self._request_queue.append(on_done)
                self._event.set()

        def stop(self):
            with self._lock:
                self._stop = True
                self._event.set()

        def join(self):
            self._thread.join()

    import spalloc_server.controller
    monkeypatch.setattr(spalloc_server.controller, "AsyncBMPController",
                        MockAsyncBMPController)

    return MockAsyncBMPController
