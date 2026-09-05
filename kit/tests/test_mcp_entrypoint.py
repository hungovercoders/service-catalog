"""Tests for the sysspec-mcp entry point's transport selection.

The entry point is thin glue over fastmcp's run(); these tests pin the
contract that matters for deployments: stdio stays the default (the
plugin wiring depends on it), and http mode passes through the binding,
path, and Host-allowlist configuration — including host_origin_protection,
which fastmcp disables by default, silently ignoring allowed_hosts.
"""

import pytest

from sysspec_mcp import __main__ as entry


class _FakeServer:
    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture()
def fake(monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(entry, "build_server", lambda: server)
    for var in ("SYSSPEC_MCP_TRANSPORT", "HOST", "PORT", "SYSSPEC_MCP_PATH",
                "SYSSPEC_MCP_ALLOWED_HOSTS"):
        monkeypatch.delenv(var, raising=False)
    return server


def test_stdio_is_the_default(fake):
    entry.main([])
    assert fake.calls == [((), {})]


def test_http_passes_binding_and_host_protection(fake):
    entry.main(["--transport", "http", "--host", "0.0.0.0", "--port", "9999",
                "--allowed-hosts", "mcp.example.com, other.example.com"])
    ((_, kwargs),) = [fake.calls[0]]
    assert kwargs["transport"] == "http"
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9999
    assert kwargs["path"] == "/mcp"
    assert kwargs["stateless_http"] is True
    assert kwargs["host_origin_protection"] == "auto"
    assert kwargs["allowed_hosts"] == ["mcp.example.com", "other.example.com"]


def test_http_without_allowlist_defaults_to_loopback(fake):
    entry.main(["--transport", "http"])
    ((_, kwargs),) = [fake.calls[0]]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8080
    assert kwargs["allowed_hosts"] is None


def test_environment_configures_http(fake, monkeypatch):
    monkeypatch.setenv("SYSSPEC_MCP_TRANSPORT", "http")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("SYSSPEC_MCP_ALLOWED_HOSTS", "mcp.example.com")
    entry.main([])
    ((_, kwargs),) = [fake.calls[0]]
    assert kwargs["transport"] == "http"
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8123
    assert kwargs["allowed_hosts"] == ["mcp.example.com"]
