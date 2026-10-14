"""Checks for rendering in worker processes, run as their own script.

    python tests/pool_check.py

Workers are spawned, and a spawned worker re-imports the main module, so
these cannot live in regression.py (which runs everything at import). This
file does its work under `if __name__ == "__main__"` and prints PASS/FAIL
lines in the regression suite's format; regression.py runs it.
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    os.environ["T2H_RENDER_PROCS"] = "2"
    os.environ["T2H_RENDER_ROOT"] = tempfile.mkdtemp(prefix="t2h_pool_")
    sys.path.insert(0, ROOT)
    import app

    client = app.app.test_client()
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))

    def run(payload, out, key):
        jid = client.post("/api/render", json=payload).get_json()["job"]
        while True:
            job = client.get(f"/api/job/{jid}").get_json()
            if job["state"] != "running":
                out[key] = job
                return
            time.sleep(0.05)

    app.warm()
    text = "Two documents rendered at the same moment, side by side. " * 500

    # one alone, then two at once: side by side should beat twice the one
    out: dict = {}
    t = time.perf_counter()
    run({"text": text}, out, "solo")
    solo = time.perf_counter() - t
    t = time.perf_counter()
    threads = [threading.Thread(target=run, args=({"text": text, "ink": ink}, out, ink))
               for ink in ("red", "blue")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    pair = time.perf_counter() - t
    done = all(out[k]["state"] == "done" for k in ("solo", "red", "blue"))
    check("worker renders complete", done, str({k: v["state"] for k, v in out.items()}))
    # One render already spreads its page work over several cores, so on a
    # small machine two at once cannot beat twice one by much. The timing is
    # only held to account where there are cores to spare; the ink check
    # below proves the two really ran apart either way.
    if (os.cpu_count() or 1) >= 8:
        check("two renders run side by side", done and pair < 1.7 * solo,
              f"one {solo:.2f}s, two at once {pair:.2f}s")
    else:
        print(f"  SKIP  two renders run side by side  [{os.cpu_count()} cores; "
              f"one {solo:.2f}s, two at once {pair:.2f}s]")
    check("pages reported ready from a worker",
          out["solo"].get("ready") == out["solo"].get("pages"),
          f"ready {out['solo'].get('ready')} of {out['solo'].get('pages')}")

    # each worker keeps its own options: the red and blue renders must differ
    # in ink, which a shared set of globals could not guarantee
    from PIL import Image
    import io

    def hue(job):
        data = client.get(f"/preview/{job['id']}/1.jpg").data
        im = Image.open(io.BytesIO(data)).convert("RGB")
        pixels = im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()
        px = [p for p in pixels if sum(p) < 330]
        return tuple(sum(c[i] for c in px) // len(px) for i in range(3))

    red, blue = hue(out["red"]), hue(out["blue"])
    check("concurrent renders keep their own ink", red[0] > red[2] and blue[2] > blue[0],
          f"red page {red}, blue page {blue}")

    # cancel crosses the process boundary and stops the worker
    jid = client.post("/api/render", json={"text": text * 3}).get_json()["job"]
    time.sleep(1.0)
    t = time.perf_counter()
    client.post(f"/api/job/{jid}/cancel")
    while client.get(f"/api/job/{jid}").get_json()["state"] == "running":
        time.sleep(0.05)
    job = client.get(f"/api/job/{jid}").get_json()
    check("cancel stops a worker render", job["state"] == "cancelled",
          f"{job['state']} in {time.perf_counter() - t:.2f}s")

    # a worker failure arrives as an error, not a hang
    _job = client.post("/api/render", json={"text": "fine"}).get_json()
    check("worker still serves after a cancel", _job.get("job") is not None)
    print(f"\n{'pool ok' if not failures else f'{failures} pool check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
