"""Utilitaires géographiques et de normalisation RadarDesk."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from adsb_models import AircraftTarget


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    """Convertit une valeur numérique de l'API en float fini, sinon None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_callsign(value: Any) -> str | None:
    if value is None:
        return None
    text = "".join(char for char in str(value) if char.isprintable())
    text = re.sub(r"\s+", "", text).strip().upper()
    return text[:16] or None


def _clean_type(value: Any) -> str | None:
    if value is None:
        return None
    text = "".join(char for char in str(value) if char.isprintable()).strip().upper()
    return text[:16] or None


def _clean_registration(value: Any) -> str | None:
    if value is None:
        return None
    text = "".join(char for char in str(value) if char.isprintable())
    text = re.sub(r"\s+", "", text).strip().upper()
    if not text or not re.fullmatch(r"[A-Z0-9-]{2,16}", text):
        return None
    return text


def _clean_squawk(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit() and len(text) <= 4:
        return text.zfill(4)
    return None


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en milles nautiques."""
    radius_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return radius_nm * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def normalize_aircraft(
    raw_aircraft: list[dict[str, Any]],
    center_lat: float,
    center_lon: float,
    max_radius_nm: int,
) -> list[AircraftTarget]:
    """Nettoie, filtre et trie les cibles renvoyées par l'API upstream."""
    cleaned: list[tuple[float, AircraftTarget]] = []
    seen: set[str] = set()

    for raw in raw_aircraft:
        if not isinstance(raw, dict):
            continue

        hex_code = str(raw.get("hex") or raw.get("icao") or "").strip().lower()
        if not hex_code or hex_code in seen:
            continue

        lat = _to_float(raw.get("lat"))
        lon = _to_float(raw.get("lon"))
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue

        distance = haversine_nm(center_lat, center_lon, lat, lon)
        if distance > max_radius_nm:
            continue

        alt_raw = raw.get("alt_baro")
        on_ground = (
            isinstance(alt_raw, str) and alt_raw.strip().lower() == "ground"
        ) or bool(raw.get("ground"))
        altitude = _to_float(alt_raw)
        alt_baro = round(altitude) if altitude is not None else None

        gs = _to_float(raw.get("gs"))
        track = _to_float(raw.get("track"))
        # Le cap peut parfois manquer alors que le heading est disponible.
        if track is None:
            track = _to_float(raw.get("true_heading"))
        if track is not None:
            track = round(track % 360.0, 1)

        target = AircraftTarget(
            hex=hex_code,
            callsign=_clean_callsign(raw.get("flight") or raw.get("callsign")),
            registration=_clean_registration(raw.get("r") or raw.get("registration")),
            lat=round(lat, 6),
            lon=round(lon, 6),
            alt_baro=alt_baro,
            gs=round(gs, 1) if gs is not None else None,
            track=track,
            squawk=_clean_squawk(raw.get("squawk")),
            type=_clean_type(raw.get("t") or raw.get("aircraft_type")),
            on_ground=on_ground,
        )
        seen.add(hex_code)
        cleaned.append((distance, target))

    cleaned.sort(key=lambda item: item[0])
    return [target for _, target in cleaned]
