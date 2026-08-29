"""Microcks mock-stack orchestration, driven entirely by the specs.

Every service's specs and examples are loaded into Microcks; contract
tests and smoke tests are derived from the manifests, the specs and the
example files - nothing here knows any service by name.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from importlib import resources
from pathlib import Path

import yaml


def compose_file(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    local = Path("mocks/docker-compose.yml")
    if local.is_file():
        return local
    # Fall back to the compose file shipped with the kit, materialized where
    # both `up` and `down` can find it again.
    target = Path(".sysspec/docker-compose.yml")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(
        (resources.files("sysspec") / "data/docker-compose.yml").read_bytes()
    )
    return target


def dc(file: Path, *args: str) -> None:
    subprocess.run(["docker", "compose", "-f", str(file), *args], check=True)


def http(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def upload(microcks_url: str, path: Path, main: bool) -> None:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/x-yaml\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    status, out = http(
        "POST",
        f"{microcks_url}/api/artifact/upload?mainArtifact={'true' if main else 'false'}",
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=30,
    )
    if status >= 300:
        raise SystemExit(f"upload failed ({status}) for {path}: {out.decode()[:200]}")
    print(f"loaded {path}")


def service_dirs(specs_dir: str, only: str | None) -> list[Path]:
    dirs = [
        p.parent
        for p in sorted(Path(specs_dir).glob("*/service.yaml"))
        if not only or p.parent.name == only
    ]
    if not dirs:
        raise SystemExit(f"no services matching '{only or '*'}' under {specs_dir}/")
    return dirs


def spec_docs(service_dir: Path, kind: str) -> list[tuple[Path, dict]]:
    return [
        (p, yaml.safe_load(p.read_text()) or {})
        for p in sorted((service_dir / kind).glob("*.y*ml"))
    ]


def info(doc: dict) -> tuple[str, str]:
    i = doc.get("info") or {}
    return i["title"], str(i["version"])


def send_operations(doc: dict) -> list[str]:
    return [
        name
        for name, op in (doc.get("operations") or {}).items()
        if (op or {}).get("action") == "send"
    ]


def up(compose: Path) -> int:
    dc(compose, "up", "-d", "--wait")
    return 0


def down(compose: Path) -> int:
    dc(compose, "down")
    return 0


def load(
    only: str | None,
    specs_dir: str,
    mocks_dir: str,
    microcks_url: str,
    minion_url: str,
    compose: Path,
) -> int:
    up(compose)
    for d in service_dirs(specs_dir, only):
        for kind in ("asyncapi", "openapi"):
            for spec, _ in spec_docs(d, kind):
                upload(microcks_url, spec, main=True)
        for examples in sorted(Path(mocks_dir).glob(f"{d.name}.*.examples.yaml")):
            upload(microcks_url, examples, main=False)
    dc(compose, "restart", "async-minion")
    for i in range(30):
        try:
            http("GET", f"{minion_url}/health", timeout=3)
            print(f"async-minion http up (after {i + 1} checks)")
            return 0
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2)
    raise SystemExit("async-minion not responding after 60s")


def run_test(
    microcks_url: str,
    service_id: str,
    runner: str,
    endpoint: str,
    timeout_ms: int,
    operation: str | None,
) -> None:
    label = f"[{runner}{f' / {operation}' if operation else ''}]"
    print(f"contract test: {service_id} {label} -> {endpoint}")
    payload: dict = {
        "serviceId": service_id,
        "testEndpoint": endpoint,
        "runnerType": runner,
        "timeout": timeout_ms,
    }
    if operation:
        payload["filteredOperations"] = [operation]
    status, out = http(
        "POST", f"{microcks_url}/api/tests",
        json.dumps(payload).encode(), {"Content-Type": "application/json"},
    )
    if status >= 300 or not (test_id := json.loads(out or b"{}").get("id")):
        raise SystemExit(
            f"could not start the test - is '{service_id}' loaded? (sysspec mocks load)"
        )
    deadline = time.monotonic() + timeout_ms / 1000 + 60
    while True:
        status, out = http("GET", f"{microcks_url}/api/tests/{test_id}")
        if status >= 300:
            raise SystemExit(f"could not read test {test_id}")
        result = json.loads(out)
        if not result.get("inProgress"):
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"test {test_id} never finished")
        time.sleep(2)

    exchanges = 0
    for case in result.get("testCaseResults") or []:
        print(f"  {'PASS' if case.get('success') else 'FAIL'}  {case.get('operationName')}")
        for step in case.get("testStepResults") or []:
            exchanges += 1
            detail = f" - {step['message']}" if step.get("message") else ""
            print(
                f"        {'ok  ' if step.get('success') else 'FAIL'} "
                f"{step.get('requestName') or '-'}{detail}"
            )
    if not result.get("success"):
        raise SystemExit(
            f"{service_id} does not conform to its spec - full detail at "
            f"{microcks_url} > Tests"
        )
    if exchanges == 0:
        raise SystemExit(
            f"{service_id}: the runner validated nothing - a green test over "
            "zero exchanges is a failure"
        )
    print(f"contract ok: {service_id} - {exchanges} exchanges validated")


def contract(
    only: str | None,
    specs_dir: str,
    microcks_url: str,
    rest_endpoint: str | None,
    async_endpoint: str | None,
) -> int:
    for d in service_dirs(specs_dir, only):
        for _, doc in spec_docs(d, "openapi"):
            title, version = info(doc)
            encoded = title.replace(" ", "+")
            run_test(
                microcks_url, f"{title}:{version}", "OPEN_API_SCHEMA",
                rest_endpoint or f"http://microcks:8080/rest/{encoded}/{version}",
                20000, None,
            )
        for _, doc in spec_docs(d, "asyncapi"):
            title, version = info(doc)
            encoded = title.replace(" ", "+")
            for op in send_operations(doc):
                run_test(
                    microcks_url, f"{title}:{version}", "ASYNC_API_SCHEMA",
                    async_endpoint
                    or f"ws://async-minion:8081/api/ws/{encoded}/{version}/{op}",
                    15000, f"SEND {op}",
                )
    return 0


def body_matches(expected, actual) -> bool:
    """Structural match: every expected value must appear in the response.
    Mock templating (values containing '{{') is not asserted."""
    if isinstance(expected, str) and "{{" in expected:
        return True
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and body_matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(body_matches(e, a) for e, a in zip(expected, actual))
        )
    return expected == actual


def rest_smoke(doc_title: str, version: str, examples: dict, microcks_url: str) -> None:
    base = f"{microcks_url}/rest/{doc_title.replace(' ', '+')}/{version}"
    for op, cases in (examples.get("operations") or {}).items():
        method, _, path = op.partition(" ")
        for case_name, case in (cases or {}).items():
            request = case.get("request") or {}
            params = dict(request.get("parameters") or {})
            url_path = path
            for key in list(params):
                if f"{{{key}}}" in url_path:
                    url_path = url_path.replace(f"{{{key}}}", str(params.pop(key)))
            url = base + url_path
            if params:
                url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
            body = request.get("body")
            status, out = http(
                method, url,
                body.encode() if body else None,
                dict(request.get("headers") or {}),
            )
            expected = case.get("response") or {}
            want = int(expected.get("status", 200))
            if status != want:
                raise SystemExit(
                    f"{op} [{case_name}]: expected {want}, got {status}"
                )
            expected_body = expected.get("body")
            if expected_body and "json" in (expected.get("mediaType") or ""):
                if not body_matches(json.loads(expected_body), json.loads(out)):
                    raise SystemExit(
                        f"{op} [{case_name}]: response does not match the example"
                    )
            print(f"rest mock ok: {op} [{case_name}] -> {status}")


def payload_schema(doc: dict, op_name: str) -> dict | None:
    """The payload schema of the (single) message on an operation's channel."""
    op = (doc.get("operations") or {}).get(op_name) or {}
    channel_key = (op.get("channel") or {}).get("$ref", "").rsplit("/", 1)[-1]
    channel = (doc.get("channels") or {}).get(channel_key) or {}
    for name in (channel.get("messages") or {}):
        message = ((doc.get("components") or {}).get("messages") or {}).get(name) or {}
        if "payload" in message:
            return message["payload"]
    return None


