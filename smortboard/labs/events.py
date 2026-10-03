"""read neutral events, including legacy logs normalized only at the adapter boundary"""

from typing import Any

from smortboard.labs.registry import get_adapter


def recover_usage(usage: dict[str, Any], lab: str, model: str | None) -> dict[str, Any]:
    """recover unknown codex costs on read; raw records and known costs stay untouched"""
    if usage.get("cost_usd") is not None or lab != "openai" or not model:
        return usage
    # historical records without complete counts remain unknown, never a zero-dollar estimate
    counts = [usage.get(key) for key in ("input_tokens", "output_tokens", "cached_tokens")]
    if any(type(count) is not int or count < 0 for count in counts) or counts[2] > counts[0]:
        return usage
    from smortboard.labs.catalog import load_catalog

    prices = next(
        (
            row.get("price_per_mtok")
            for row in load_catalog().get(lab, {}).get("models", [])
            if row["id"] == model
        ),
        None,
    )
    if not prices or not all(key in prices for key in ("input", "cached_input", "output")):
        return usage
    input_tokens, output_tokens, cached_tokens = counts
    cost = (
        (input_tokens - cached_tokens) * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000
    return {
        **usage,
        "cost_usd": cost,
        "cost_estimated": True,
        "cost_provenance": {"method": "historical_estimate", "price_per_mtok": dict(prices)},
    }


def neutral_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    payload = event.get("payload") or {}
    if isinstance(payload.get("neutral"), list):
        return [
            {
                **item,
                "usage": recover_usage(
                    item["usage"], item.get("lab", "anthropic"), item.get("model")
                ),
            }
            if item.get("kind") == "usage" and isinstance(item.get("usage"), dict)
            else item
            for item in payload["neutral"]
        ]
    lab = payload.get("lab") or "anthropic"
    raw = {**payload, "type": payload.get("type") or event.get("kind")}
    return [
        {
            **item.to_dict(),
            "lab": lab,
            "profile": payload.get("profile") or "default",
            "model": item.model or payload.get("model"),
        }
        for item in get_adapter(lab).normalize(raw)
    ]


def result_fields(event: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item["result"] for item in neutral_events(event) if item["kind"] == "result"), None
    )


def cost_sum(costs) -> float | None:
    values = list(costs)
    return None if any(value is None for value in values) else round(sum(values), 6)


def event_cost(event: dict[str, Any]) -> float | None:
    items = neutral_events(event)
    reported = next(
        (item["result"].get("cost_usd") for item in items if item["kind"] == "result"), None
    )
    if reported is not None:
        return float(reported)
    usage = [item["usage"].get("cost_usd") for item in items if item["kind"] == "usage"]
    return cost_sum(usage) if usage else None


def model_ref(item: dict[str, Any]) -> str | None:
    model = item.get("model")
    return f"{item.get('lab') or 'anthropic'}/{model}" if model else None
