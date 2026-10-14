"""Production entry point: `gunicorn wsgi:app` (or `waitress-serve wsgi:app`).

Run it as ONE process with several threads, for example

    gunicorn wsgi:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:8000

Jobs and their progress live in that process's memory, and a render holds the
renderer's lock, so a second worker process would neither see the first one's
jobs nor share its warm caches. Threads are what serve the polling.
"""
import logging
import os
import threading

from app import RENDER_ROOT, app, warm

logging.basicConfig(level=os.environ.get("T2H_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
RENDER_ROOT.mkdir(parents=True, exist_ok=True)
threading.Thread(target=warm, daemon=True, name="warm").start()

__all__ = ["app"]
