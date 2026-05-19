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


class DuffelClient:
    """Minimal Duffel offer-request client.

    Duffel is powerful, but its response varies by carrier and market. This parser keeps
    the dashboard usable while preserving the raw JSON for inspection.
    """

    def __init__(self, source: dict[str, Any], baggage_rules: dict[str, Any]):
        creds = source.get("credentials", {})
        self.access_token = os.getenv(creds.get("access_token_env", "DUFFEL_ACCESS_TOKEN"), "")
        self.version = os.getenv(creds.get("version_env", "DUFFEL_VERSION"), "v2")
        self.source = source
        self.baggage_rules = baggage_rules

    def enabled_and_configured(self) -> bool:
        return bool(self.access_token and self.source.get("enabled", False))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Duffel-Version": self.version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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
        slices = [
            {
                "origin": route.get("origin", "SCL"),
                "destination": route.get("destination", "LHR"),
                "departure_date": departure_date,
            }
        ]
        if return_date:
            slices.append(
                {
                    "origin": route.get("destination", "LHR"),
                    "destination": route.get("origin", "SCL"),
                    "departure_date": return_date,
                }
            )
        passengers = [{"type": "adult"} for _ in range(adults)]
        body = {
            "data": {
                "slices": slices,
                "passengers": passengers,
                "cabin_class": cabin.lower().replace("_", " "),
                "private_fares": req.get("private_fares", False),
            }
        }
        create = requests.post(
            "https://api.duffel.com/air/offer_requests",
            headers=self._headers(),
            json=body,
            timeout=45,
        )
        create.raise_for_status()
        offer_request = create.json().get("data", {})
        offers_url = f"https://api.duffel.com/air/offers?offer_request_id={offer_request.get('id')}&limit=50"
        r = requests.get(offers_url, headers=self._headers(), timeout=45)
        r.raise_for_status()
        payload = r.json()
        offers: list[FlightOffer] = []
        for item in payload.get("data", []):
            slices_data = item.get("slices", [])
            if not slices_data:
                continue
            segs = slices_data[0].get("segments", [])
            if not segs:
                continue
            first = segs[0]
            last = segs[-1]
            owner = item.get("owner", {}) or {}
            airline_code = normalise_airline_code(owner.get("iata_code") or "")
            carrier_codes = [airline_code]
            route_text = " → ".join(
                [first.get("origin", {}).get("iata_code", "SCL")]
                + [s.get("destination", {}).get("iata_code", "") for s in segs]
            )
            for seg in segs:
                marketing = seg.get("marketing_carrier", {}) or {}
                operating = seg.get("operating_carrier", {}) or {}
                for carrier in (marketing, operating):
                    code = normalise_airline_code(carrier.get("iata_code"))
                    if code and code not in carrier_codes:
                        carrier_codes.append(code)
            # Duffel may include baggage info per passenger/segment, but it varies.
            included_bags = None
            included_weight = None
            try:
                baggages = item["slices"][0]["segments"][0]["passengers"][0].get("baggages", [])
                checked = [b for b in baggages if b.get("type") == "checked"]
                if checked:
                    included_bags = len(checked)
            except Exception:
                pass
            price = float(item.get("total_amount") or 0.0)
            baggage = estimate_baggage_extra(
                self.baggage_rules,
                carrier_codes,
                adults,
                checked_bags_per_person,
                checked_bag_weight_kg,
                included_bags,
                included_weight,
                cabin=cabin,
                use_airline_max_weight=use_airline_max_weight,
            )
            total = price + baggage.estimated_extra_gbp if baggage.baggage_feasible else float("inf")
            offers.append(
                FlightOffer(
                    source=self.source.get("name", "Duffel"),
                    airline=owner.get("name", airline_code),
                    airline_code=airline_code,
                    price_gbp=price,
                    currency=item.get("total_currency", req.get("currency", "GBP")),
                    depart_at=first.get("departing_at", ""),
                    arrive_at=last.get("arriving_at", ""),
                    duration=slices_data[0].get("duration"),
                    stops=max(0, len(segs) - 1),
                    route=route_text,
                    cabin=cabin,
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
