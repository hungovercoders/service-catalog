"""Tests for the sysspec MCP server's context-efficiency contract.

fastmcp's @mcp.tool decorator returns the original function, so the tools
are called directly here. If a future fastmcp returns a wrapper object
instead, its original callable is exposed as `.fn`.
"""

from pathlib import Path

import pytest

from sysspec_mcp import server

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _specs_dir(monkeypatch):
    monkeypatch.setenv("SPECS_DIR", str(REPO_ROOT / "specs"))


# -- get_artifact -----------------------------------------------------------


def test_get_artifact_full_content_by_default():
    out = server.get_artifact("orders", "openapi/orders.openapi.yaml")
    assert out["authority"] == "contract of record"
    assert out["truncated"] is False
    assert out["section"] is None
    assert "openapi" in out["content"]
    assert out["total_bytes"] == len(out["content"].encode("utf-8"))


def test_get_artifact_section_returns_subtree_only():
    out = server.get_artifact(
        "orders", "openapi/orders.openapi.yaml", section="/components/schemas/Order"
    )
    assert out["section"] == "/components/schemas/Order"
    assert "order_id" in out["content"]
    assert "openapi:" not in out["content"]  # not the whole document


def test_get_artifact_bad_section_lists_available_keys():
    with pytest.raises(ValueError, match="Available there"):
        server.get_artifact(
            "orders", "openapi/orders.openapi.yaml", section="/components/schemas/Nope"
        )


def test_get_artifact_truncates_honestly():
    out = server.get_artifact("orders", "openapi/orders.openapi.yaml", max_bytes=200)
    assert out["truncated"] is True
    assert len(out["content"].encode("utf-8")) <= 200
    assert out["total_bytes"] > 200
    assert "section=" in out["note"]


def test_get_artifact_section_rejected_for_features():
    with pytest.raises(ValueError, match="get_acceptance_criteria"):
        server.get_artifact(
            "orders", "features/place-order.feature", section="/anything"
        )


# -- get_message_schema -----------------------------------------------------


def test_get_message_schema_lists_names_without_bodies():
    out = server.get_message_schema("orders")
    names = [m["name"] for m in out["messages"]]
    assert "OrderPlaced" in names
    assert out["schemas"], "expected OpenAPI component schemas to be listed"
    assert "payload" not in out


def test_get_message_schema_asyncapi_hit():
    out = server.get_message_schema("orders", "OrderPlaced")
    assert out["source"] == "asyncapi"
    assert out["payload"]


def test_get_message_schema_openapi_fallback():
    index = server.get_message_schema("orders")
    schema_name = index["schemas"][0]["name"]
    out = server.get_message_schema("orders", schema_name)
    assert out["source"] == "openapi"
    assert out["payload"]


def test_get_message_schema_miss_lists_names():
    with pytest.raises(ValueError, match="OrderPlaced"):
        server.get_message_schema("orders", "NoSuchThing")


# -- get_acceptance_criteria -------------------------------------------------


def test_acceptance_criteria_names_only_has_no_gherkin():
    out = server.get_acceptance_criteria("orders", names_only=True)
    assert out["truncated"] is False
    for feature in out["features"]:
        assert feature["scenarios"]
        assert "gherkin" not in feature


def test_acceptance_criteria_scenario_filter_includes_header():
    index = server.get_acceptance_criteria("orders", names_only=True)
    first_scenario = index["features"][0]["scenarios"][0]
    out = server.get_acceptance_criteria("orders", scenario=first_scenario)
    feature = out["features"][0]
    assert feature["header"].lstrip().startswith(("@", "#", "Feature:"))
    assert any(first_scenario in m["name"] for m in feature["matched"])
    assert feature["total_scenarios"] >= len(feature["matched"])


def test_acceptance_criteria_scenario_miss_lists_scenarios():
    with pytest.raises(ValueError, match="Scenarios:"):
        server.get_acceptance_criteria("orders", scenario="zzz-no-such-scenario")


def test_acceptance_criteria_path_filter():
    out = server.get_acceptance_criteria("orders", path="features/place-order.feature")
    assert [f["path"] for f in out["features"]] == ["features/place-order.feature"]
    assert "Feature" in out["features"][0]["gherkin"]


def test_acceptance_criteria_unknown_path_raises():
    with pytest.raises(ValueError, match="not declared"):
        server.get_acceptance_criteria("orders", path="features/nope.feature")


def test_acceptance_criteria_budget_degrades_to_index():
    out = server.get_acceptance_criteria("orders", max_bytes=10)
    assert out["truncated"] is True
    assert out["features"][0]["gherkin_omitted"] is True
    assert out["features"][0]["scenarios"]
    assert "path=" in out["note"]


# -- trace_channel -----------------------------------------------------------


def test_trace_channel_known_address():
    produces = server.get_service("orders")["produces"]
    out = server.trace_channel(produces[0])
    assert out["produced_by"] == ["orders"]


def test_trace_channel_unknown_address_returns_empty_not_error():
    out = server.trace_channel("no.such.channel.v9")
    assert out["produced_by"] == []
    assert out["consumed_by"] == []
    assert "No service" in out["note"]


# -- search_specs -------------------------------------------------------------


def test_search_specs_limit_and_truncation_metadata():
    out = server.search_specs("the", limit=1)
    assert out["returned"] == 1
    assert out["total_matches"] > 1
    assert out["truncated"] is True


def test_search_specs_service_filter():
    out = server.search_specs("order", service="orders")
    assert out["hits"]
    assert {h["service"] for h in out["hits"]} == {"orders"}
    assert out["truncated"] is (out["total_matches"] > out["returned"])


def test_search_specs_bad_kind_raises():
    with pytest.raises(ValueError, match="Valid kinds"):
        server.search_specs("order", kind="yaml")


def test_search_specs_unknown_service_raises():
    with pytest.raises(ValueError, match="Available"):
        server.search_specs("order", service="nope")
