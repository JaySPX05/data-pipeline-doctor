"""MemoryStore.close(): every Hindsight client is closed on the thread that created it.

A client's HTTP session lives on its thread's event loop, so closing it from another thread
fails (and aiohttp then prints "Unclosed client session" at exit). Offline: fake clients.
"""
import threading

from app.config import Settings
from app.memory.hindsight_store import MemoryStore


class RecordingClient:
    def __init__(self, log):
        self.made_on = threading.get_ident()
        self.closed_on = None
        log.append(self)

    def close(self):
        self.closed_on = threading.get_ident()


def make_store(log):
    cfg = Settings(memory_enabled=True, hindsight_url="http://fake")
    return MemoryStore(cfg, client_factory=lambda timeout: RecordingClient(log))


def test_close_runs_on_each_owning_thread_including_pool_threads():
    log = []
    store = make_store(log)
    store._thread_client("write_client", 1.0)                       # main thread
    pool = store._executor()
    gate = threading.Barrier(3)

    def use_client():
        gate.wait(timeout=5)  # hold 3 pool threads at once, so 3 different threads make clients
        return store._thread_client("recall_client", 1.0)

    for f in [pool.submit(use_client) for _ in range(3)]:
        f.result(timeout=5)

    assert len(log) == 4
    store.close()
    assert all(c.closed_on == c.made_on for c in log)
    assert len({c.made_on for c in log}) == 4


def test_store_still_works_after_close():
    log = []
    store = make_store(log)
    first = store._thread_client("write_client", 1.0)
    store.close()
    second = store._thread_client("write_client", 1.0)
    assert second is not first and first.closed_on is not None
    assert store._executor() is not None  # a fresh pool is made on demand


def test_close_all_closes_every_store_and_ignores_close_errors():
    class Broken(RecordingClient):
        def close(self):
            raise RuntimeError("boom")

    log = []
    good = make_store(log)
    bad = MemoryStore(Settings(memory_enabled=True, hindsight_url="http://fake"),
                      client_factory=lambda timeout: Broken(log))
    good._thread_client("write_client", 1.0)
    bad._thread_client("write_client", 1.0)
    MemoryStore.close_all()  # must not raise
    assert log[0].closed_on is not None
