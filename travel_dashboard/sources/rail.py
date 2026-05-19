from __future__ import annotations

from datetime import datetime
from typing import Any

from travel_dashboard.models import RailOffer


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def static_rail_estimates(config: dict[str, Any]) -> list[RailOffer]:
    """Fallback rail estimates for LHR → Bristol.

    Live UK rail fares should be obtained from a licensed National Rail OJP feed,
    GWR/retailer partner feeds, or a user-verified scraping source. This fallback keeps
    the dashboard useful for planning and comparison.
    """
    return [
        RailOffer(
            source="Manual estimate from public fare pages",
            route_name="Heathrow Express + GWR via Paddington",
            price_gbp=34.0,
            duration="~2h00–2h30",
            changes=1,
            luggage_score=5,
            notes="Uses Heathrow Express advance from £10 plus a low Paddington–Bristol advance estimate around £24. Better luggage space on airport leg.",
            deep_link="https://www.nationalrail.co.uk/destinations/trains-from-heathrow-airport-to-bristol/",
            fetched_at=_iso_now(),
        ),
        RailOffer(
            source="Manual estimate from public fare pages",
            route_name="Elizabeth line + GWR via Paddington",
            price_gbp=37.9,
            duration="~2h15–2h45",
            changes=1,
            luggage_score=3,
            notes="Uses Elizabeth line Heathrow–Zone 1 fare from £13.90 plus a low Paddington–Bristol advance estimate around £24. Cheaper than walk-up Heathrow Express but more crowded.",
            deep_link="https://www.nationalrail.co.uk/destinations/trains-from-heathrow-airport-to-bristol/",
            fetched_at=_iso_now(),
        ),
        RailOffer(
            source="Manual estimate from public fare pages",
            route_name="Through-ticket Heathrow T2/T3 → Bristol Temple Meads",
            price_gbp=35.0,
            duration="Varies by departure",
            changes=1,
            luggage_score=4,
            notes="Retailer pages show advance through tickets from about £35. Use as a benchmark until a live rail feed is connected.",
            deep_link="https://www.thetrainline.com/train-times/heathrow-terminal-2-to-bristol-temple-meads",
            fetched_at=_iso_now(),
        ),
    ]
