# SCL → LHR → Bristol Fare Dashboard

A Streamlit dashboard for planning travel from Santiago de Chile (SCL) to London Heathrow (LHR), then from Heathrow to Bristol Temple Meads.

The app is designed for a high-luggage, one-way move: by default it assumes **2 adult passengers**, each with **2 checked bags**, plus on-board luggage and a personal item. The checked-bag weight can be evaluated dynamically by airline: in **airline-max mode**, each checked bag is set to the maximum per-piece checked-bag weight allowed by the visible airline/cabin rule. The default outbound date window is **1–10 September 2026**, and can be changed from the sidebar.

## What's new in this version

- Flight searches are now explicitly **one-way / outbound only**: SCL → LHR, with no return date sent to providers.
- The sidebar now has a **start/end departure date window selector**, defaulting to **2026-09-01 → 2026-09-10**.
- The baggage model is no longer a single hard-coded 32 kg assumption.
- The dashboard evaluates **2 checked bags/person × 2 people = 4 checked bags total** by default.
- In airline-max mode, every offer is evaluated using that itinerary's airline/cabin maximum checked-bag weight.
- Mixed-carrier or codeshare itineraries use the **strictest visible checked-bag limit** across the route.
- The flight table now shows: requested checked bags, requested per-bag weight, airline maximum, extra bags, overweight bags, feasibility, and a per-offer baggage note.

## Why API-first?

Airline and rail fare pages often have restrictive terms, dynamic JavaScript, bot protection, and rapidly changing selectors. The dashboard therefore uses the same pluggable source pattern as the housing dashboard, but prioritises official or partner APIs. Static scraping entries are included in `sources.yaml`, but set to `enabled: false` until you manually verify terms, robots.txt, and selectors.

## Included sources

### Flights

- `Amadeus Flight Offers Search` — enabled by default, but requires `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`.
- `SerpApi Google Flights` — optional; supports Google Flights style search and may expose booking/baggage information in some flows.
- `Duffel Flight Offers` — optional; useful if you want a more complete selling/booking-style API, including extras in supported markets.

### Rail

The rail module currently uses conservative public-fare benchmarks:

- Heathrow Express + GWR via Paddington
- Elizabeth line + GWR via Paddington
- Through-ticket Heathrow T2/T3 → Bristol Temple Meads benchmark

For live UK rail fares, use a licensed National Rail OJP feed or a retailer/partner API. National Rail OJP is listed in `sources.yaml` but disabled because it requires a formal licence.

## Setup

```bash
cd scl_lhr_bristol_fare_dashboard
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Add your API credentials to `.env`.

The default search window lives in `config.yaml`:

```yaml
search:
  trip_type: one_way
  departure_date_start: "2026-09-01"
  departure_date_end: "2026-09-10"
```

You can adjust this in the Streamlit sidebar without editing the file. The scheduler uses the same start/end window for its 6-hour refreshes.


For Amadeus test mode, create a free developer app and add:

```dotenv
AMADEUS_CLIENT_ID=...
AMADEUS_CLIENT_SECRET=...
AMADEUS_HOST=https://test.api.amadeus.com
```

## Run the dashboard

```bash
streamlit run app.py
```

## Optional background refresh

The dashboard itself uses Streamlit caching with a 6-hour TTL. If you prefer a separate scheduled refresh that writes to SQLite:

```bash
python scheduler.py --once
python scheduler.py
```

The cadence is controlled by:

```dotenv
REFRESH_INTERVAL_HOURS=6
```

## Docker

```bash
docker compose up --build
```

Then open <http://localhost:8501>.

## Baggage model

The dashboard separates:

1. Base fare returned by a provider.
2. Included checked-bag allowance when available.
3. Requested high-luggage plan.
4. Estimated extra-bag and overweight costs.
5. Estimated total.

The default high-luggage plan is:

```text
2 adults × 2 checked bags per adult = 4 checked bags total
```

In airline-max mode, the per-bag weight is calculated per offer. For example:

```text
BA itinerary → 4 checked bags × 32 kg, if the visible cabin/rule allows 32 kg
KLM economy itinerary → standard included allowance may be 23 kg, but the overweight model can still estimate 32 kg where allowed
Mixed-carrier itinerary → strictest visible airline/cabin max is used
```

The model remains conservative because exact baggage pricing is fare-family and booking-channel dependent. When a provider returns exact baggage/ancillary pricing, those fields should be preferred over the fallback estimates in `baggage_rules.yaml`.

## Files

```text
app.py                         Streamlit dashboard
scheduler.py                   Optional 6-hour refresh worker
config.yaml                    Default app/search settings
sources.yaml                   API/scraping source definitions
baggage_rules.yaml             Airline baggage policy/fee fallback model
travel_dashboard/              Python package
  baggage.py                   Airline-max baggage estimator
  config.py                    YAML/.env loading
  db.py                        SQLite storage
  fetch.py                     Source orchestration
  models.py                    Dataclasses
  sources/                     API clients and rail estimates
```
