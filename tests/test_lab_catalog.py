"""operator catalog overrides and model refs stay local data, without credential reads"""

import json
import subprocess
import sys

import pytest

from smortboard.labs.catalog import load_catalog, parse_ref, resolve_ref, supported_efforts, tier_of


def test_routing_imports_without_store_initialization():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from smortboard.labs.routing import role_ref; role_ref({}, 'worker')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_refs_and_unknown_refs(tmp_path):
    catalog = load_catalog(tmp_path / "missing.json")
    assert {"fable", "opus", "sonnet", "haiku"} <= {
        row["id"] for row in catalog["anthropic"]["models"]
    }
    assert {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} <= {
        row["id"] for row in catalog["openai"]["models"]
    }
    assert parse_ref("sonnet") == ("anthropic", "sonnet")
    assert parse_ref("openai/x") == ("openai", "x")
    assert resolve_ref("sonnet", catalog) == ("anthropic", "sonnet")
    assert resolve_ref("openai/unknown", catalog) is None
    assert resolve_ref("unknown/sonnet", catalog) is None
    assert resolve_ref(None, catalog) is None
    assert resolve_ref("--unsafe", catalog) is None
    assert tier_of("anthropic/opus", catalog) == "deep"
    assert tier_of("openai/unknown", catalog) is None
    assert not (tmp_path / "missing.json").exists()
    assert all("price_per_mtok" not in row for row in catalog["openai"]["models"])


def test_override_merges_by_lab_and_model_and_is_reloaded(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "anthropic": {"models": [{"id": "opus", "label": "Planning"}]},
                "openai": {
                    "models": [
                        {
                            "id": "x",
                            "label": "X",
                            "tier": "standard",
                            "price_per_mtok": {"input": 1, "output": 3},
                        }
                    ]
                },
            }
        )
    )
    before = path.read_bytes()
    catalog = load_catalog(path)
    assert resolve_ref("openai/x", catalog) == ("openai", "x")
    assert catalog["openai"]["adapter"] == "codex"
    opus = next(row for row in catalog["anthropic"]["models"] if row["id"] == "opus")
    assert opus["label"] == "Planning" and opus["tier"] == "deep"
    assert resolve_ref("sonnet", catalog)
    assert path.read_bytes() == before
    path.write_text("{}")
    assert resolve_ref("openai/x", load_catalog(path)) is None


@pytest.mark.parametrize(
    "override",
    [
        [],
        {"openai": {"models": "bad"}},
        {"openai": {"models": [{"id": "--unsafe", "label": "X", "tier": "standard"}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "price_per_mtok": {"input": -1}}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "price_per_mtok": {"input": float("nan")}}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "effort_levels": "high"}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "effort_levels": ["high", "high"]}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "effort_levels": ["unknown"]}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "effort_levels": [{}]}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "default_effort": "ultra"}]}},
        {"openai": {"models": [{"id": "gpt-6-astra", "default_effort": []}]}},
    ],
)
def test_invalid_overrides_are_refused(tmp_path, override):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(override))
    with pytest.raises(ValueError):
        load_catalog(path)


def test_exact_versions_and_effort_metadata_are_available(tmp_path):
    catalog = load_catalog(tmp_path / "missing.json")
    for model in (
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-haiku-4-5",
    ):
        assert resolve_ref(f"anthropic/{model}", catalog) == ("anthropic", model)
    for model in ("gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"):
        assert resolve_ref(f"openai/{model}", catalog) == ("openai", model)
    assert "max" in supported_efforts("anthropic", "claude-opus-5-5", catalog)
    assert "xhigh" in supported_efforts("openai", "gpt-6.1-sol", catalog)
    assert supported_efforts("anthropic", "claude-haiku-4-5", catalog) == []
    for lab in catalog.values():
        for row in lab["models"]:
            assert row["default_effort"] is None or row["default_effort"] in row["effort_levels"]


def test_override_without_effort_metadata_retains_legacy_support(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "openai": {
                    "models": [
                        {"id": "custom", "label": "custom", "tier": "standard"},
                        {
                            "id": "no-effort",
                            "label": "no effort",
                            "tier": "light",
                            "effort_levels": [],
                        },
                    ]
                }
            }
        )
    )
    catalog = load_catalog(path)
    assert supported_efforts("openai", "custom", catalog) == ["low", "medium", "high"]
    assert supported_efforts("openai", "no-effort", catalog) == []
