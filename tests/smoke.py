"""Drive a running Text2Hand server the way the studio does.

    python tests/smoke.py http://127.0.0.1:8000

Waits for /healthz, loads both pages, renders a short Markdown document,
downloads the PDF, and exits non-zero on the first thing that is wrong. Only
the standard library, so it runs against a container from a bare runner.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def fail(msg):
    print("FAIL", msg)
    sys.exit(1)


deadline = time.time() + 90
while True:
    try:
        status, _, body = get("/healthz", timeout=3)
        if status == 200 and json.loads(body).get("ok"):
            break
    except (urllib.error.URLError, ConnectionError, OSError):
        pass
    if time.time() > deadline:
        fail("server never became healthy")
    time.sleep(1)
print("ok   healthz")

for path in ("/", "/studio"):
    status, ctype, body = get(path, timeout=30)
    if status != 200 or b"Text2Hand" not in body:
        fail(f"{path} -> {status}")
    print("ok  ", path)

status, started = post("/api/render", {
    "text": "# Smoke test\n\n- It’s a café list\n- with x² in it\n\nA paragraph of prose.",
    "markdown": True,
})
if status != 202:
    fail(f"render not accepted: {status} {started}")
jid = started["job"]
deadline = time.time() + 120
while True:
    _, _, body = get(f"/api/job/{jid}")
    job = json.loads(body)
    if job["state"] != "running":
        break
    if time.time() > deadline:
        fail("render never finished")
    time.sleep(0.5)
if job["state"] != "done":
    fail(f"render ended {job['state']}: {job.get('error')}")
if job.get("missing"):
    fail(f"characters skipped: {job['missing']}")
print(f"ok   render, {job['pages']} page(s)")

status, ctype, body = get(f"/download/{job['id']}/handwriting.pdf", timeout=30)
if status != 200 or not body.startswith(b"%PDF-"):
    fail(f"pdf download -> {status} {ctype}")
print(f"ok   pdf, {len(body):,} bytes")

status, ctype, body = get(f"/preview/{job['id']}/1.jpg")
if status != 200 or "jpeg" not in ctype:
    fail(f"preview -> {status} {ctype}")
print("ok   preview")

# two at once: renders run in worker processes, and both must finish
import threading

results = {}


def render_one(key):
    _, started = post("/api/render", {"text": "Side by side. " * 300, "ink": key})
    end = time.time() + 120
    while time.time() < end:
        _, _, raw = get(f"/api/job/{started['job']}")
        state = json.loads(raw)
        if state["state"] != "running":
            results[key] = state["state"]
            return
        time.sleep(0.5)
    results[key] = "timeout"


pair = [threading.Thread(target=render_one, args=(k,)) for k in ("red", "blue")]
for t in pair:
    t.start()
for t in pair:
    t.join()
if results != {"red": "done", "blue": "done"}:
    fail(f"concurrent renders: {results}")
print("ok   two renders at once")
print("smoke test passed")
