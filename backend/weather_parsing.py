"""Normalisation des observations METAR / TAF."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from adsb_utils import _to_float


def parse_metar_visibility_sm(raw: str) -> float | None:
    """Extrait une visibilité METAR brute (mètres ou SM) en statute miles."""
    text = raw.upper()
    if "CAVOK" in text:
        return 6.21

    wind = re.search(r"\b(?:\d{3}|VRB)\d{2,3}(?:G\d{2,3})?(?:KT|MPS)\b", text)
    rest = text[wind.end() :] if wind else text

    sm = re.search(r"\b(\d+(?:/\d+)?)\s*SM\b", rest)
    if sm:
        value = sm.group(1)
        try:
            if "/" in value:
                numerator, denominator = value.split("/", 1)
                return round(float(numerator) / float(denominator), 2)
            return round(float(value), 2)
        except (ValueError, ZeroDivisionError):
            return None

    metric = re.search(r"\b(\d{4})(?:NDV)?\b", rest)
    if metric:
        meters = int(metric.group(1))
        if meters >= 9999:
            return 6.21
        return round(meters / 1609.344, 2)
    return None


def derive_flight_category(
    visibility_sm: float | None,
    cloud_layers: list[dict[str, Any]],
) -> str:
    """Déduit la catégorie de vol quand le fournisseur ne la fournit pas."""
    ceilings = [
        cloud["base_ft"]
        for cloud in cloud_layers
        if cloud.get("cover") in {"BKN", "OVC", "VV"}
        and cloud.get("base_ft") is not None
    ]
    ceiling = min(ceilings) if ceilings else None
    if visibility_sm is None and ceiling is None:
        return "UNKNOWN"
    if (visibility_sm is not None and visibility_sm < 1) or (
        ceiling is not None and ceiling < 500
    ):
        return "LIFR"
    if (visibility_sm is not None and visibility_sm < 3) or (
        ceiling is not None and ceiling < 1000
    ):
        return "IFR"
    if (visibility_sm is not None and visibility_sm <= 5) or (
        ceiling is not None and ceiling <= 3000
    ):
        return "MVFR"
    return "VFR"


def clean_metar(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise une observation METAR pour le frontend."""
    clouds = []
    for cloud in raw.get("clouds") or []:
        if not isinstance(cloud, dict):
            continue
        clouds.append(
            {
                "cover": cloud.get("cover"),
                "base_ft": cloud.get("base"),
            }
        )

    visibility = _to_float(raw.get("visib"))
    raw_metar = raw.get("rawOb") or ""
    if visibility is None:
        visibility = parse_metar_visibility_sm(raw_metar)

    category = str(raw.get("fltCat") or "UNKNOWN").upper()
    if category not in {"VFR", "MVFR", "IFR", "LIFR"}:
        category = derive_flight_category(visibility, clouds)

    return {
        "icao": raw.get("icaoId"),
        "name": raw.get("name"),
        "observed": raw.get("reportTime"),
        "flight_category": category,
        "wind_direction": raw.get("wdir"),
        "wind_speed_kt": raw.get("wspd"),
        "wind_gust_kt": raw.get("wgst"),
        "visibility_sm": round(visibility, 2) if visibility is not None else None,
        "temperature_c": raw.get("temp"),
        "dewpoint_c": raw.get("dewp"),
        "altimeter_hpa": raw.get("altim"),
        "weather": raw.get("wxString") or "",
        "clouds": clouds,
        "raw": raw_metar,
    }


