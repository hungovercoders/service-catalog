"""The null service - a falsifiability gate for bound feature suites.

Strict binding proves every step is bound; it cannot prove a binding does
anything. A suite is falsifiable only if every scenario fails against a
service that proves nothing, so this module serves the most convincing
wrong answer - 200 {} to every method on every path, no events - runs the
suite against it, and is red unless zero scenarios pass. The response is
deliberately an ordinary 200 rather than an error: against a weird reply
a status-code-only scenario would fail and look honest; against the
plausible empty answer it passes, and gets flagged too.
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _NullHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._respond
        raise AttributeError(name)

    def log_message(self, *args):
        pass


def _serve(port: int) -> ThreadingHTTPServer:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _NullHandler)
    except OSError as exc:
        raise SystemExit(
            f"null run: cannot bind 127.0.0.1:{port} ({exc}) - pass --port"
        ) from exc
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def passed_scenarios(doc) -> tuple[list[str], int]:
    """Scenarios in a cucumber-format results document where every step
    passed - the ones the null service could not make fail. Returns
    (passed descriptions, total scenarios seen)."""
    passed: list[str] = []
    total = 0
    for feature in doc:
        for element in feature.get("elements", []):
            if element.get("type", "scenario") != "scenario":
                continue
            total += 1
            steps = element.get("steps", [])
            if steps and all(
                step.get("result", {}).get("status") == "passed"
                for step in steps
            ):
                name = element.get("name", "").strip() or "(unnamed)"
                passed.append(
                    f"{feature.get('uri', '?')}:{element.get('line', '?')} {name}"
                )
    return passed, total


def check(results: Path) -> int:
    try:
        doc = json.loads(results.read_text())
    except FileNotFoundError:
        print(
            f"null run: {results} was not written - the suite command must"
            " emit cucumber-format JSON there (e.g. --format json:<file>);"
            " a missing result is never a pass"
        )
        return 1
    except json.JSONDecodeError as exc:
        print(f"null run: {results} is not cucumber-format JSON ({exc})")
        return 1
    if not isinstance(doc, list):
        print(f"null run: {results} is not a cucumber-format feature array")
        return 1
    passed, total = passed_scenarios(doc)
    if not total:
        print(f"null run: no scenarios in {results} - wrong results file?")
        return 1
    if passed:
        print(
            f"null run: {len(passed)} of {total} scenarios passed against a"
            " service that answers 200 {} to everything - these bindings"
            " prove nothing:"
        )
        for line in passed:
            print(f"  {line}")
        return 1
    print(
        f"null run: {total} scenarios, 0 passed against the null service"
        " - the suite is falsifiable"
    )
    return 0


def run(port: int, results: str, timeout: int, cmd: list[str]) -> int:
    if not cmd:
        print("null run: no suite command after --")
        return 1
    results_path = Path(results)
    results_path.unlink(missing_ok=True)
    server = _serve(port)
    print(
        f"null service answering 200 {{}} on http://127.0.0.1:{port}"
        " - every scenario must fail"
    )
    try:
        try:
            proc = subprocess.run(cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(
                f"null run: suite exceeded {timeout}s against the null"
                " service - likely an event await with no client timeout"
            )
            return 1
    finally:
        server.shutdown()
        server.server_close()
    print(f"null run: suite exited {proc.returncode} (non-zero expected here)")
    return check(results_path)
