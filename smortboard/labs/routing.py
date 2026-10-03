"""resolve role and card choices without changing legacy stored settings"""

from smortboard.labs.catalog import EFFORT_LEVELS, load_catalog, parse_ref, resolve_ref


def role_ref(settings: dict, role: str, card: dict | None = None) -> tuple[str, str]:
    if card and card.get("model"):
        lab, model = parse_ref(card["model"])
        return card.get("lab") or lab, model
    model = settings.get(f"{role}_model")
    lab = settings.get(f"{role}_lab")
    if role == "fold" and not model and not lab:
        model = settings.get("orchestrator_model")
        lab = settings.get("orchestrator_lab")
    if model:
        inferred, model = parse_ref(model)
        return lab or inferred, model
    lab = lab or "anthropic"
    rows = load_catalog()[lab]["models"]
    preferred = next((row for row in rows if role in row.get("prefer_for", [])), None)
    if preferred is None:
        tier = "deep" if role in ("orchestrator", "fold") else "standard"
        preferred = next((row for row in rows if row.get("tier") == tier), rows[0])
    return lab, preferred["id"]


def role_effort(settings: dict, role: str) -> str | None:
    """the operator's effort for one role, or None - which passes no flag, the cli's default"""
    value = settings.get(f"{role}_effort")
    return value if value in EFFORT_LEVELS else None


def command_model(lab: str, model: str) -> str:
    return model if lab == "anthropic" else f"{lab}/{model}"


def _queued_fallback(store, role: str, card: dict) -> tuple[dict, tuple[str, str]] | None:
    """the latest unconsumed lab_fallback for the role, with its resolved ref"""
    events = store.list_events(card["id"])
    consumed = {
        event["payload"].get("fallback_seq")
        for event in events
        if event["kind"] == "fallback_consumed"
    }
    for event in reversed(events):
        payload = event["payload"]
        if (
            event["kind"] != "lab_fallback"
            or payload.get("role") != role
            or event["seq"] in consumed
        ):
            continue
        ref = resolve_ref(f"{payload.get('lab')}/{payload.get('model')}")
        if ref is not None:
            return event, ref
    return None


def run_ref(store, role: str, card: dict, *, consume: bool = False) -> tuple[str, str]:
    """a queued fallback applies once to its role, leaving the stored card choice intact"""
    queued = _queued_fallback(store, role, card)
    if queued is not None:
        event, ref = queued
        if consume:
            store.append_event(
                card["id"], "fallback_consumed", {"fallback_seq": event["seq"], "role": role}
            )
        return ref
    return role_ref(store.get_settings(), role, card if role == "worker" else None)


def run_effort(store, role: str, card: dict) -> str | None:
    """the effort for the run run_ref names: a queued fallback's own, else the card's (worker
    only, like the card's model), else the role's. read before run_ref consumes the fallback"""
    queued = _queued_fallback(store, role, card)
    if queued is not None and queued[0]["payload"].get("effort") in EFFORT_LEVELS:
        return queued[0]["payload"]["effort"]
    if role == "worker" and card.get("effort") in EFFORT_LEVELS:
        return card["effort"]
    return role_effort(store.get_settings(), role)
