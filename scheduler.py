from __future__ import annotations

import argparse
from datetime import date, timedelta

from travel_dashboard.config import get_refresh_ttl_seconds, load_env, load_yaml
from travel_dashboard.db import connect, init_db, replace_flights, replace_rail
from travel_dashboard.fetch import search_flights_window, search_rail


def _parse_date_setting(value, fallback: date) -> date:
    if isinstance(value, date):
        return value
    if value in (None, "", "null"):
        return fallback
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return fallback


def _departure_window(search_cfg: dict) -> tuple[date, int]:
    start = _parse_date_setting(
        search_cfg.get("departure_date_start", search_cfg.get("departure_date")),
        date.today(),
    )
    end_raw = search_cfg.get("departure_date_end")
    if end_raw in (None, "", "null"):
        days = max(1, int(search_cfg.get("date_window_days", 10)))
        return start, days
    end = _parse_date_setting(end_raw, start)
    if end < start:
        end = start
    return start, max(1, (end - start).days + 1)


def refresh_once() -> None:
    load_env()
    config = load_yaml("config.yaml")
    baggage_rules = load_yaml("baggage_rules.yaml")
    app_cfg = config.get("app", {})
    search_cfg = config.get("search", {})
    bag_settings = baggage_rules.get("settings", {})

    start, window_days = _departure_window(search_cfg)

    offers, flight_messages = search_flights_window(
        start_date=start,
        days=window_days,
        adults=int(app_cfg.get("default_adults", 2)),
        cabin=str(search_cfg.get("cabin", "ECONOMY")),
        checked_bags_per_person=int(app_cfg.get("default_checked_bags_per_person", bag_settings.get("default_bags_per_person", 2))),
        checked_bag_weight_kg=float(app_cfg.get("default_checked_bag_weight_kg", 32)),
        use_airline_max_weight=bool(app_cfg.get("default_use_airline_max_weight", bag_settings.get("default_use_airline_max_weight", True))),
        return_date=None,
    )
    rail_rows, rail_messages = search_rail(config)

    conn = connect()
    init_db(conn)
    replace_flights(conn, offers)
    replace_rail(conn, rail_rows)
    conn.close()

    print("\n".join(flight_messages + rail_messages))
    print(f"Stored {len(offers)} flight rows and {len(rail_rows)} rail rows.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh SCL → LHR → Bristol travel fares.")
    parser.add_argument("--once", action="store_true", help="Run once and exit.")
    args = parser.parse_args()
    if args.once:
        refresh_once()
        return

    config = load_yaml("config.yaml")
    interval_seconds = get_refresh_ttl_seconds(config)
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:
        raise SystemExit("Install requirements first: pip install -r requirements.txt") from exc
    scheduler = BlockingScheduler()
    scheduler.add_job(refresh_once, "interval", seconds=interval_seconds, next_run_time=None)
    refresh_once()
    scheduler.start()


if __name__ == "__main__":
    main()
