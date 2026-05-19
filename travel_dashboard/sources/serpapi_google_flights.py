from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from travel_dashboard.baggage import estimate_baggage_extra, normalise_airline_code
from travel_dashboard.db import raw_json
from travel_dashboard.models import FlightOffer

TRAVEL_CLASS = {
    "ECONOMY": 1,
    "PREMIUM_ECONOMY": 2,
    "BUSINESS": 3,
    "FIRST": 4,
}


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class SerpApiGoogleFlightsClient:
    def __init__(self, source: dict[str, Any], baggage_rules: dict[str, Any]):
        creds = source.get("credentials", {})
        self.api_key = os.getenv(creds.get("api_key_env", "SERPAPI_API_KEY"), "")
        self.source = source
        self.baggage_rules = baggage_rules

    def enabled_and_configured(self) -> bool:
        return bool(self.api_key and self.source.get("enabled", False))

    def search(
        self,
        departure_date: str,
        adults: int,
        cabin: str,
        checked_bags_per_person: int,
        checked_bag_weight_kg: float | None,
        return_date: str | None = None,
        use_airline_max_weight: bool = True,
    ) -> list[FlightOffer]:
        route = self.source.get("route", {})
        req = self.source.get("request", {})
        params = {
            "engine": "google_flights",
            "api_key": self.api_key,
            "departure_id": route.get("origin", "SCL"),
            "arrival_id": route.get("destination", "LHR"),
            "outbound_date": departure_date,
            # SerpApi defaults to round-trip (type=1), which requires return_date.
            # Force one-way by default to match this dashboard's outbound-only design.
            "type": 2,
            "currency": req.get("currency", "GBP"),
            "gl": req.get("gl", "uk"),
            "hl": req.get("hl", "en"),
            "adults": adults,
            "travel_class": TRAVEL_CLASS.get(cabin, 1),
            # Google Flights' bags parameter is carry-on bags, not checked baggage.
            "bags": adults,
        }
        if return_date:
            params["type"] = 1
            params["return_date"] = return_date
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=45)
        r.raise_for_status()
        payload = r.json()
        raw_flights = (payload.get("best_flights") or []) + (payload.get("other_flights") or [])
        offers: list[FlightOffer] = []
        for f in raw_flights[: int(req.get("max_results", 50))]:
            legs = f.get("flights", [])
            if not legs:
                continue
            first = legs[0]
            last = legs[-1]
            airline = first.get("airline", "")
            carrier_codes = []
            for leg in legs:
                code = normalise_airline_code(leg.get("flight_number") or leg.get("airline"))
                if code and code not in carrier_codes:
                    carrier_codes.append(code)
            airline_code = carrier_codes[0] if carrier_codes else normalise_airline_code(airline)
            route_text = " → ".join(
                [first.get("departure_airport", {}).get("id", "SCL")]
                + [leg.get("arrival_airport", {}).get("id", "") for leg in legs]
            )
            price = float(f.get("price") or 0.0)
            baggage = estimate_baggage_extra(
                self.baggage_rules,
                carrier_codes or airline_code,
                adults,
                checked_bags_per_person,
                checked_bag_weight_kg,
                None,
                None,
                cabin=cabin,
                use_airline_max_weight=use_airline_max_weight,
            )
            total = price + baggage.estimated_extra_gbp if baggage.baggage_feasible else float("inf")
            offers.append(
                FlightOffer(
                    source=self.source.get("name", "SerpApi Google Flights"),
                    airline=airline,
                    airline_code=airline_code,
                    price_gbp=price,
                    currency=req.get("currency", "GBP"),
                    depart_at=first.get("departure_airport", {}).get("time", ""),
                    arrive_at=last.get("arrival_airport", {}).get("time", ""),
                    duration=str(f.get("total_duration", "")),
                    stops=max(0, len(legs) - 1),
                    route=route_text,
                    cabin=cabin,
                    estimated_baggage_extra_gbp=baggage.estimated_extra_gbp,
                    estimated_total_gbp=total,
                    requested_checked_bags_total=baggage.requested_checked_bags_total,
                    requested_checked_bags_per_person=baggage.requested_checked_bags_per_person,
                    requested_checked_weight_kg=baggage.requested_checked_weight_kg,
                    airline_max_checked_weight_kg=baggage.airline_max_checked_weight_kg,
                    standard_checked_weight_kg=baggage.standard_checked_weight_kg,
                    extra_bags_total=baggage.extra_bags_total,
                    overweight_bags_total=baggage.overweight_bags_total,
                    baggage_feasible=baggage.baggage_feasible,
                    baggage_policy=baggage.baggage_policy,
                    baggage_note=baggage.baggage_note,
                    deep_link=f.get("booking_token"),
                    raw_json=raw_json(f),
                    fetched_at=_iso_now(),
                )
            )
        return offers
