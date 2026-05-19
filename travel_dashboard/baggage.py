from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class BaggageEstimate:
    estimated_extra_gbp: float
    requested_checked_bags_total: int
    requested_checked_bags_per_person: int
    requested_checked_weight_kg: float
    airline_max_checked_weight_kg: float
    standard_checked_weight_kg: float
    included_checked_bags_per_person: int
    included_checked_weight_kg: float
    extra_bags_total: int
    overweight_bags_total: int
    baggage_feasible: bool
    baggage_policy: str
    baggage_note: str


def normalise_airline_code(value: str | None) -> str:
    """Return a likely 2-character IATA code from provider-specific fields."""
    if not value:
        return ""
    text = str(value).strip().upper()
    # Common cases: "BA", "BA 251", "BA251", "British Airways BA251".
    match = re.search(r"\b([A-Z0-9]{2})\s?\d{1,4}[A-Z]?\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Z]{2})\b", text)
    if match:
        return match.group(1)
    return text[:2]


def airline_rule(baggage_rules: dict[str, Any], airline_code: str | None) -> dict[str, Any]:
    rules = baggage_rules.get("rules", {})
    return rules.get(normalise_airline_code(airline_code), rules.get("default", {}))


def _cabin_key(cabin: str | None) -> str:
    cabin_norm = (cabin or "ECONOMY").upper().replace(" ", "_")
    if cabin_norm in {"PREMIUM", "PREMIUM_COMFORT", "PREMIUM_ECONOMY"}:
        return "premium_economy"
    if cabin_norm in {"BUSINESS"}:
        return "business"
    if cabin_norm in {"FIRST", "LA_PREMIERE"}:
        return "first"
    return "economy"


def _rule_max_weight(rule: dict[str, Any], cabin: str | None) -> float:
    cabin_key = _cabin_key(cabin)
    by_cabin = rule.get("max_checked_weight_by_cabin_kg", {}) or {}
    value = by_cabin.get(cabin_key, rule.get("max_checked_weight_kg", 32))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 32.0


def _rule_standard_weight(rule: dict[str, Any], cabin: str | None) -> float:
    cabin_key = _cabin_key(cabin)
    by_cabin = rule.get("standard_checked_weight_by_cabin_kg", {}) or {}
    value = by_cabin.get(cabin_key, rule.get("standard_checked_weight_kg", 23))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 23.0


def _rule_included_bags_fallback(rule: dict[str, Any], cabin: str | None) -> int:
    cabin_key = _cabin_key(cabin)
    by_cabin = rule.get("included_checked_bags_fallback_by_cabin", {}) or {}
    value = by_cabin.get(cabin_key, rule.get("included_checked_bags_fallback", rule.get("max_checked_bags_in_economy_estimate", 1)))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def itinerary_rule_summary(
    baggage_rules: dict[str, Any],
    airline_codes: Iterable[str | None] | str | None,
    cabin: str | None,
) -> tuple[list[str], float, float, list[str]]:
    """Return rules for an itinerary.

    For interline or codeshare itineraries, use the strictest per-piece maximum
    across the visible operating/marketing carrier codes. This is conservative,
    but safer for moving with heavy luggage.
    """
    if isinstance(airline_codes, str) or airline_codes is None:
        codes = [normalise_airline_code(airline_codes)] if airline_codes else []
    else:
        codes = [normalise_airline_code(c) for c in airline_codes if normalise_airline_code(c)]
    codes = list(dict.fromkeys(codes))  # preserve order, remove duplicates
    if not codes:
        codes = ["default"]

    max_values: list[float] = []
    standard_values: list[float] = []
    notes: list[str] = []
    for code in codes:
        rule = airline_rule(baggage_rules, code)
        max_values.append(_rule_max_weight(rule, cabin))
        standard_values.append(_rule_standard_weight(rule, cabin))
        if rule.get("notes"):
            name = rule.get("airline_name", code)
            notes.append(f"{name}: {rule['notes']}")

    # Strictest maximum and standard allowance across the itinerary.
    return codes, min(max_values), min(standard_values), notes


