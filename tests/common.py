import pytest

import threading

from collections import deque

from spinn_partition.machine import Machine
from spinn_partition.controller import Controller


def simple_machine(name, width=1, height=2, tags=set(["default"]),
                   dead_boards=None, dead_links=None, ip_prefix=""):
    """Construct a simple machine with nothing broken etc."""
    return Machine(name=name, tags=tags, width=width, height=height,
                   dead_boards=dead_boards or set(),
                   dead_links=dead_links or set(),
                   board_locations={(x, y, z): (x, y, z)
                                    for x in range(width)
                                    for y in range(height)
                                    for z in range(3)},
                   bmp_ips={(x, y): "{}10.1.{}.{}".format(ip_prefix, x, y)
                            for x in range(width)
                            for y in range(height)},
                   spinnaker_ips={(x, y, z): "{}11.{}.{}.{}".format(
                                      ip_prefix, x, y, z)
                                  for x in range(width)
                                  for y in range(height)
                                  for z in range(3)})


@pytest.fixture
def MockABC(monkeypatch):
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
            
            # Reports if the object was locked by its context manager
            self.locked = 0
            
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
                        with self._lock:
                            while self._request_queue:
                                self._request_queue.popleft()()
                            self._event.clear()
            finally:
                MockAsyncBMPController.running_theads -= 1
        
        def __enter__(self):
            self._lock.acquire()
            self.locked += 1
        
        def __exit__(self, type=None, value=None, traceback=None):
            self.locked -= 1
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
    
    import spinn_partition.controller
    monkeypatch.setattr(spinn_partition.controller, "AsyncBMPController",
                        MockAsyncBMPController)
    
    return MockAsyncBMPController

