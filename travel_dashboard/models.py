from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class FlightOffer:
    source: str
    airline: str
    airline_code: str
    price_gbp: float
    currency: str
    depart_at: str
    arrive_at: str
    duration: Optional[str]
    stops: int
    route: str
    cabin: str
    included_checked_bags: Optional[int] = None
    included_checked_weight_kg: Optional[float] = None
    estimated_baggage_extra_gbp: float = 0.0
    estimated_total_gbp: float = 0.0
    requested_checked_bags_total: int = 0
    requested_checked_bags_per_person: int = 0
    requested_checked_weight_kg: float = 0.0
    airline_max_checked_weight_kg: float = 0.0
    standard_checked_weight_kg: float = 23.0
    extra_bags_total: int = 0
    overweight_bags_total: int = 0
    baggage_feasible: bool = True
    baggage_policy: str = "airline_max"
    baggage_note: str = ""
    deep_link: Optional[str] = None
    raw_json: Optional[str] = None
    fetched_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d.get("fetched_at"):
            d["fetched_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return d


@dataclass
class RailOffer:
    source: str
    route_name: str
    price_gbp: float
    duration: Optional[str]
    changes: Optional[int]
    luggage_score: int
    notes: str
    deep_link: Optional[str] = None
    fetched_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d.get("fetched_at"):
            d["fetched_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return d
