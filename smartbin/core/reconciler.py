from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

CANONICAL_LEVELS = (
    "CLASSIC",
    "STANDARD",
    "GOLD",
    "PLATINUM",
    "SIGNATURE",
    "INFINITE",
    "WORLD",
    "BUSINESS",
    "COMMERCIAL",
    "CORPORATE",
    "PREPAID",
    "GOVERNMENT",
)

LEVEL_MAP = {
    "classic": "CLASSIC",
    "standard": "STANDARD",
    "gold": "GOLD",
    "platinum": "PLATINUM",
    "signature": "SIGNATURE",
    "infinite": "INFINITE",
    "world": "WORLD",
    "world elite": "WORLD",
    "world-elite": "WORLD",
    "business": "BUSINESS",
    "commercial": "COMMERCIAL",
    "corporate": "CORPORATE",
    "prepaid": "PREPAID",
    "government": "GOVERNMENT",
    "govt": "GOVERNMENT",
    "debit": "STANDARD",
    "credit": "CLASSIC",
    "electron": "STANDARD",
    "maestro": "STANDARD",
    "titanium": "PLATINUM",
    "black": "INFINITE",
    "secured": "STANDARD",
    "purchase": "STANDARD",
    "consumer": "CLASSIC",
    "rewards": "CLASSIC",
}

STRING_FIELDS = ("brand", "level", "bank", "phone", "site", "country", "iso2", "currency")
INT_FIELDS = ("prepaid", "commercial", "bin_age", "card_length", "luhn", "avs", "dob", "ssn", "mmn")
DEFAULTS: dict[str, Any] = {
    "brand": "UNKNOWN",
    "level": "UNKNOWN",
    "bank": "UNKNOWN",
    "phone": "",
    "site": "",
    "country": "UNKNOWN",
    "iso2": "",
    "currency": "",
    "prepaid": 0,
    "commercial": 0,
    "bin_age": 0,
    "card_length": 16,
    "luhn": 1,
    "avs": 1,
    "dob": 0,
    "ssn": 0,
    "mmn": 0,
}


def normalize_level(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    raw = " ".join(str(value).replace("_", " ").replace("-", " ").split()).lower()
    if not raw:
        return "UNKNOWN"
    if raw.upper() in CANONICAL_LEVELS:
        return raw.upper()
    if raw in LEVEL_MAP:
        return LEVEL_MAP[raw]
    for token in raw.split():
        if token in LEVEL_MAP:
            return LEVEL_MAP[token]
    return "UNKNOWN"


def _normalize_string(field: str, value: Any) -> str:
    text = str(value).strip()
    if not text or text.upper() in ("UNKNOWN", "N/A", "NONE", "NULL"):
        return ""
    if field == "level":
        normalized = normalize_level(text)
        return "" if normalized == "UNKNOWN" else normalized
    if field == "iso2":
        return text.upper()[:2]
    if field == "currency":
        return text.upper()[:3]
    if field in ("brand", "country", "bank"):
        return text.title() if text.isupper() else text
    return text


def _normalize_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return 1
    if text in ("0", "false", "no", "n", "off", ""):
        return 0
    try:
        return int(float(text))
    except ValueError:
        return None


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def field_confidence(total_weight: float, win_weight: float, win_sources: int, losers: int) -> float:
    """Source weights x agreement x corroboration - disagreement penalty."""
    if total_weight <= 0:
        return 0.0
    weight_factor = min(1.0, total_weight / 2.0)
    agreement = win_weight / total_weight
    corroboration = min(1.0, win_sources / 2.0)
    base = 100.0 * weight_factor * agreement * (0.70 + 0.30 * corroboration)
    return _clamp(base - 15.0 * losers)


def _reconcile_field(
    field: str,
    values: list[tuple[str, float, Any]],
    is_int: bool = False,
) -> tuple[Any, float, dict[str, Any]]:
    provenance: dict[str, Any] = {}
    buckets: dict[Any, float] = {}
    bucket_sources: dict[Any, set[str]] = {}
    for source, weight, raw in values:
        if is_int:
            normalized = _normalize_int(raw)
        else:
            normalized = _normalize_string(field, raw)
        if normalized in (None, ""):
            continue
        provenance[source] = raw
        buckets[normalized] = buckets.get(normalized, 0.0) + weight
        bucket_sources.setdefault(normalized, set()).add(source)
    if not buckets:
        return DEFAULTS[field], 0.0, provenance

    win = max(buckets, key=lambda key: (buckets[key], str(key)))
    total = sum(buckets.values())
    win_weight = buckets[win]
    win_sources = len(bucket_sources[win])
    losers = sum(1 for key in buckets if key != win)
    conf = field_confidence(total, win_weight, win_sources, losers)
    return win, conf, provenance


def reconcile(
    bin8: str,
    results: Iterable[tuple[str, float, dict[str, Any]]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure. Same inputs -> same output. No IO, no clock except an explicit now."""
    now = now or datetime.now(timezone.utc)
    rows = [(name, float(weight), dict(data)) for name, weight, data in results if data]
    record: dict[str, Any] = {"bin": bin8}
    sources_json: dict[str, dict[str, Any]] = {}
    field_conf: dict[str, float] = {}

    for field in STRING_FIELDS:
        values = [(name, weight, data[field]) for name, weight, data in rows if field in data]
        value, conf, provenance = _reconcile_field(field, values)
        record[field] = value
        if provenance:
            sources_json[field] = provenance
            field_conf[field] = conf

    for field in INT_FIELDS:
        values = [(name, weight, data[field]) for name, weight, data in rows if field in data]
        value, conf, provenance = _reconcile_field(field, values, is_int=True)
        record[field] = value
        if provenance:
            sources_json[field] = provenance
            field_conf[field] = conf

    if rows and "bin_age" in field_conf:
        ages = [_normalize_int(data.get("bin_age")) for _, _, data in rows if data.get("bin_age")]
        ages = [a for a in ages if a is not None]
        if ages:
            record["bin_age"] = max(ages)

    populated = [c for c in field_conf.values() if c > 0]
    record["confidence"] = round(sum(populated) / len(populated), 1) if populated else 0.0
    record["sources_json"] = sources_json
    record["field_conf_json"] = field_conf
    record["verified_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    record["sample_size"] = len(rows)
    return record


def decay(confidence: float, verified_utc: str, now: datetime | None = None) -> float:
    """Full strength <=30d, linear to a 50% floor at 90d, then flat 50%."""
    now = now or datetime.now(timezone.utc)
    try:
        verified = datetime.fromisoformat(verified_utc.replace("Z", "+00:00"))
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return confidence
    days = (now - verified).total_seconds() / 86400.0
    if days <= 30:
        return confidence
    if days >= 90:
        return round(confidence * 0.5, 1)
    factor = 1.0 - 0.5 * ((days - 30.0) / 60.0)
    return round(confidence * factor, 1)


def effective_confidence(confidence: float, verified_utc: str, now: datetime | None = None) -> float:
    return decay(confidence, verified_utc, now=now)
