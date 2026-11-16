"""Lecture et normalisation de la télémétrie du récepteur local."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from adsb_utils import iso_utc, utc_now


def local_db_root(local_json_url: str) -> str:
    parsed = urlparse(local_json_url)
    path = parsed.path or "/"
    for suffix in ("/data/aircraft.json", "/aircraft.json"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    else:
        path = path.rsplit("/", 1)[0] if "/" in path else ""
    base_path = path.rstrip("/") + "/"
    if not base_path.startswith("/"):
        base_path = "/" + base_path
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"

def local_stats_payload(
    stats: dict[str, Any], receiver: dict[str, Any]
) -> dict[str, Any]:
    last = stats.get("last1min") if isinstance(stats, dict) else None
    last = last if isinstance(last, dict) else {}
    local = last.get("local") if isinstance(last.get("local"), dict) else {}
    start = last.get("start")
    end = last.get("end")
    try:
        window = max(1.0, float(end) - float(start))
    except (TypeError, ValueError):
        window = 60.0
    messages = int(last.get("messages") or 0)
    max_distance = last.get("max_distance")
    max_distance_nm = (
        round(float(max_distance) / 1852.0, 1)
        if isinstance(max_distance, (int, float))
        else None
    )
    accepted = local.get("accepted")
    if not isinstance(accepted, list):
        accepted = []

    return {
        "enabled": True,
        "source": "local",
        "generated_at": iso_utc(utc_now()),
        "stats_now": stats.get("now"),
        "receiver": {
            "version": receiver.get("version"),
            "refresh_ms": receiver.get("refresh"),
            "lat": receiver.get("lat"),
            "lon": receiver.get("lon"),
            "readsb": bool(receiver.get("readsb")),
            "history": receiver.get("history"),
        },
        "aircraft": {
            "with_position": int(stats.get("aircraft_with_pos") or 0),
            "without_position": int(stats.get("aircraft_without_pos") or 0),
            "by_type": stats.get("aircraft_count_by_type") or {},
        },
        "gain_db": stats.get("gain_db"),
        "estimated_ppm": stats.get("estimated_ppm"),
        "last_minute": {
            "messages": messages,
            "message_rate": round(messages / window, 2),
            "signal": local.get("signal"),
            "noise": local.get("noise"),
            "peak_signal": local.get("peak_signal"),
            "strong_signals": int(local.get("strong_signals") or 0),
            "max_distance_nm": max_distance_nm,
            "position_count": int(last.get("position_count_total") or 0),
            "samples_processed": int(local.get("samples_processed") or 0),
            "modes": int(local.get("modes") or 0),
            "bad": int(local.get("bad") or 0),
            "unknown_icao": int(local.get("unknown_icao") or 0),
            "accepted": accepted,
            "samples_lost": int(local.get("samples_lost") or 0),
            "samples_dropped": int(local.get("samples_dropped") or 0),
        },
    }

async def get_local_json(
    client: httpx.AsyncClient, url: str
) -> tuple[dict[str, Any] | None, int | None]:
    """Lit un JSON du récepteur ; renvoie (payload, statut HTTP)."""
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return None, None
    if response.status_code >= 400:
        return None, response.status_code
    try:
        payload = response.json()
    except ValueError:
        return None, response.status_code
    return (payload if isinstance(payload, dict) else None), response.status_code
