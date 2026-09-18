"""operator catalog overrides and model refs stay local data, without credential reads"""

import json

import pytest

from smortboard.labs.catalog import load_catalog, parse_ref, resolve_ref, tier_of


def test_legacy_refs_and_unknown_refs(tmp_path):
    catalog = load_catalog(tmp_path / "missing.json")
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
    ],
)
def test_invalid_overrides_are_refused(tmp_path, override):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(override))
    with pytest.raises(ValueError):
        load_catalog(path)
