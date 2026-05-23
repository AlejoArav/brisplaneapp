from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from travel_dashboard.models import RailOffer

MONEY_RE = re.compile(r"(?:£|gbp)\s*([0-9]+(?:[.,][0-9]{1,2})?)", flags=re.IGNORECASE)
NUMBER_RE = re.compile(r"([0-9]+(?:[.,][0-9]{1,2})?)")
INTEGER_RE = re.compile(r"\d+")
RAIL_SOURCE_TYPES = {"static_css", "api_national_rail_ojp"}
TRANSPORTAPI_PREFIX = "api_transportapi_"


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    match = NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("total", "amount", "adult", "value", "price"):
            extracted = _extract_price(value.get(key))
            if extracted is not None:
                return extracted
        for child in value.values():
            extracted = _extract_price(child)
            if extracted is not None:
                return extracted
        return None
    if isinstance(value, list):
        for item in value:
            extracted = _extract_price(item)
            if extracted is not None:
                return extracted
        return None
    text = str(value)
    money_match = MONEY_RE.search(text)
    if money_match:
        try:
            return float(money_match.group(1).replace(",", "."))
        except ValueError:
            return None
    return _as_float(text)


def _extract_changes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if "direct" in text or "no change" in text or "non-stop" in text:
        return 0
    match = INTEGER_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _normalise_duration(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        total = int(value)
        hours = total // 60
        minutes = total % 60
        if hours:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", text):
        hh, mm, _ss = text.split(":")
        return f"{int(hh)}h {int(mm):02d}m"
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hh, mm = text.split(":")
        return f"{int(hh)}h {int(mm):02d}m"
    return text


def _route_parts_summary(route_parts: list[dict[str, Any]]) -> str:
    parts = []
    for part in route_parts:
        mode = str(part.get("mode", "")).strip().upper()
        line = str(part.get("line_name", "")).strip()
        if line:
            parts.append(f"{mode}:{line}" if mode else line)
        elif mode:
            parts.append(mode)
    return " + ".join(parts)


def _rail_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for source in sources:
        typ = str(source.get("type", ""))
        if typ.startswith(TRANSPORTAPI_PREFIX) or typ in RAIL_SOURCE_TYPES:
            out.append(source)
    return out


def _timeout_seconds(source: dict[str, Any], default: int = 30) -> int:
    request_cfg = source.get("request", {})
    timeout = request_cfg.get("timeout_seconds", default)
    try:
        return max(5, int(timeout))
    except (TypeError, ValueError):
        return default


def _static_css_allowed(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
        return parser.can_fetch(user_agent, url)
    except Exception:
        # If robots cannot be fetched, allow request but avoid failing hard.
        return True


def _http_headers(source: dict[str, Any]) -> dict[str, str]:
    ua = str(source.get("user_agent", "scl-lhr-bristol-dashboard/0.1 personal-use")).strip()
    return {"User-Agent": ua}


def _transportapi_credentials(source: dict[str, Any]) -> tuple[str, str]:
    creds = source.get("credentials", {})
    app_id = os.getenv(str(creds.get("app_id_env", "TRANSPORTAPI_APP_ID")), "").strip()
    app_key = os.getenv(str(creds.get("app_key_env", "TRANSPORTAPI_APP_KEY")), "").strip()
    return app_id, app_key


def _transportapi_url_and_params(source: dict[str, Any], departure_date: str | None) -> tuple[str, dict[str, Any]]:
    request_cfg = source.get("request", {})
    route = source.get("route", {})
    origin = quote(str(route.get("origin", "heathrowairport")).strip(), safe=",:-._~")
    destination = quote(str(route.get("destination", "bristol temple meads")).strip(), safe=",:-._~")
    mode = str(request_cfg.get("time_mode", "at")).strip().lower()
    dep_time = str(request_cfg.get("departure_time", "09:00")).strip()
    endpoint_template = str(
        request_cfg.get(
            "endpoint_template",
            "https://transportapi.com/v3/uk/public/journey/from/{origin}/to/{destination}.json",
        )
    )
    if mode in {"at", "by"} and departure_date:
        url = endpoint_template.replace(
            ".json",
            f"/{mode}/{departure_date}/{dep_time}.json",
        )
    else:
        url = endpoint_template
    url = url.format(origin=origin, destination=destination, date=departure_date or "", time=dep_time)

    params = {}
    extra_params = request_cfg.get("params", {})
    if isinstance(extra_params, dict):
        params.update(extra_params)
    params.setdefault("app_id", "")
    params.setdefault("app_key", "")
    if request_cfg.get("region"):
        params.setdefault("region", request_cfg.get("region"))
    return url, params


def _transportapi_parse_offer(route: dict[str, Any], source_name: str, index: int) -> RailOffer | None:
    price = _extract_price(route.get("fare"))
    if price is None:
        price = _extract_price(route.get("price"))
    if price is None:
        price = _extract_price(route.get("total"))
    if price is None:
        return None

    route_parts = route.get("route_parts", [])
    summary = ""
    if isinstance(route_parts, list) and route_parts:
        summary = _route_parts_summary([p for p in route_parts if isinstance(p, dict)])
    summary = summary or str(route.get("summary", "")).strip() or f"{source_name} option {index + 1}"

    changes = route.get("changes")
    if changes is None and isinstance(route_parts, list) and route_parts:
        changes = max(0, len(route_parts) - 1)
    changes = _extract_changes(changes)

    return RailOffer(
        source=source_name,
        route_name=summary,
        price_gbp=price,
        duration=_normalise_duration(route.get("duration")),
        changes=changes,
        luggage_score=4,
        notes="Live journey estimate from TransportAPI.",
        deep_link=str(route.get("url") or "") or None,
        fetched_at=_iso_now(),
    )


def _transportapi_rail_offers(source: dict[str, Any], departure_date: str | None) -> list[RailOffer]:
    app_id, app_key = _transportapi_credentials(source)
    if not app_id or not app_key:
        raise ValueError("enabled but missing credentials.")

    url, params = _transportapi_url_and_params(source, departure_date)
    params["app_id"] = app_id
    params["app_key"] = app_key
    response = requests.get(
        url,
        params=params,
        headers=_http_headers(source),
        timeout=_timeout_seconds(source),
    )
    response.raise_for_status()
    payload = response.json()
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not isinstance(routes, list):
        return []
    request_cfg = source.get("request", {})
    max_results = request_cfg.get("max_results", 5)
    try:
        limit = max(1, int(max_results))
    except (TypeError, ValueError):
        limit = 5
    offers: list[RailOffer] = []
    for idx, route in enumerate(routes[:limit]):
        if not isinstance(route, dict):
            continue
        offer = _transportapi_parse_offer(route, source.get("name", "TransportAPI"), idx)
        if offer is not None:
            offers.append(offer)
    return offers


def _tag_text(tag: Any) -> str:
    if tag is None:
        return ""
    try:
        return tag.get_text(" ", strip=True)
    except Exception:
        return str(tag).strip()


def _first_selected_text(scope: Any, selector: str) -> str:
    if not selector:
        return ""
    try:
        tag = scope.select_one(selector)
    except Exception:
        return ""
    return _tag_text(tag)


def _static_css_cards(source: dict[str, Any], soup: BeautifulSoup) -> list[RailOffer]:
    selectors = source.get("selectors", {}) or {}
    card_selector = str(selectors.get("card", "")).strip()
    if not card_selector:
        return []
    try:
        cards = soup.select(card_selector)
    except Exception:
        cards = []
    offers: list[RailOffer] = []
    max_results = source.get("request", {}).get("max_results", 5)
    try:
        limit = max(1, int(max_results))
    except (TypeError, ValueError):
        limit = 5
    for card in cards[:limit]:
        title = _first_selected_text(card, str(selectors.get("title", "")).strip()) or source.get("name", "Rail page")
        price_text = _first_selected_text(card, str(selectors.get("price", "")).strip()) or _tag_text(card)
        price = _extract_price(price_text)
        if price is None:
            continue
        duration = _first_selected_text(card, str(selectors.get("duration", "")).strip())
        changes = _first_selected_text(card, str(selectors.get("changes", "")).strip())
        offers.append(
            RailOffer(
                source=source.get("name", "Static CSS rail source"),
                route_name=title,
                price_gbp=price,
                duration=_normalise_duration(duration),
                changes=_extract_changes(changes),
                luggage_score=4,
                notes="Live page scrape estimate (selectors-based).",
                deep_link=source.get("url"),
                fetched_at=_iso_now(),
            )
        )
    return offers


def _static_css_table_rows(source: dict[str, Any], soup: BeautifulSoup) -> list[RailOffer]:
    selectors = source.get("selectors", {}) or {}
    table_selector = str(selectors.get("fare_table", "table")).strip() or "table"
    try:
        tables = soup.select(table_selector)
    except Exception:
        tables = []
    offers: list[RailOffer] = []
    for table in tables:
        rows = table.select("tr")
        for row in rows:
            ticket_type_selector = str(selectors.get("ticket_type", "")).strip()
            ticket_type = _first_selected_text(row, ticket_type_selector) if ticket_type_selector else _tag_text(row.select_one("th"))
            standard_selector = str(selectors.get("standard_price", "")).strip()
            business_selector = str(selectors.get("business_price", "")).strip()
            standard_text = _first_selected_text(row, standard_selector)
            business_text = _first_selected_text(row, business_selector)
            standard_price = _extract_price(standard_text)
            business_price = _extract_price(business_text)
            if standard_price is None and business_price is None:
                continue
            if standard_price is not None:
                offers.append(
                    RailOffer(
                        source=source.get("name", "Static CSS rail source"),
                        route_name=ticket_type or "Standard fare",
                        price_gbp=standard_price,
                        duration=None,
                        changes=None,
                        luggage_score=4,
                        notes="Live page scrape estimate (table parse).",
                        deep_link=source.get("url"),
                        fetched_at=_iso_now(),
                    )
                )
            if business_price is not None:
                offers.append(
                    RailOffer(
                        source=source.get("name", "Static CSS rail source"),
                        route_name=(ticket_type or "Business fare") + " (Business)",
                        price_gbp=business_price,
                        duration=None,
                        changes=None,
                        luggage_score=4,
                        notes="Live page scrape estimate (table parse).",
                        deep_link=source.get("url"),
                        fetched_at=_iso_now(),
                    )
                )
    return offers


def _static_css_fallback(source: dict[str, Any], soup: BeautifulSoup) -> list[RailOffer]:
    selectors = source.get("selectors", {}) or {}
    price_selector = str(selectors.get("price", "")).strip() or "[class*='price'], [data-test*='price']"
    duration_selector = str(selectors.get("duration", "")).strip()
    try:
        price_nodes = soup.select(price_selector)
    except Exception:
        price_nodes = []
    offers: list[RailOffer] = []
    seen_prices: set[float] = set()
    for node in price_nodes:
        text = _tag_text(node)
        price = _extract_price(text)
        if price is None or price in seen_prices:
            continue
        seen_prices.add(price)
        duration_text = ""
        if duration_selector:
            duration_text = _first_selected_text(node.parent, duration_selector)
        offers.append(
            RailOffer(
                source=source.get("name", "Static CSS rail source"),
                route_name=source.get("name", "Rail fare"),
                price_gbp=price,
                duration=_normalise_duration(duration_text),
                changes=None,
                luggage_score=4,
                notes="Live page scrape estimate (fallback parse).",
                deep_link=source.get("url"),
                fetched_at=_iso_now(),
            )
        )
        if len(offers) >= 5:
            break
    return offers


def _static_css_rail_offers(source: dict[str, Any]) -> list[RailOffer]:
    url = str(source.get("url", "")).strip()
    if not url:
        return []
    headers = _http_headers(source)
    if source.get("respect_robots_txt", False) and not _static_css_allowed(url, headers["User-Agent"]):
        raise ValueError("robots.txt disallows this scrape target for the configured user agent.")
    response = requests.get(url, headers=headers, timeout=_timeout_seconds(source))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    offers = _static_css_cards(source, soup)
    if offers:
        return offers
    offers = _static_css_table_rows(source, soup)
    if offers:
        return offers
    return _static_css_fallback(source, soup)


def _departure_date_from_config(config: dict[str, Any]) -> str | None:
    search_cfg = config.get("search", {})
    start = search_cfg.get("departure_date_start") or search_cfg.get("departure_date")
    if not start:
        return None
    return str(start)


def live_rail_offers(config: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[list[RailOffer], list[str]]:
    rows: list[RailOffer] = []
    messages: list[str] = []
    departure_date = _departure_date_from_config(config)

    for source in _rail_sources(sources):
        source_name = source.get("name", "Unnamed rail source")
        typ = str(source.get("type", "")).strip()
        if typ == "api_national_rail_ojp":
            messages.append(f"Skipped {source_name}: source type {typ!r} requires a formal licence and is not implemented.")
            continue
        try:
            if typ.startswith(TRANSPORTAPI_PREFIX):
                offers = _transportapi_rail_offers(source, departure_date)
            elif typ == "static_css":
                offers = _static_css_rail_offers(source)
            else:
                messages.append(f"Skipped {source_name}: unsupported rail source type {typ!r}.")
                continue
        except ValueError as exc:
            messages.append(f"Skipped {source_name}: {exc}")
            continue
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "HTTP error"
            reason = exc.response.reason if exc.response is not None else "request failed"
            messages.append(f"{source_name} failed: HTTP {status} {reason}")
            continue
        except Exception as exc:
            messages.append(f"{source_name} failed: {exc}")
            continue

        if offers:
            rows.extend(offers)
            messages.append(f"{source_name}: {len(offers)} offers.")
        else:
            messages.append(f"{source_name}: no live offers parsed.")
    return rows, messages


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
