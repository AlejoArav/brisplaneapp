from __future__ import annotations

import os
from datetime import date, timedelta
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import streamlit as st

from travel_dashboard.config import get_refresh_ttl_seconds, load_env, load_yaml
from travel_dashboard.db import connect, init_db, load_table, replace_flights, replace_rail
from travel_dashboard.fetch import search_flights_window, search_rail

st.set_page_config(page_title="SCL → LHR → Bristol fares", layout="wide")

load_env()
CONFIG = load_yaml("config.yaml")
BAGGAGE_RULES = load_yaml("baggage_rules.yaml")
TTL = get_refresh_ttl_seconds(CONFIG)


@st.cache_data(ttl=TTL, show_spinner="Searching fares and updating local cache…")
def refresh_data_cached(
    start_date: date,
    window_days: int,
    adults: int,
    cabin: str,
    checked_bags_per_person: int,
    checked_bag_weight_kg: float | None,
    use_airline_max_weight: bool,
):
    flight_rows, flight_messages = search_flights_window(
        start_date=start_date,
        days=window_days,
        adults=adults,
        cabin=cabin,
        checked_bags_per_person=checked_bags_per_person,
        checked_bag_weight_kg=checked_bag_weight_kg,
        use_airline_max_weight=use_airline_max_weight,
        return_date=None,
    )
    rail_rows, rail_messages = search_rail(CONFIG)
    conn = connect()
    init_db(conn)
    replace_flights(conn, flight_rows)
    replace_rail(conn, rail_rows)
    flights_df = load_table(conn, "flight_offers")
    rail_df = load_table(conn, "rail_offers")
    conn.close()
    return flights_df, rail_df, flight_messages + rail_messages


def local_cache() -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = connect()
    init_db(conn)
    flights = load_table(conn, "flight_offers")
    rail = load_table(conn, "rail_offers")
    conn.close()
    return flights, rail