def estimate_baggage_extra(
    baggage_rules: dict[str, Any],
    airline_codes: Iterable[str | None] | str | None,
    adults: int,
    checked_bags_per_person: int,
    checked_bag_weight_kg: float | None,
    included_checked_bags: int | None,
    included_checked_weight_kg: float | None,
    cabin: str | None = "ECONOMY",
    use_airline_max_weight: bool = True,
) -> BaggageEstimate:
    """Estimate baggage cost and feasibility for a high-luggage move.

    If `use_airline_max_weight` is true, the requested per-bag weight is set to
    the maximum checked-bag weight for the itinerary's airline(s) and cabin. For
    mixed-carrier itineraries, the strictest visible carrier limit is used.
    """
    codes, itinerary_max_weight, standard_weight, rule_notes = itinerary_rule_summary(
        baggage_rules, airline_codes, cabin
    )

    policy = "airline_max" if use_airline_max_weight else "fixed"
    requested_weight = itinerary_max_weight if use_airline_max_weight else float(checked_bag_weight_kg or itinerary_max_weight)
    requested_total = int(adults) * int(checked_bags_per_person)

    # Fallback fees are based on the first visible carrier, but strictest weight
    # limits above are based on all visible carriers.
    primary_rule = airline_rule(baggage_rules, codes[0] if codes and codes[0] != "default" else None)
    overweight_fee = float(primary_rule.get("overweight_fee_estimate_gbp", 80))
    extra_bag_fee = float(primary_rule.get("extra_bag_fee_estimate_gbp", 120))
    included_bags = included_checked_bags
    if included_bags is None:
        included_bags = _rule_included_bags_fallback(primary_rule, cabin)
    included_weight = float(included_checked_weight_kg or standard_weight)

    feasible = requested_weight <= itinerary_max_weight and requested_total >= 0
    if not feasible:
        note = (
            f"Not bookable as normal checked baggage: requested {requested_weight:g} kg per bag exceeds "
            f"the strictest visible airline limit of {itinerary_max_weight:g} kg."
        )
        return BaggageEstimate(
            estimated_extra_gbp=math.inf,
            requested_checked_bags_total=requested_total,
            requested_checked_bags_per_person=int(checked_bags_per_person),
            requested_checked_weight_kg=requested_weight,
            airline_max_checked_weight_kg=itinerary_max_weight,
            standard_checked_weight_kg=standard_weight,
            included_checked_bags_per_person=int(included_bags),
            included_checked_weight_kg=included_weight,
            extra_bags_total=0,
            overweight_bags_total=0,
            baggage_feasible=False,
            baggage_policy=policy,
            baggage_note=note,
        )

    included_total = int(adults) * int(included_bags)
    extra_bags = max(0, requested_total - included_total)
    overweight_bags = requested_total if requested_weight > included_weight else 0
    estimated = extra_bags * extra_bag_fee + overweight_bags * overweight_fee

    codes_text = ", ".join(c for c in codes if c != "default") or "default policy"
    note_parts = [
        f"Policy={policy}; carriers considered: {codes_text}.",
        f"Requested {requested_total} checked bags total: {checked_bags_per_person}/person × {adults} adult(s).",
        f"Per-bag target weight: {requested_weight:g} kg; strictest airline/cabin max: {itinerary_max_weight:g} kg.",
        f"Included estimate: {included_bags} bag(s)/person at {included_weight:g} kg; extra bags: {extra_bags}; overweight bags: {overweight_bags}.",
    ]
    if rule_notes:
        note_parts.append(" ".join(rule_notes[:3]))
    return BaggageEstimate(
        estimated_extra_gbp=estimated,
        requested_checked_bags_total=requested_total,
        requested_checked_bags_per_person=int(checked_bags_per_person),
        requested_checked_weight_kg=requested_weight,
        airline_max_checked_weight_kg=itinerary_max_weight,
        standard_checked_weight_kg=standard_weight,
        included_checked_bags_per_person=int(included_bags),
        included_checked_weight_kg=included_weight,
        extra_bags_total=extra_bags,
        overweight_bags_total=overweight_bags,
        baggage_feasible=True,
        baggage_policy=policy,
        baggage_note=" ".join(note_parts),
    )