def clean_metar_taf(payload: dict[str, Any], fallback_station: str) -> dict[str, Any]:
    """Normalise la réponse de api.metar-taf.com vers le format RadarDesk."""
    metar = payload.get("metar") if isinstance(payload.get("metar"), dict) else {}
    airport = payload.get("airport") if isinstance(payload.get("airport"), dict) else {}

    clouds = []
    for cloud in metar.get("clouds") or []:
        if not isinstance(cloud, dict):
            continue
        cover = cloud.get("cover") or cloud.get("type") or cloud.get("code")
        base = (
            cloud.get("base")
            if cloud.get("base") is not None
            else cloud.get("base_feet")
            if cloud.get("base_feet") is not None
            else cloud.get("height")
        )
        clouds.append({"cover": cover, "base_ft": base})

    ceiling = metar.get("ceiling")
    if ceiling is not None and not any(
        cloud.get("cover") in {"BKN", "OVC", "VV"} for cloud in clouds
    ):
        clouds.append({"cover": "BKN", "base_ft": ceiling})

    observed = metar.get("observed")
    if isinstance(observed, (int, float)):
        observed = (
            datetime.fromtimestamp(float(observed), UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    visibility_m = metar.get("visibility")
    visibility_sm = None
    if isinstance(visibility_m, (int, float)):
        visibility_sm = round(float(visibility_m) / 1609.344, 2)

    wind_dir = metar.get("wind_dir")
    if wind_dir == -1:
        wind_dir = "VRB"

    return {
        "icao": str(
            metar.get("station_id")
            or airport.get("icao")
            or airport.get("id")
            or fallback_station
        ).upper(),
        "name": airport.get("name") or metar.get("station_name"),
        "observed": observed,
        "flight_category": (metar.get("code") or "UNKNOWN").upper(),
        "wind_direction": wind_dir,
        "wind_speed_kt": metar.get("wind_speed"),
        "wind_gust_kt": metar.get("wind_gust"),
        "visibility_sm": visibility_sm,
        "temperature_c": metar.get("temperature"),
        "dewpoint_c": metar.get("dewpoint"),
        "altimeter_hpa": metar.get("qnh"),
        "weather": metar.get("weather") or "",
        "clouds": clouds,
        "raw": metar.get("raw") or "",
    }


def clean_checkwx(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise une réponse METAR décodée CheckWX."""
    clouds = []
    for cloud in raw.get("clouds") or []:
        if not isinstance(cloud, dict):
            continue
        clouds.append(
            {
                "cover": cloud.get("code"),
                "base_ft": cloud.get("feet"),
            }
        )

    wind = raw.get("wind") if isinstance(raw.get("wind"), dict) else {}
    speed = wind.get("speed") if isinstance(wind.get("speed"), dict) else {}
    gust = wind.get("gust") if isinstance(wind.get("gust"), dict) else {}
    direction = wind.get("degrees")
    if direction is None and str(wind.get("direction") or "").upper() == "VRB":
        direction = "VRB"

    conditions = []
    for condition in raw.get("conditions") or []:
        if isinstance(condition, dict) and condition.get("text"):
            conditions.append(condition["text"])
        elif isinstance(condition, str):
            conditions.append(condition)

    station = raw.get("station") if isinstance(raw.get("station"), dict) else {}
    pressure = raw.get("pressure") if isinstance(raw.get("pressure"), dict) else {}
    visibility = (
        raw.get("visibility") if isinstance(raw.get("visibility"), dict) else {}
    )
    temperature = (
        raw.get("temperature") if isinstance(raw.get("temperature"), dict) else {}
    )
    dewpoint = raw.get("dewpoint") if isinstance(raw.get("dewpoint"), dict) else {}

    return {
        "icao": str(raw.get("icao") or "").upper(),
        "name": station.get("name"),
        "observed": raw.get("observed"),
        "flight_category": (raw.get("flight_category") or "UNKNOWN").upper(),
        "wind_direction": direction,
        "wind_speed_kt": speed.get("kts"),
        "wind_gust_kt": gust.get("kts"),
        "visibility_sm": visibility.get("miles"),
        "temperature_c": temperature.get("celsius"),
        "dewpoint_c": dewpoint.get("celsius"),
        "altimeter_hpa": pressure.get("mb"),
        "weather": ", ".join(conditions),
        "clouds": clouds,
        "raw": raw.get("raw_text") or "",
    }
