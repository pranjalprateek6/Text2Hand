"""Renders in worker processes, so several can run at once.

The renderer keeps its settings in module globals, which used to mean one
render at a time for the whole server behind a lock: an 80 page document held
everyone else's one page preview for as long as it took. A worker process has
globals of its own, so each worker renders one document at a time and several
workers render side by side, each keeping its glyph caches warm between jobs.

Progress, ready pages and cancellation cross the process boundary through a
manager queue and event per render. Workers are started with spawn on every
platform: forking a process that is already running threads is unsafe.

A module run as __main__ is re-imported in each spawned worker, so only an
entry point guarded by `if __name__ == "__main__"` (app.py, wsgi servers) may
create a pool. Anything else imports the app with T2H_RENDER_PROCS=0 and
renders in-process, which is what the tests do.
"""
from __future__ import annotations

import multiprocessing
import queue
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_POLL = 0.05                            # seconds between looks at a worker's queue


def _warm_worker() -> None:
    """Load the hand once per worker, before its first job."""
    import render_core
    import text_to_handwriting as t2h
    render_core.apply_options()
    t2h.derive_metrics()
    t2h._load_words()
    t2h.Sheet().close()


def _run(text: str, opts: dict, folder: str, events, stop, base: str | None = None):
    """In a worker: render, reporting through `events`, stopping on `stop`."""
    import render_core
    import text_to_handwriting as t2h

    def on_progress(stage: str, count: int) -> None:
        if stop.is_set():
            raise t2h.RenderStopped()
        events.put(("stage", stage, count))

    def on_ready(n: int) -> None:
        events.put(("ready", n))

    return render_core.render_into(Path(folder), text, opts, on_progress, on_ready,
                                   base=Path(base) if base else None)


class RenderPool:
    def __init__(self, processes: int) -> None:
        ctx = multiprocessing.get_context("spawn")
        extra = {}
        if sys.version_info >= (3, 11):
            # a worker is replaced after this many renders, so whatever its
            # caches and the allocator have accumulated is given back
            extra["max_tasks_per_child"] = 50
        self.processes = processes
        self._executor = ProcessPoolExecutor(max_workers=processes, mp_context=ctx,
                                             initializer=_warm_worker, **extra)
        self._manager = ctx.Manager()

    def warm(self) -> None:
        """Start the workers now, so the first render does not pay for it."""
        for f in [self._executor.submit(_noop) for _ in range(self.processes)]:
            f.result()

    def render(self, text: str, opts: dict, folder: Path, on_progress=None, on_ready=None,
               base: Path | None = None):
        """Render in a worker; return what render_into does.

        `on_progress` runs here, in the calling thread, and may raise to stop
        the render: the worker is told, stops at its next page, and the same
        exception is raised here once it has.
        """
        events = self._manager.Queue()
        stop = self._manager.Event()
        future = self._executor.submit(_run, text, opts, str(folder), events, stop,
                                       str(base) if base else None)
        stopping: BaseException | None = None

        def deliver(event) -> None:
            nonlocal stopping
            kind = event[0]
            try:
                if kind == "stage" and on_progress:
                    on_progress(event[1], event[2])
                elif kind == "ready" and on_ready:
                    on_ready(event[1])
            except BaseException as exc:        # the caller wants it stopped
                if stopping is None:
                    stopping = exc
                    stop.set()

        while not future.done():
            try:
                deliver(events.get(timeout=_POLL))
            except queue.Empty:
                pass
        while True:                             # whatever arrived after the last look
            try:
                deliver(events.get_nowait())
            except queue.Empty:
                break
        try:
            result = future.result()
        except BaseException:
            if stopping is not None:
                raise stopping from None
            raise
        if stopping is not None:
            raise stopping
        return result

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._manager.shutdown()


def _noop() -> None:
    return None