def event_smoke(doc: dict, minion_url: str) -> None:
    import jsonschema
    from websockets.sync.client import connect

    title, version = info(doc)
    ws_base = minion_url.replace("http://", "ws://").replace("https://", "wss://")
    for op in send_operations(doc):
        url = f"{ws_base}/api/ws/{title.replace(' ', '+')}/{version}/{op}"
        with connect(url) as ws:
            message = json.loads(ws.recv(timeout=20))
        schema = payload_schema(doc, op)
        if schema is not None:
            jsonschema.validate(message, schema)
        print(f"event mock ok: {title}/{op} - envelope validates against the spec")


def test(
    only: str | None,
    specs_dir: str,
    mocks_dir: str,
    microcks_url: str,
    minion_url: str,
) -> int:
    checked = 0
    for d in service_dirs(specs_dir, only):
        for spec, doc in spec_docs(d, "openapi"):
            examples_path = Path(mocks_dir) / f"{d.name}.rest.examples.yaml"
            if not examples_path.is_file():
                print(f"no REST examples for {d.name} ({examples_path}) - skipping")
                continue
            title, version = info(doc)
            rest_smoke(title, version, yaml.safe_load(examples_path.read_text()) or {},
                       microcks_url)
            checked += 1
        for _, doc in spec_docs(d, "asyncapi"):
            event_smoke(doc, minion_url)
            checked += 1
    if checked == 0:
        raise SystemExit("mocks test validated nothing - no specs found")
    return 0


def watch(channel: str, minion_url: str) -> int:
    from websockets.sync.client import connect

    ws_base = minion_url.replace("http://", "ws://").replace("https://", "wss://")
    with connect(f"{ws_base}/api/ws/{channel}") as ws:
        print(f"watching {channel} (Ctrl-C to stop)")
        try:
            while True:
                print(ws.recv())
        except KeyboardInterrupt:
            return 0
