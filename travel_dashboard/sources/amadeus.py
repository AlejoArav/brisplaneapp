from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from travel_dashboard.baggage import estimate_baggage_extra, normalise_airline_code
from travel_dashboard.db import raw_json
from travel_dashboard.models import FlightOffer


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class AmadeusClient:
    def __init__(self, source: dict[str, Any], baggage_rules: dict[str, Any]):
        creds = source.get("credentials", {})
        host_env = creds.get("host_env", "AMADEUS_HOST")
        self.host = os.getenv(host_env, "https://test.api.amadeus.com").rstrip("/")
        self.client_id = os.getenv(creds.get("client_id_env", "AMADEUS_CLIENT_ID"), "")
        self.client_secret = os.getenv(creds.get("client_secret_env", "AMADEUS_CLIENT_SECRET"), "")
        self.source = source
        self.baggage_rules = baggage_rules

    def enabled_and_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.source.get("enabled", False))

    def _token(self) -> str:
        url = f"{self.host}/v1/security/oauth2/token"
        r = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]

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
        token = self._token()
        route = self.source.get("route", {})
        req = self.source.get("request", {})
        params = {
            "originLocationCode": route.get("origin", "SCL"),
            "destinationLocationCode": route.get("destination", "LHR"),
            "departureDate": departure_date,
            "adults": adults,
            "currencyCode": req.get("currency", "GBP"),
            "max": req.get("max_results", 50),
            "travelClass": cabin,
        }
        if return_date:
            params["returnDate"] = return_date
        url = f"{self.host}/v2/shopping/flight-offers"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=45)
        r.raise_for_status()
        payload = r.json()
        dictionaries = payload.get("dictionaries", {})
        carriers = dictionaries.get("carriers", {})
        offers: list[FlightOffer] = []
        for item in payload.get("data", []):
            itineraries = item.get("itineraries", [])
            first_itin = itineraries[0] if itineraries else {}
            segments = first_itin.get("segments", [])
            if not segments:
                continue
            dep = segments[0].get("departure", {}).get("at", "")
            arr = segments[-1].get("arrival", {}).get("at", "")
            carrier_codes = [normalise_airline_code(seg.get("carrierCode", "")) for seg in segments]
            carrier_code = carrier_codes[0] if carrier_codes else ""
            route_text = " → ".join(
                [segments[0].get("departure", {}).get("iataCode", "SCL")]
                + [s.get("arrival", {}).get("iataCode", "") for s in segments]
            )
            included_bags = None
            included_weight = None
            cabin_value = cabin
            traveler_pricings = item.get("travelerPricings", [])
            if traveler_pricings:
                fare_segments = traveler_pricings[0].get("fareDetailsBySegment", [])
                if fare_segments:
                    first_fare = fare_segments[0]
                    cabin_value = first_fare.get("cabin", cabin)
                    checked = first_fare.get("includedCheckedBags", {}) or {}
                    included_bags = checked.get("quantity")
                    included_weight = checked.get("weight")
            base_price = float(item.get("price", {}).get("grandTotal", 0.0))
            baggage = estimate_baggage_extra(
                self.baggage_rules,
                carrier_codes,
                adults,
                checked_bags_per_person,
                checked_bag_weight_kg,
                included_bags,
                included_weight,
                cabin=cabin_value,
                use_airline_max_weight=use_airline_max_weight,
            )
            total = base_price + baggage.estimated_extra_gbp if baggage.baggage_feasible else float("inf")
            offers.append(
                FlightOffer(
                    source=self.source.get("name", "Amadeus"),
                    airline=carriers.get(carrier_code, carrier_code),
                    airline_code=carrier_code,
                    price_gbp=base_price,
                    currency=item.get("price", {}).get("currency", req.get("currency", "GBP")),
                    depart_at=dep,
                    arrive_at=arr,
                    duration=first_itin.get("duration"),
                    stops=max(0, len(segments) - 1),
                    route=route_text,
                    cabin=cabin_value,
                    included_checked_bags=included_bags,
                    included_checked_weight_kg=included_weight,
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
                    deep_link=None,
                    raw_json=raw_json(item),
                    fetched_at=_iso_now(),
                )
            )
        return offers