def normalise_df(flights_df: pd.DataFrame, rail_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    for df in (flights_df, rail_df):
        for col in [
            "price_gbp",
            "estimated_baggage_extra_gbp",
            "estimated_total_gbp",
            "included_checked_bags",
            "included_checked_weight_kg",
            "requested_checked_bags_total",
            "requested_checked_bags_per_person",
            "requested_checked_weight_kg",
            "airline_max_checked_weight_kg",
            "standard_checked_weight_kg",
            "extra_bags_total",
            "overweight_bags_total",
            "luggage_score",
        ]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    if "baggage_feasible" in flights_df.columns:
        flights_df["baggage_feasible"] = flights_df["baggage_feasible"].fillna(True).astype(bool)
    return flights_df, rail_df


def _parse_date_setting(value, fallback: date) -> date:
    """Parse dates from YAML/env-friendly values; PyYAML may return strings or date objects."""
    if isinstance(value, date):
        return value
    if value in (None, "", "null"):
        return fallback
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return fallback


def _default_departure_window(search_cfg: dict) -> tuple[date, date]:
    fallback_start = date.today() + timedelta(days=60)
    start = _parse_date_setting(
        search_cfg.get("departure_date_start", search_cfg.get("departure_date")),
        fallback_start,
    )
    end_raw = search_cfg.get("departure_date_end")
    if end_raw in (None, "", "null"):
        default_days = max(1, int(search_cfg.get("date_window_days", 10)))
        end = start + timedelta(days=default_days - 1)
    else:
        end = _parse_date_setting(end_raw, start)
    if end < start:
        end = start
    return start, end


def _normalise_date_input(value, fallback_start: date, fallback_end: date) -> tuple[date, date]:
    """Streamlit returns either a single date or a tuple while the range is being selected."""
    if isinstance(value, tuple):
        if len(value) >= 2 and value[0] is not None and value[1] is not None:
            return value[0], value[1]
        if len(value) >= 1 and value[0] is not None:
            return value[0], value[0]
        return fallback_start, fallback_end
    if value is None:
        return fallback_start, fallback_end
    return value, value


def _inclusive_window_days(start: date, end: date) -> int:
    return max(1, (end - start).days + 1)


def _currency_code(app_cfg: dict) -> str:
    return str(app_cfg.get("default_currency", "GBP")).upper()


def _gbp_to_target_rate(app_cfg: dict, target_currency: str) -> float:
    currency = target_currency.upper()
    if currency == "GBP":
        return 1.0
    env_key = f"GBP_TO_{currency}_RATE"
    env_value = os.getenv(env_key)
    if env_value:
        try:
            rate = float(env_value)
            if rate > 0:
                return rate
        except ValueError:
            pass
    cfg_key = f"gbp_to_{currency.lower()}_rate"
    try:
        cfg_value = float(app_cfg.get(cfg_key, 0.0))
        if cfg_value > 0:
            return cfg_value
    except (TypeError, ValueError):
        pass
    if currency == "CLP":
        return 1210.0
    return 1.0


def _convert_amount(
    value: float | int | None,
    from_currency: str | None,
    target_currency: str,
    gbp_to_target_rate: float,
) -> float | None:
    if value is None or pd.isna(value):
        return None
    amount = float(value)
    from_code = str(from_currency or "GBP").upper()
    target_code = target_currency.upper()
    if from_code == target_code:
        return amount
    if from_code == "GBP":
        return amount * gbp_to_target_rate
    if from_code == "CLP" and target_code == "GBP" and gbp_to_target_rate > 0:
        return amount / gbp_to_target_rate
    return amount


def _money_prefix(currency: str) -> str:
    code = currency.upper()
    if code == "CLP":
        return "CLP$"
    if code == "GBP":
        return "£"
    return f"{code} "


def _format_money(value: float | int | None, currency: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{_money_prefix(currency)}{float(value):,.0f}"


def _departure_date_from_text(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return date.today().isoformat()


def _route_endpoints(route_text: str | None) -> tuple[str, str]:
    parts = [p.strip() for p in str(route_text or "").split("→") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return "SCL", "LHR"


def _default_search_link(route_text: str | None, depart_at: str | None, adults: int, cabin: str, currency: str) -> str:
    origin, destination = _route_endpoints(route_text)
    dep_date = _departure_date_from_text(depart_at)
    cabin_text = str(cabin or "ECONOMY").replace("_", " ").lower()
    query = f"Flights from {origin} to {destination} on {dep_date} for {adults} adults {cabin_text}"
    return f"https://www.google.com/travel/flights?q={quote_plus(query)}&curr={currency.upper()}"


def _add_display_columns(
    flights_df: pd.DataFrame,
    rail_df: pd.DataFrame,
    app_cfg: dict,
    adults: int,
    cabin: str,
) -> tuple[pd.DataFrame, pd.DataFrame, str, float]:
    target_currency = _currency_code(app_cfg)
    gbp_to_target_rate = _gbp_to_target_rate(app_cfg, target_currency)
    flights = flights_df.copy()
    rail = rail_df.copy()

    if not flights.empty:
        if "currency" not in flights.columns:
            flights["currency"] = "GBP"
        flights["currency"] = flights["currency"].astype(str).str.upper()
        flights["price_display"] = flights.apply(
            lambda row: _convert_amount(row.get("price_gbp"), row.get("currency"), target_currency, gbp_to_target_rate),
            axis=1,
        )
        if "estimated_baggage_extra_gbp" not in flights.columns:
            flights["estimated_baggage_extra_gbp"] = 0.0
        flights["estimated_baggage_extra_display"] = flights["estimated_baggage_extra_gbp"].apply(
            lambda v: _convert_amount(v, "GBP", target_currency, gbp_to_target_rate)
        )

        def _row_total(row: pd.Series) -> float:
            feasible = row.get("baggage_feasible", True)
            if pd.isna(feasible):
                feasible = True
            if not feasible:
                return float("inf")
            price_val = row.get("price_display")
            bag_val = row.get("estimated_baggage_extra_display")
            if pd.isna(price_val) or pd.isna(bag_val):
                return float("inf")
            return float(price_val) + float(bag_val)

        flights["estimated_total_display"] = flights.apply(_row_total, axis=1)
        flights["search_link"] = flights.apply(
            lambda row: row.get("deep_link")
            if str(row.get("deep_link", "")).startswith("http")
            else _default_search_link(
                row.get("route"),
                row.get("depart_at"),
                adults=adults,
                cabin=cabin,
                currency=target_currency,
            ),
            axis=1,
        )

    if not rail.empty:
        rail["price_display"] = rail.get("price_gbp", pd.Series(dtype=float)).apply(
            lambda v: _convert_amount(v, "GBP", target_currency, gbp_to_target_rate)
        )

    return flights, rail, target_currency, gbp_to_target_rate


app_cfg = CONFIG.get("app", {})
search_cfg = CONFIG.get("search", {})
bag_settings = BAGGAGE_RULES.get("settings", {})

st.title(app_cfg.get("title", "SCL → LHR → Bristol fare dashboard"))
st.caption(
    "Compares flight offers with an airline-policy baggage model for a high-luggage move, "
    "then adds planning estimates for LHR → Bristol rail transfer. Searches are outbound-only and cached for 6 hours by default."
)

with st.sidebar:
    st.header("Search settings")
    default_start, default_end = _default_departure_window(search_cfg)
    dep_range = st.date_input(
        "Departure date window",
        value=(default_start, default_end),
        help="Outbound-only search window for SCL → LHR. The default is 1–10 September 2026 and can be adjusted here.",
    )
    dep_date, dep_end_date = _normalise_date_input(dep_range, default_start, default_end)
    if dep_end_date < dep_date:
        st.warning("The end date was before the start date, so only the start date will be searched.")
        dep_end_date = dep_date
    window_days = _inclusive_window_days(dep_date, dep_end_date)
    st.caption(
        f"Outbound-only search: SCL → LHR, departures from {dep_date.isoformat()} "
        f"to {dep_end_date.isoformat()} inclusive ({window_days} date{'s' if window_days != 1 else ''}). "
        "No return date is sent to providers."
    )

    adults = st.number_input("Adults", min_value=1, max_value=9, value=int(app_cfg.get("default_adults", 2)))
    cabin = st.selectbox(
        "Cabin",
        options=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
        index=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"].index(str(search_cfg.get("cabin", "ECONOMY"))),
    )

    st.subheader("Baggage assumptions")
    checked_bags = st.number_input(
        "Checked bags per person",
        min_value=0,
        max_value=9,
        value=int(app_cfg.get("default_checked_bags_per_person", bag_settings.get("default_bags_per_person", 2))),
        help="For your move, keep this at 2 unless you decide to ship some luggage separately.",
    )
    use_airline_max_weight = st.toggle(
        "Use each airline's maximum checked-bag weight",
        value=bool(app_cfg.get("default_use_airline_max_weight", bag_settings.get("default_use_airline_max_weight", True))),
        help="If enabled, the dashboard uses the strictest visible airline/cabin maximum for each itinerary.",
    )
    fixed_bag_weight = None
    if not use_airline_max_weight:
        fixed_bag_weight = st.number_input(
            "Fixed weight per checked bag (kg)",
            min_value=0.0,
            max_value=40.0,
            value=float(app_cfg.get("default_checked_bag_weight_kg", 23)),
            step=1.0,
        )
    else:
        st.info("Airline-max mode: each offer is evaluated at that itinerary's maximum allowed checked-bag weight.")

    refresh = st.button("Search / refresh now", type="primary")
    use_existing = st.button("Load local cache only")

if refresh:
    flights_df, rail_df, messages = refresh_data_cached(
        dep_date,
        window_days,
        adults,
        cabin,
        checked_bags,
        fixed_bag_weight,
        use_airline_max_weight,
    )
else:
    flights_df, rail_df = local_cache()
    messages = ["Loaded local cache. Press Search / refresh now to query enabled providers."]

flights_df, rail_df = normalise_df(flights_df, rail_df)
flights_df, rail_df, display_currency, gbp_to_display_rate = _add_display_columns(
    flights_df,
    rail_df,
    app_cfg=app_cfg,
    adults=int(adults),
    cabin=str(cabin),
)

st.subheader("Status")
with st.expander("Source messages", expanded=False):
    for msg in messages:
        st.write("•", msg)
if display_currency != "GBP":
    st.caption(
        f"Display currency: {display_currency}. Any GBP-denominated estimates (baggage model and rail fallback) "
        f"are converted using 1 GBP = {gbp_to_display_rate:,.2f} {display_currency}."
    )

# KPI cards
c1, c2, c3, c4, c5 = st.columns(5)
if not flights_df.empty:
    feasible_flights = flights_df.copy()
    if "baggage_feasible" in feasible_flights.columns:
        feasible_flights = feasible_flights[feasible_flights["baggage_feasible"]]
    feasible_flights = feasible_flights.replace([float("inf"), -float("inf")], pd.NA).dropna(subset=["estimated_total_display"])
    if not feasible_flights.empty:
        best_flight = feasible_flights.sort_values("estimated_total_display", na_position="last").iloc[0]
        c1.metric("Best flight incl. baggage est.", _format_money(best_flight["estimated_total_display"], display_currency))
        c2.metric("Base flight price", _format_money(best_flight["price_display"], display_currency))
        c3.metric("Baggage estimate", _format_money(best_flight.get("estimated_baggage_extra_display", 0), display_currency))
        c4.metric("Checked bags total", f"{int(best_flight.get('requested_checked_bags_total', adults * checked_bags))}")
        c5.metric("Per-bag target", f"{best_flight.get('requested_checked_weight_kg', 0):.0f} kg")
    else:
        c1.metric("Best flight incl. baggage est.", "No feasible data")
        c2.metric("Base flight price", "—")
        c3.metric("Baggage estimate", "—")
        c4.metric("Checked bags total", adults * checked_bags)
        c5.metric("Per-bag target", "airline max")
else:
    c1.metric("Best flight incl. baggage est.", "No data")
    c2.metric("Base flight price", "—")
    c3.metric("Baggage estimate", "—")
    c4.metric("Checked bags total", adults * checked_bags)
    c5.metric("Per-bag target", "airline max" if use_airline_max_weight else f"{fixed_bag_weight:.0f} kg")

st.divider()

left, right = st.columns([2, 1])
with left:
    st.subheader("Flight offers")
    if flights_df.empty:
        st.info(
            "No flight offers yet. Enable and configure at least one API source in sources.yaml and .env, "
            "then press Search / refresh now."
        )
    else:
        view_cols = [
            "source",
            "airline",
            "airline_code",
            "depart_at",
            "arrive_at",
            "stops",
            "route",
            "cabin",
            "baggage_policy",
            "baggage_feasible",
            "requested_checked_bags_total",
            "requested_checked_weight_kg",
            "airline_max_checked_weight_kg",
            "included_checked_bags",
            "included_checked_weight_kg",
            "extra_bags_total",
            "overweight_bags_total",
            "currency",
            "price_display",
            "estimated_baggage_extra_display",
            "estimated_total_display",
            "search_link",
            "fetched_at",
        ]
        existing_cols = [c for c in view_cols if c in flights_df.columns]
        table_df = flights_df[existing_cols].sort_values("estimated_total_display", na_position="last")
        st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "price_display": st.column_config.NumberColumn(
                    f"Base fare ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                ),
                "estimated_baggage_extra_display": st.column_config.NumberColumn(
                    f"Baggage estimate ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                ),
                "estimated_total_display": st.column_config.NumberColumn(
                    f"Estimated total ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                ),
                "search_link": st.column_config.LinkColumn("Search flight page"),
            },
        )

        with st.expander("Baggage calculation notes per offer", expanded=False):
            note_cols = [c for c in ["airline", "route", "baggage_note"] if c in flights_df.columns]
            if note_cols:
                st.dataframe(flights_df[note_cols], use_container_width=True, hide_index=True)

        chart_df = flights_df.copy()
        if "baggage_feasible" in chart_df.columns:
            chart_df = chart_df[chart_df["baggage_feasible"]]
        chart_df = chart_df.replace([float("inf"), -float("inf")], pd.NA).dropna(subset=["estimated_total_display"])
        if not chart_df.empty:
            fig = px.scatter(
                chart_df,
                x="depart_at",
                y="estimated_total_display",
                color="airline",
                size="estimated_baggage_extra_display",
                hover_data=["source", "route", "stops", "price_display", "requested_checked_weight_kg"],
                title=f"Estimated total flight price by departure ({display_currency})",
            )
            st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Rail transfer estimates")
    if rail_df.empty:
        st.info("No rail estimates available.")
    else:
        st.dataframe(
            rail_df[["route_name", "price_display", "duration", "changes", "luggage_score", "notes"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "price_display": st.column_config.NumberColumn(
                    f"Price ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                )
            },
        )
        st.warning(
            "With four very heavy checked bags, the rail segment is mainly a handling problem, not a fare problem. "
            "The Heathrow Express + GWR route may be worth paying for if it reduces crowded-platform transfers."
        )

st.divider()

st.subheader("Combined planning view")
if not flights_df.empty and not rail_df.empty:
    f = flights_df.copy()
    if "baggage_feasible" in f.columns:
        f = f[f["baggage_feasible"]]
    f = f.replace([float("inf"), -float("inf")], pd.NA).dropna(subset=["estimated_total_display"])
    r = rail_df.dropna(subset=["price_display"]).copy()
    if not f.empty and not r.empty:
        best_rail_price = float(r["price_display"].min())
        f["flight_plus_min_rail_display"] = f["estimated_total_display"] + best_rail_price * adults
        cols = [
            "airline",
            "depart_at",
            "route",
            "stops",
            "requested_checked_bags_total",
            "requested_checked_weight_kg",
            "estimated_total_display",
            "flight_plus_min_rail_display",
            "search_link",
        ]
        st.dataframe(
            f[cols].sort_values("flight_plus_min_rail_display", na_position="last").head(20),
            use_container_width=True,
            hide_index=True,
            column_config={
                "estimated_total_display": st.column_config.NumberColumn(
                    f"Flight total ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                ),
                "flight_plus_min_rail_display": st.column_config.NumberColumn(
                    f"Flight + min rail ({display_currency})",
                    format=f"{_money_prefix(display_currency)} %d",
                ),
                "search_link": st.column_config.LinkColumn("Search flight page"),
            },
        )
    else:
        st.info("No feasible flight rows to combine with rail yet.")
else:
    st.info("Combined view appears once flight and rail data are available.")

st.subheader("Implementation notes")
st.markdown(
    """
- `sources.yaml` follows the same idea as the housing app: API/scraping sources are separate from dashboard logic.
- Refresh cadence is controlled by `REFRESH_INTERVAL_HOURS` and defaults to 6 hours.
- Rail supports TransportAPI journey sources and `static_css` selectors as live best-effort inputs.
- Flight searches are **one-way only**: SCL → LHR. The app does not send a return date to flight providers.
- The default departure window is **2026-09-01 to 2026-09-10**, editable from the sidebar with a start/end date selector.
- Default baggage planning is **2 checked bags per person × 2 people** at **23 kg per checked bag**.
- For mixed-carrier itineraries, the app uses the strictest visible baggage limit across the itinerary, then estimates extra-bag and overweight fees unless the provider returns exact ancillary prices.
    """
)
