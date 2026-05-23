from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import requests

from travel_dashboard.config import load_env, load_yaml
from travel_dashboard.sources.amadeus import AmadeusClient
from travel_dashboard.sources.duffel import DuffelClient
from travel_dashboard.sources.rail import live_rail_offers, static_rail_estimates
from travel_dashboard.sources.serpapi_google_flights import SerpApiGoogleFlightsClient

FLIGHT_SOURCE_TYPES = {
    "api_amadeus_flight_offers",
    "api_serpapi_google_flights",
    "api_duffel_flight_offers",
}


def enabled_sources(sources_config: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in sources_config.get("sources", []) if s.get("enabled", False)]


def enabled_flight_sources(sources_config: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in enabled_sources(sources_config) if s.get("type") in FLIGHT_SOURCE_TYPES]


def _redact_sensitive(text: str) -> str:
    redacted = re.sub(r"(?i)(api_key=)[^&\s]+", r"\1***", text)
    redacted = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1***", redacted)
    return redacted


def _format_source_error(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        response = exc.response
        status = response.status_code
        reason = response.reason or "HTTP error"
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                if isinstance(payload.get("errors"), list) and payload["errors"]:
                    first = payload["errors"][0]
                    if isinstance(first, dict):
                        detail = (
                            str(first.get("detail") or first.get("message") or first.get("title") or "").strip()
                        )
                detail = detail or str(payload.get("error_description") or payload.get("error") or payload.get("message") or "").strip()
        except ValueError:
            detail = ""
        if detail:
            return f"HTTP {status} {reason}: {_redact_sensitive(detail)}"
        return f"HTTP {status} {reason}"
    return _redact_sensitive(str(exc))


def search_flights(
    departure_date: str,
    adults: int,
    cabin: str,
    checked_bags_per_person: int,
    checked_bag_weight_kg: float | None,
    return_date: str | None = None,
    use_airline_max_weight: bool = True,
) -> tuple[list[dict], list[str]]:
    load_env()
    app_config = load_yaml("config.yaml")
    # This dashboard is intentionally outbound-only. Keeping this guard here prevents
    # accidental round-trip API requests if an old config still contains return_date.
    if str(app_config.get("search", {}).get("trip_type", "one_way")).lower() == "one_way":
        return_date = None
    sources_config = load_yaml("sources.yaml")
    baggage_rules = load_yaml("baggage_rules.yaml")
    offers = []
    messages = []
    for source in enabled_flight_sources(sources_config):
        typ = source.get("type")
        client = None
        if typ == "api_amadeus_flight_offers":
            client = AmadeusClient(source, baggage_rules)
        elif typ == "api_serpapi_google_flights":
            client = SerpApiGoogleFlightsClient(source, baggage_rules)
        elif typ == "api_duffel_flight_offers":
            client = DuffelClient(source, baggage_rules)
        if not client.enabled_and_configured():
            messages.append(f"Skipped {source.get('name')}: enabled but missing credentials.")
            continue
        try:
            source_offers = client.search(
                departure_date=departure_date,
                adults=adults,
                cabin=cabin,
                checked_bags_per_person=checked_bags_per_person,
                checked_bag_weight_kg=checked_bag_weight_kg,
                return_date=return_date,
                use_airline_max_weight=use_airline_max_weight,
            )
            offers.extend([o.to_dict() for o in source_offers])
            messages.append(f"{source.get('name')}: {len(source_offers)} offers.")
        except Exception as exc:
            messages.append(f"{source.get('name')} failed: {_format_source_error(exc)}")
    return offers, messages


def search_flights_window(
    start_date: date,
    days: int,
    adults: int,
    cabin: str,
    checked_bags_per_person: int,
    checked_bag_weight_kg: float | None,
    return_date: str | None = None,
    use_airline_max_weight: bool = True,
) -> tuple[list[dict], list[str]]:
    all_offers = []
    messages = []
    for offset in range(days):
        day = (start_date + timedelta(days=offset)).isoformat()
        offers, msgs = search_flights(
            departure_date=day,
            adults=adults,
            cabin=cabin,
            checked_bags_per_person=checked_bags_per_person,
            checked_bag_weight_kg=checked_bag_weight_kg,
            return_date=return_date,
            use_airline_max_weight=use_airline_max_weight,
        )
        all_offers.extend(offers)
        messages.extend([f"{day}: {m}" for m in msgs])
    return all_offers, messages


def search_rail(config: dict[str, Any]) -> tuple[list[dict], list[str]]:
    load_env()
    sources_config = load_yaml("sources.yaml")
    rows, messages = live_rail_offers(config, enabled_sources(sources_config))
    if rows:
        return [r.to_dict() for r in rows], messages
    fallback_rows = [r.to_dict() for r in static_rail_estimates(config)]
    fallback_message = "Rail: using static planning estimates. Connect TransportAPI or National Rail licensed feeds for live fares."
    if not messages:
        messages = [fallback_message]
    else:
        messages.append(fallback_message)
    return fallback_rows, messages
