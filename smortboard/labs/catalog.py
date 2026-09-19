"""packaged model choices, extended by operator data without writing on reads"""

import json
import math
import os
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

_LAB = re.compile(r"[a-z][a-z0-9_-]{0,49}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}\Z")
_TIERS = {"light", "standard", "deep"}
ROLES = ("worker", "reviewer", "orchestrator", "fold")


def parse_ref(value: str) -> tuple[str, str]:
    """split a model ref; a bare legacy model belongs to anthropic"""
    if not isinstance(value, str):
        raise ValueError("model ref must be a string")
    lab, model = value.split("/", 1) if "/" in value else ("anthropic", value)
    if not _LAB.fullmatch(lab) or not _MODEL.fullmatch(model):
        raise ValueError(f"invalid model ref: {value!r}")
    return lab, model


def _validate(catalog: Any) -> None:
    if not isinstance(catalog, dict):
        raise ValueError("catalog must be an object keyed by lab")
    for lab, entry in catalog.items():
        if not _LAB.fullmatch(lab) or not isinstance(entry, dict):
            raise ValueError(f"invalid catalog lab: {lab!r}")
        if not isinstance(entry.get("adapter"), str) or not entry["adapter"]:
            raise ValueError(f"catalog lab {lab!r} needs an adapter")
        if not isinstance(entry.get("models"), list):
            raise ValueError(f"catalog lab {lab!r} needs a models list")
        seen = set()
        for model in entry["models"]:
            if not isinstance(model, dict):
                raise ValueError(f"invalid model in catalog lab {lab!r}")
            _, name = parse_ref(f"{lab}/{model.get('id', '')}")
            if name in seen:
                raise ValueError(f"duplicate catalog model: {lab}/{name}")
            seen.add(name)
            if model.get("tier") not in _TIERS:
                raise ValueError(f"invalid tier for {lab}/{name}")
            if not isinstance(model.get("label"), str) or not model["label"]:
                raise ValueError(f"missing label for {lab}/{name}")
            preferred = model.get("prefer_for", [])
            if not isinstance(preferred, list) or any(role not in ROLES for role in preferred):
                raise ValueError(f"invalid prefer_for for {lab}/{name}")
            prices = model.get("price_per_mtok")
            if prices is not None:
                if not isinstance(prices, dict):
                    raise ValueError(f"invalid prices for {lab}/{name}")
                for key, price in prices.items():
                    if (
                        key not in {"input", "cached_input", "output"}
                        or isinstance(price, bool)
                        or not isinstance(price, int | float)
                        or not math.isfinite(price)
                        or price < 0
                    ):
                        raise ValueError(f"invalid price for {lab}/{name}: {key}")


def load_catalog(override_path: str | Path | None = None) -> dict[str, Any]:
    """merge overrides by lab/model; missing override files have no side effects"""
    catalog = json.loads(files("smortboard.labs").joinpath("catalog.json").read_text())
    config_root = (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        if os.name == "nt"
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    )
    path = (
        Path(override_path)
        if override_path is not None
        else config_root / "smortboard" / "catalog.json"
    )
    if path.exists():
        override = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            raise ValueError("catalog override must be an object keyed by lab")
        for lab, entry in override.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("models", []), list):
                raise ValueError(f"invalid override for lab {lab!r}")
            base = catalog.get(lab, {"models": []})
            models = {model["id"]: model for model in base["models"]}
            for model in entry.get("models", []):
                if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                    raise ValueError(f"invalid model override for lab {lab!r}")
                name = model["id"]
                models[name] = {**models.get(name, {}), **model}
            catalog[lab] = {**base, **entry, "models": list(models.values())}
    _validate(catalog)
    return catalog


def resolve_ref(value: str | None, catalog: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """return a known ref, or None so a proposed unknown model can use the board default"""
    try:
        lab, model = parse_ref(value)
    except ValueError:
        return None
    catalog = load_catalog() if catalog is None else catalog
    if any(row["id"] == model for row in catalog.get(lab, {}).get("models", [])):
        return lab, model
    return None


def tier_of(value: str | None, catalog: dict[str, Any] | None = None) -> str | None:
    catalog = load_catalog() if catalog is None else catalog
    ref = resolve_ref(value, catalog)
    if ref is None:
        return None
    lab, model = ref
    return next(row["tier"] for row in catalog[lab]["models"] if row["id"] == model)
