"""Écriture des fichiers aircraft.json / receiver.json pour tar1090."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from adsb_models import CacheEntry


def aircraft_payload(snapshot: CacheEntry) -> dict[str, Any]:
    """Convertit un snapshot RadarDesk au format dump1090/readsb."""
    age = max(0.0, time.time() - snapshot.fetched_at.timestamp())
    aircraft: list[dict[str, Any]] = []
    for target in snapshot.aircraft:
        item: dict[str, Any] = {
            "hex": target.hex,
            "flight": target.callsign or "",
            "r": target.registration or "",
            "t": target.type or "",
            "lat": target.lat,
            "lon": target.lon,
            "seen": round(age, 2),
            "seen_pos": round(age, 2),
            "type": "adsb_icao",
            "mlat": [],
            "tisb": [],
        }
        if target.on_ground:
            item["alt_baro"] = "ground"
        elif target.alt_baro is not None:
            item["alt_baro"] = target.alt_baro
        if target.gs is not None:
            item["gs"] = target.gs
        if target.track is not None:
            item["track"] = target.track
        if target.squawk:
            item["squawk"] = target.squawk
        aircraft.append(item)
    return {
        "now": int(snapshot.fetched_at.timestamp()),
        "messages": 0,
        "aircraft": aircraft,
    }


def receiver_payload(
    *,
    interval_s: int,
    lat: float,
    lon: float,
    site_name: str,
    max_range_nm: int,
) -> dict[str, Any]:
    return {
        "version": "RadarDesk",
        "refresh": interval_s * 1000,
        "history": 0,
        "lat": lat,
        "lon": lon,
        "site_name": site_name,
        "maxRange": max_range_nm,
        "binCraft": False,
        "zstd": False,
        "reapi": False,
        "dbServer": False,
        "haveTraces": False,
        "readsb": False,
    }


def write_files(
    snapshot: CacheEntry,
    data_dir: Path,
    *,
    interval_s: int,
    lat: float,
    lon: float,
    site_name: str,
    max_range_nm: int,
) -> None:
    """Écrit les deux fichiers de façon atomique."""
    data_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "aircraft.json": aircraft_payload(snapshot),
        "receiver.json": receiver_payload(
            interval_s=interval_s,
            lat=lat,
            lon=lon,
            site_name=site_name,
            max_range_nm=max_range_nm,
        ),
    }
    for name, payload in payloads.items():
        temporary = data_dir / f".{name}.tmp"
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, data_dir / name)
