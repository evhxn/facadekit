"""Smoke test for a frozen FacadeKit build: python smoke_test.py <path-to-frozen-executable>."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

PORT = 8155
BASE = f"http://127.0.0.1:{PORT}"


def main(exe: str) -> int:
    server = subprocess.Popen(
        [exe, "serve", "--no-browser", "--sim", "--port", str(PORT)],
        stdin=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            if server.poll() is not None:
                print("server exited early", file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen(f"{BASE}/api/v1/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                print("health check timed out", file=sys.stderr)
                return 1
            time.sleep(1)
        with urllib.request.urlopen(f"{BASE}/", timeout=10) as r:
            html = r.read().decode("utf-8", "replace")
        if '<div id="root">' not in html:
            print('index page lacks <div id="root">', file=sys.stderr)
            return 1
        e2e = subprocess.run([exe, "sim", "e2e"], stdin=subprocess.DEVNULL, timeout=600)
        if e2e.returncode != 0:
            print(f"sim e2e exited {e2e.returncode}", file=sys.stderr)
            return 1
        print("smoke test passed")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
