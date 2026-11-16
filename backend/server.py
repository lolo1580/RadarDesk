"""Serveur local ADS-B pour scope radar PPI.

Le serveur joue le rôle de proxy/cache entre le frontend et l'API publique
Airplanes.live. Il normalise les cibles et filtre celles qui n'ont pas de
position. En déploiement Docker, le frontend est servi par un conteneur Nginx
séparé qui relaie /api vers ce service.

Lancement :
    python server.py
    # ou
    uvicorn server:app --host 0.0.0.0 --port 8000

Toutes les valeurs peuvent être surchargées par variables d'environnement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Les modules internes sont importés depuis le dossier backend, que le serveur
# soit lancé par ``python server.py`` ou importé comme paquet par les tests.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from adsb_models import AircraftTarget, CacheEntry
from adsb_utils import (
    haversine_nm,
    iso_utc,
    utc_now,
)
from adsb_utils import (
    normalize_aircraft as _normalize_aircraft,
)
from influx_export import InfluxExporter, InfluxSettings
from local_support import (
    get_local_json,
    local_db_root,
    local_stats_payload,
)
from stats_store import StatsStore
from tar1090_writer import write_files as write_tar1090_data
from weather_parsing import (
    clean_checkwx,
    clean_metar,
    clean_metar_taf,
)

# ============================================================================
# CONFIGURATION PRINCIPALE
# ============================================================================

HOST = os.getenv("ADSB_HOST", "0.0.0.0")
PORT = int(os.getenv("ADSB_PORT", "8000"))

# Position du récepteur / centre du scope et rayon de couverture.
LAT = float(os.getenv("ADSB_LAT", "46.8687"))
LON = float(os.getenv("ADSB_LON", "6.9416"))
DIST_NM = int(os.getenv("ADSB_RADIUS_NM", "50"))
# Portée maximale réellement récupérée en amont. Le frontend peut ensuite
# réduire ou augmenter la distance d'affichage jusqu'à cette valeur sans
# provoquer un nouvel appel API.
MAX_RADIUS_NM = int(os.getenv("ADSB_MAX_RADIUS_NM", "150"))

# Cache : le frontend interroge l'API toutes les 3 secondes.
REFRESH_MS = int(os.getenv("ADSB_REFRESH_MS", "3000"))
CACHE_TTL_S = float(os.getenv("ADSB_CACHE_TTL", "4.0"))
STALE_TTL_S = float(os.getenv("ADSB_STALE_TTL", "120"))
UPSTREAM_FAILURE_BACKOFF_S = float(os.getenv("ADSB_FAILURE_BACKOFF", "5.0"))

# Météo aviation : METAR/TAF et frames radar RainViewer.
METAR_CACHE_TTL_S = float(os.getenv("ADSB_METAR_CACHE_TTL", "600"))
WEATHER_RADAR_CACHE_TTL_S = float(os.getenv("ADSB_WEATHER_RADAR_CACHE_TTL", "120"))
WEATHER_MAX_STATIONS = 120
METAR_PROVIDER = os.getenv("ADSB_METAR_PROVIDER", "aviationweather").strip().lower()
METAR_TAF_API_KEY = os.getenv("METAR_TAF_API_KEY", "").strip()
CHECKWX_API_KEY = os.getenv("CHECKWX_API_KEY", "").strip()
METAR_PROVIDERS = {"aviationweather", "metar_taf", "checkwx"}
RADAR_STYLE = os.getenv("ADSB_RADAR_STYLE", "phosphor").strip().lower()
RADAR_STYLES = {"phosphor", "mono", "atc"}
LOCATION_MODE = os.getenv("ADSB_LOCATION_MODE", "coords").strip().lower()
AIRPORT_ICAO = os.getenv("ADSB_AIRPORT_ICAO", "").strip().upper() or None
LOCATION_MODES = {"coords", "airport"}
if LOCATION_MODE not in LOCATION_MODES:
    raise RuntimeError("ADSB_LOCATION_MODE doit être coords ou airport.")
if LOCATION_MODE == "airport" and not AIRPORT_ICAO:
    raise RuntimeError("ADSB_AIRPORT_ICAO est requis en mode airport.")
if RADAR_STYLE not in RADAR_STYLES:
    raise RuntimeError("ADSB_RADAR_STYLE doit être phosphor, mono ou atc.")
if METAR_PROVIDER not in METAR_PROVIDERS:
    raise RuntimeError(
        "ADSB_METAR_PROVIDER doit être aviationweather, metar_taf ou checkwx."
    )
if METAR_PROVIDER == "metar_taf" and not METAR_TAF_API_KEY:
    raise RuntimeError("METAR_TAF_API_KEY est requis pour la source metar-taf.com.")
if METAR_PROVIDER == "checkwx" and not CHECKWX_API_KEY:
    raise RuntimeError("CHECKWX_API_KEY est requis pour la source CheckWX.")

# Timeout d'un appel upstream (secondes).
UPSTREAM_TIMEOUT_S = float(os.getenv("ADSB_TIMEOUT", "4.0"))

# Airplanes.live reste la source primaire. L'API étant devenue réservée aux
# feeders pour certaines adresses (HTTP 403), adsb.lol sert de secours
# optionnel. Mettre ADSB_FALLBACK_PROVIDER=none pour le désactiver.
PRIMARY_PROVIDER = os.getenv("ADSB_PRIMARY_PROVIDER", "airplanes_live").strip().lower()
FALLBACK_PROVIDER = os.getenv("ADSB_FALLBACK_PROVIDER", "adsb_lol").strip().lower()
PRIMARY_COOLDOWN_S = float(os.getenv("ADSB_PRIMARY_COOLDOWN", "600"))

# Réception locale : le backend peut consommer directement un aircraft.json
# dump1090/readsb exposé par une autre machine du réseau.
RECEPTION_MODE = os.getenv("ADSB_RECEPTION_MODE", "online").strip().lower()
LOCAL_JSON_URL = os.getenv("ADSB_LOCAL_JSON_URL", "").strip()
RECEPTION_MODES = {"online", "local"}
if RECEPTION_MODE not in RECEPTION_MODES:
    raise RuntimeError("ADSB_RECEPTION_MODE doit être online ou local.")
if LOCAL_JSON_URL:
    _local_parsed = urlparse(LOCAL_JSON_URL)
    if _local_parsed.scheme not in {"http", "https"} or not _local_parsed.netloc:
        raise RuntimeError(
            "ADSB_LOCAL_JSON_URL doit commencer par http:// ou https://."
        )

LOG_LEVEL = os.getenv("ADSB_LOG_LEVEL", "INFO").upper()
CORS_ORIGINS_RAW = os.getenv("ADSB_CORS_ORIGINS", "")

# Optionnel : permet de servir le frontend depuis FastAPI en développement.
# En Docker, le frontend est un conteneur Nginx distinct.
STATIC_DIR = Path(os.getenv("ADSB_STATIC_DIR", str(BASE_DIR / "static")))
ASSETS_DIR = STATIC_DIR / "static" if (STATIC_DIR / "static").is_dir() else STATIC_DIR
INDEX_FILE = STATIC_DIR / "index.html"

# Surcharge de configuration gérée par le service d'administration.
RUNTIME_CONFIG_FILE = Path(os.getenv("ADSB_RUNTIME_CONFIG_FILE", "/data/runtime.json"))
INTERNAL_TOKEN = os.getenv("ADSB_INTERNAL_TOKEN", "").strip()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Données JSON au format dump1090/readsb pour l'interface tar1090.
TAR1090_DATA_DIR = Path(os.getenv("ADSB_TAR1090_DATA_DIR", "/tar1090_data"))
TAR1090_WRITE_INTERVAL_S = int(os.getenv("ADSB_TAR1090_WRITE_INTERVAL_S", "10"))
if not 2 <= TAR1090_WRITE_INTERVAL_S <= 60:
    raise RuntimeError(
        "ADSB_TAR1090_WRITE_INTERVAL_S doit être compris entre 2 et 60."
    )

# Cache de la base tar1090 du récepteur local (db2) pour enrichir les
# immatriculations et types absents de son aircraft.json.
_local_db_base = ""
_local_db_folder = ""
_local_db_detected_at = 0.0
_local_db_cache: dict[str, dict[str, Any]] = {}
_local_db_lock = asyncio.Lock()
_local_stats_cache: tuple[float, dict[str, Any]] | None = None
_local_stats_cache_lock = asyncio.Lock()

# Statistiques persistantes de l'antenne (SQLite).
STATS_DB_PATH = Path(os.getenv("ADSB_STATS_DB_PATH", "/stats/adsb-stats.db"))
STATS_ENABLED = _env_flag("ADSB_STATS_ENABLED", True)
STATS_SAMPLE_SECONDS = int(os.getenv("ADSB_STATS_SAMPLE_SECONDS", "30"))
LOCAL_STATS_STALE_S = float(os.getenv("ADSB_LOCAL_STATS_STALE_S", "30"))
LOCAL_STATS_CACHE_S = float(os.getenv("ADSB_LOCAL_STATS_CACHE_S", "3.0"))
STATS_RETENTION_DAYS = int(os.getenv("ADSB_STATS_RETENTION_DAYS", "180"))
STATS_BACKUP_KEEP = int(os.getenv("ADSB_STATS_BACKUP_KEEP", "7"))
# Un nouvel échantillon séparé de plus de ce délai compte comme un nouveau
# passage de l'aéronef, et non comme la continuation du précédent.
STATS_PASS_GAP_SECONDS = int(os.getenv("ADSB_STATS_PASS_GAP_SECONDS", "1800"))
STATS_RANGE_STEPS = (5, 10, 25, 50, 75, 100, 125, 150, 200, 250)
if not 5 <= STATS_SAMPLE_SECONDS <= 3600:
    raise RuntimeError("ADSB_STATS_SAMPLE_SECONDS doit être compris entre 5 et 3600.")
if not 1 <= STATS_RETENTION_DAYS <= 3650:
    raise RuntimeError("ADSB_STATS_RETENTION_DAYS doit être compris entre 1 et 3650.")
if not 1 <= STATS_BACKUP_KEEP <= 365:
    raise RuntimeError("ADSB_STATS_BACKUP_KEEP doit être compris entre 1 et 365.")
if not 60 <= STATS_PASS_GAP_SECONDS <= 86_400:
    raise RuntimeError(
        "ADSB_STATS_PASS_GAP_SECONDS doit être compris entre 60 et 86400."
    )

stats_store = StatsStore(
    STATS_DB_PATH,
    enabled=STATS_ENABLED,
    sample_seconds=STATS_SAMPLE_SECONDS,
    retention_days=STATS_RETENTION_DAYS,
    backup_keep=STATS_BACKUP_KEEP,
    pass_gap_seconds=STATS_PASS_GAP_SECONDS,
)


def normalize_aircraft(raw_aircraft: list[dict[str, Any]]) -> list[AircraftTarget]:
    """Normalise les cibles avec le centre et la portée courants."""
    return _normalize_aircraft(raw_aircraft, LAT, LON, MAX_RADIUS_NM)

# Export InfluxDB v2 (optionnel, surchargeable dans l'administration).
INFLUX_ENABLED = _env_flag("ADSB_INFLUX_ENABLED", False)
INFLUX_URL = os.getenv("ADSB_INFLUX_URL", "").strip().rstrip("/")
INFLUX_ORG = os.getenv("ADSB_INFLUX_ORG", "").strip()
INFLUX_BUCKET = os.getenv("ADSB_INFLUX_BUCKET", "").strip()
INFLUX_TOKEN = os.getenv("ADSB_INFLUX_TOKEN", "").strip()
INFLUX_MEASUREMENT = os.getenv("ADSB_INFLUX_MEASUREMENT", "adsb_stats").strip()
INFLUX_INTERVAL_S = int(os.getenv("ADSB_INFLUX_INTERVAL_S", "30"))
if not 5 <= INFLUX_INTERVAL_S <= 3600:
    raise RuntimeError("ADSB_INFLUX_INTERVAL_S doit être compris entre 5 et 3600.")
if INFLUX_ENABLED:
    parsed_influx = urlparse(INFLUX_URL)
    if (
        not INFLUX_URL
        or parsed_influx.scheme not in {"http", "https"}
        or not parsed_influx.netloc
        or not INFLUX_ORG
        or not INFLUX_BUCKET
        or not INFLUX_TOKEN
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", INFLUX_MEASUREMENT)
    ):
        raise RuntimeError(
            "Configuration InfluxDB incomplète: URL, org, bucket, token et mesure valides requis."
        )

influx_exporter = InfluxExporter(
    InfluxSettings(
        enabled=INFLUX_ENABLED,
        url=INFLUX_URL,
        org=INFLUX_ORG,
        bucket=INFLUX_BUCKET,
        token=INFLUX_TOKEN,
        measurement=INFLUX_MEASUREMENT,
        airport_icao=AIRPORT_ICAO,
        location_mode=LOCATION_MODE,
    )
)

# ============================================================================
# FOURNISSEURS
# ============================================================================


@dataclass(frozen=True, slots=True)
class Provider:
    key: str
    label: str
    url_template: str


PROVIDERS: dict[str, Provider] = {
    "airplanes_live": Provider(
        key="airplanes_live",
        label="airplanes.live",
        url_template="https://api.airplanes.live/v2/point/{lat}/{lon}/{dist}",
    ),
    "adsb_lol": Provider(
        key="adsb_lol",
        label="adsb.lol",
        url_template="https://api.adsb.lol/v2/point/{lat}/{lon}/{dist}",
    ),
}


def _resolve_provider(name: str) -> Provider | None:
    name = (name or "").strip().lower()
    if name in {"", "none", "off", "disabled"}:
        return None
    provider = PROVIDERS.get(name)
    if provider is None:
        allowed = ", ".join(PROVIDERS)
        raise RuntimeError(
            f"Fournisseur ADS-B inconnu: {name!r}. Valeurs possibles: {allowed}, none"
        )
    return provider


PRIMARY = _resolve_provider(PRIMARY_PROVIDER)
FALLBACK = _resolve_provider(FALLBACK_PROVIDER)
if PRIMARY is None:
    raise RuntimeError("ADSB_PRIMARY_PROVIDER ne peut pas être désactivé.")
if FALLBACK is not None and FALLBACK.key == PRIMARY.key:
    FALLBACK = None

if not -90.0 <= LAT <= 90.0:
    raise RuntimeError("ADSB_LAT doit être compris entre -90 et 90.")
if not -180.0 <= LON <= 180.0:
    raise RuntimeError("ADSB_LON doit être compris entre -180 et 180.")
if not 1 <= DIST_NM <= 250:
    raise RuntimeError("ADSB_RADIUS_NM doit être compris entre 1 et 250.")
if not DIST_NM <= MAX_RADIUS_NM <= 250:
    raise RuntimeError(
        "ADSB_MAX_RADIUS_NM doit être compris entre ADSB_RADIUS_NM et 250."
    )
if not 500 <= REFRESH_MS <= 60_000:
    raise RuntimeError("ADSB_REFRESH_MS doit être compris entre 500 et 60000.")
if CACHE_TTL_S < 0 or STALE_TTL_S < CACHE_TTL_S:
    raise RuntimeError("Vérifier ADSB_CACHE_TTL et ADSB_STALE_TTL.")

BASELINE_CONFIG: dict[str, Any] = {
    "lat": LAT,
    "lon": LON,
    "dist_nm": DIST_NM,
    "max_radius_nm": MAX_RADIUS_NM,
    "refresh_ms": REFRESH_MS,
    "cache_ttl_s": CACHE_TTL_S,
    "stale_ttl_s": STALE_TTL_S,
    "primary_provider": PRIMARY.key,
    "fallback_provider": FALLBACK.key if FALLBACK else "none",
    "primary_cooldown_s": PRIMARY_COOLDOWN_S,
    "reception_mode": RECEPTION_MODE,
    "local_json_url": LOCAL_JSON_URL,
    "weather_metar_provider": METAR_PROVIDER,
    "radar_style": RADAR_STYLE,
    "location_mode": LOCATION_MODE,
    "airport_icao": AIRPORT_ICAO,
    "influx_enabled": INFLUX_ENABLED,
    "influx_url": INFLUX_URL,
    "influx_org": INFLUX_ORG,
    "influx_bucket": INFLUX_BUCKET,
    "influx_token": INFLUX_TOKEN,
    "influx_measurement": INFLUX_MEASUREMENT,
    "influx_interval_s": INFLUX_INTERVAL_S,
}

CORS_ORIGINS = [item.strip() for item in CORS_ORIGINS_RAW.split(",") if item.strip()]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
# Évite une ligne de log par appel HTTP upstream à chaque rafraîchissement.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("radardesk")

# ============================================================================
# MODÈLE DE DONNÉES
# ============================================================================


# ============================================================================
# ÉTAT MÉMOIRE / CACHE
# ============================================================================

_cache: CacheEntry | None = None
_cache_lock = asyncio.Lock()
_primary_blocked_until = 0.0
_upstream_retry_after = 0.0
_last_upstream_error: str | None = None
_runtime_config_active = False
_metar_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_weather_radar_cache: tuple[float, dict[str, Any]] | None = None
_weather_lock = asyncio.Lock()

# ============================================================================
# CONFIGURATION RUNTIME (ADMINISTRATION)
# ============================================================================


def validate_runtime_config(payload: Any) -> dict[str, Any]:
    """Valide une surcharge de configuration et complète les champs absents."""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise TypeError("La configuration doit être un objet JSON.")

    values: dict[str, Any] = dict(BASELINE_CONFIG)

    def number(name: str) -> float:
        raw = payload.get(name, values[name])
        if isinstance(raw, bool):
            raise TypeError(f"{name} doit être un nombre.")
        try:
            result = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} doit être un nombre.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} doit être un nombre fini.")
        return result

    values["lat"] = number("lat")
    values["lon"] = number("lon")
    values["dist_nm"] = round(number("dist_nm"))
    values["max_radius_nm"] = round(number("max_radius_nm"))
    values["refresh_ms"] = round(number("refresh_ms"))
    values["cache_ttl_s"] = round(number("cache_ttl_s"), 2)
    values["stale_ttl_s"] = round(number("stale_ttl_s"), 2)
    values["primary_cooldown_s"] = round(number("primary_cooldown_s"), 1)

    primary = (
        str(payload.get("primary_provider", values["primary_provider"])).strip().lower()
    )
    fallback = (
        str(payload.get("fallback_provider", values["fallback_provider"]))
        .strip()
        .lower()
    )
    weather_metar = (
        str(payload.get("weather_metar_provider", values["weather_metar_provider"]))
        .strip()
        .lower()
    )
    radar_style = str(payload.get("radar_style", values["radar_style"])).strip().lower()
    location_mode = (
        str(payload.get("location_mode", values["location_mode"])).strip().lower()
    )
    airport_icao = payload.get("airport_icao", values.get("airport_icao"))
    airport_icao = str(airport_icao).strip().upper() if airport_icao else None
    if primary not in PROVIDERS:
        raise ValueError(f"Source primaire inconnue: {primary!r}.")
    if fallback != "none" and fallback not in PROVIDERS:
        raise ValueError(f"Source de secours inconnue: {fallback!r}.")
    if fallback != "none" and fallback == primary:
        raise ValueError(
            "La source de secours doit être différente de la source primaire."
        )
    reception_mode = (
        str(payload.get("reception_mode", values["reception_mode"]))
        .strip()
        .lower()
    )
    local_json_url = str(
        payload.get("local_json_url", values.get("local_json_url", "")) or ""
    ).strip()
    if reception_mode not in RECEPTION_MODES:
        raise ValueError("Le mode de réception doit être online ou local.")
    if reception_mode == "local" and not local_json_url:
        raise ValueError("L'URL aircraft.json est requise en réception locale.")
    if local_json_url:
        parsed_local = urlparse(local_json_url)
        if (
            parsed_local.scheme not in {"http", "https"}
            or not parsed_local.netloc
        ):
            raise ValueError(
                "L'URL de réception locale doit commencer par http:// ou https://."
            )
    if weather_metar not in METAR_PROVIDERS:
        raise ValueError(f"Source METAR inconnue: {weather_metar!r}.")
    if weather_metar == "metar_taf" and not METAR_TAF_API_KEY:
        raise ValueError("METAR_TAF_API_KEY est requis pour metar-taf.com.")
    if weather_metar == "checkwx" and not CHECKWX_API_KEY:
        raise ValueError("CHECKWX_API_KEY est requis pour CheckWX.")
    if radar_style not in RADAR_STYLES:
        raise ValueError(f"Style radar inconnu: {radar_style!r}.")
    if location_mode not in LOCATION_MODES:
        raise ValueError(f"Mode de localisation inconnu: {location_mode!r}.")
    if location_mode == "airport" and not (
        airport_icao and re.fullmatch(r"[A-Z0-9]{4}", airport_icao)
    ):
        raise ValueError("Un code OACI valide est requis en mode airport.")

    influx_raw = payload.get("influx_enabled", values["influx_enabled"])
    if isinstance(influx_raw, bool):
        influx_enabled = influx_raw
    elif isinstance(influx_raw, str) and influx_raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "0",
        "false",
        "no",
        "off",
    }:
        influx_enabled = influx_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        raise ValueError("influx_enabled doit être un booléen.")

    influx_url = str(payload.get("influx_url", values["influx_url"]) or "").strip().rstrip("/")
    influx_org = str(payload.get("influx_org", values["influx_org"]) or "").strip()
    influx_bucket = str(payload.get("influx_bucket", values["influx_bucket"]) or "").strip()
    influx_token = str(payload.get("influx_token", values["influx_token"]) or "").strip()
    if not influx_token:
        influx_token = str(values.get("influx_token") or "").strip()
    influx_measurement = str(
        payload.get("influx_measurement", values["influx_measurement"]) or ""
    ).strip()
    influx_interval_s = round(number("influx_interval_s"))

    if influx_enabled:
        parsed_influx = urlparse(influx_url)
        if parsed_influx.scheme not in {"http", "https"} or not parsed_influx.netloc:
            raise ValueError("L'URL InfluxDB doit commencer par http:// ou https://.")
        if not influx_org:
            raise ValueError("L'organisation InfluxDB est requise.")
        if not influx_bucket:
            raise ValueError("Le bucket InfluxDB est requis.")
        if not influx_token:
            raise ValueError("Le token InfluxDB est requis.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", influx_measurement):
            raise ValueError(
                "La mesure InfluxDB doit contenir uniquement lettres, chiffres, '_', '.' ou '-'."
            )
    if not 5 <= influx_interval_s <= 3600:
        raise ValueError(
            "L'intervalle d'export InfluxDB doit être compris entre 5 et 3600 s."
        )

    if not -90.0 <= values["lat"] <= 90.0:
        raise ValueError("La latitude doit être comprise entre -90 et 90.")
    if not -180.0 <= values["lon"] <= 180.0:
        raise ValueError("La longitude doit être comprise entre -180 et 180.")
    if not 1 <= values["dist_nm"] <= 250:
        raise ValueError("Le rayon par défaut doit être compris entre 1 et 250 NM.")
    if not values["dist_nm"] <= values["max_radius_nm"] <= 250:
        raise ValueError(
            "La portée maximale doit être comprise entre le rayon par défaut et 250 NM."
        )
    if not 500 <= values["refresh_ms"] <= 60_000:
        raise ValueError("Le rafraîchissement doit être compris entre 500 et 60000 ms.")
    if not 0 <= values["cache_ttl_s"] <= 60:
        raise ValueError("Le cache TTL doit être compris entre 0 et 60 s.")
    if not values["cache_ttl_s"] <= values["stale_ttl_s"] <= 3600:
        raise ValueError(
            "Le cache périmé doit être compris entre le cache TTL et 3600 s."
        )
    if not 0 <= values["primary_cooldown_s"] <= 86_400:
        raise ValueError("Le cooldown doit être compris entre 0 et 86400 s.")

    values["primary_provider"] = primary
    values["fallback_provider"] = fallback
    values["reception_mode"] = reception_mode
    values["local_json_url"] = local_json_url
    values["weather_metar_provider"] = weather_metar
    values["radar_style"] = radar_style
    values["location_mode"] = location_mode
    values["airport_icao"] = airport_icao if location_mode == "airport" else None
    values["influx_enabled"] = influx_enabled
    values["influx_url"] = influx_url
    values["influx_org"] = influx_org
    values["influx_bucket"] = influx_bucket
    values["influx_token"] = influx_token
    values["influx_measurement"] = influx_measurement
    values["influx_interval_s"] = influx_interval_s
    return values


def read_runtime_config() -> dict[str, Any] | None:
    """Lit le fichier de surcharge, ou None s'il n'existe pas encore."""
    if not RUNTIME_CONFIG_FILE.is_file():
        return None
    try:
        raw = json.loads(RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Fichier de configuration illisible: {exc}") from exc
    return validate_runtime_config(raw)


def _apply_runtime_config(values: dict[str, Any], *, active: bool) -> None:
    global LAT, LON, DIST_NM, MAX_RADIUS_NM
    global REFRESH_MS, CACHE_TTL_S, STALE_TTL_S, PRIMARY_COOLDOWN_S
    global PRIMARY, FALLBACK, PRIMARY_PROVIDER, FALLBACK_PROVIDER
    global RECEPTION_MODE, LOCAL_JSON_URL
    global METAR_PROVIDER, RADAR_STYLE, LOCATION_MODE, AIRPORT_ICAO
    global INFLUX_ENABLED, INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET
    global INFLUX_TOKEN, INFLUX_MEASUREMENT, INFLUX_INTERVAL_S
    global _cache, _primary_blocked_until, _upstream_retry_after
    global _last_upstream_error, _runtime_config_active
    global _local_db_base, _local_db_folder, _local_db_detected_at
    global _local_stats_cache

    LAT = float(values["lat"])
    LON = float(values["lon"])
    DIST_NM = int(values["dist_nm"])
    MAX_RADIUS_NM = int(values["max_radius_nm"])
    REFRESH_MS = int(values["refresh_ms"])
    CACHE_TTL_S = float(values["cache_ttl_s"])
    STALE_TTL_S = float(values["stale_ttl_s"])
    PRIMARY_COOLDOWN_S = float(values["primary_cooldown_s"])
    PRIMARY_PROVIDER = str(values["primary_provider"])
    FALLBACK_PROVIDER = str(values["fallback_provider"])
    RECEPTION_MODE = str(values.get("reception_mode", "online")).strip().lower()
    LOCAL_JSON_URL = str(values.get("local_json_url", "")).strip()
    if RECEPTION_MODE == "local" and not LOCAL_JSON_URL:
        logger.warning(
            "Réception locale active sans URL aircraft.json configurée."
        )
    METAR_PROVIDER = str(values["weather_metar_provider"])
    RADAR_STYLE = str(values["radar_style"])
    LOCATION_MODE = str(values["location_mode"])
    AIRPORT_ICAO = values["airport_icao"]
    INFLUX_ENABLED = bool(values.get("influx_enabled", False))
    INFLUX_URL = str(values.get("influx_url", "")).strip().rstrip("/")
    INFLUX_ORG = str(values.get("influx_org", "")).strip()
    INFLUX_BUCKET = str(values.get("influx_bucket", "")).strip()
    INFLUX_TOKEN = str(values.get("influx_token", "")).strip()
    INFLUX_MEASUREMENT = str(values.get("influx_measurement", "adsb_stats")).strip()
    INFLUX_INTERVAL_S = int(values.get("influx_interval_s", 30))
    influx_exporter.configure(
        InfluxSettings(
            enabled=INFLUX_ENABLED,
            url=INFLUX_URL,
            org=INFLUX_ORG,
            bucket=INFLUX_BUCKET,
            token=INFLUX_TOKEN,
            measurement=INFLUX_MEASUREMENT,
            airport_icao=AIRPORT_ICAO,
            location_mode=LOCATION_MODE,
        )
    )
    PRIMARY = PROVIDERS[PRIMARY_PROVIDER]
    FALLBACK = PROVIDERS[FALLBACK_PROVIDER] if FALLBACK_PROVIDER != "none" else None
    _cache = None
    _primary_blocked_until = 0.0
    _upstream_retry_after = 0.0
    _last_upstream_error = None
    _local_db_base = ""
    _local_db_folder = ""
    _local_db_detected_at = 0.0
    _local_db_cache.clear()
    _local_stats_cache = None
    _runtime_config_active = active


def apply_runtime_config_from_file() -> bool:
    """Applique la surcharge si elle existe, sinon les valeurs d'environnement."""
    values = read_runtime_config()
    _apply_runtime_config(values or BASELINE_CONFIG, active=values is not None)
    return values is not None


def config_payload(*, admin: bool = False) -> dict[str, Any]:
    """Configuration publique ; les détails Influx sont réservés à l'admin."""
    payload: dict[str, Any] = {
        "lat": LAT,
        "lon": LON,
        "dist_nm": DIST_NM,
        "max_radius_nm": MAX_RADIUS_NM,
        "refresh_ms": REFRESH_MS,
        "cache_ttl_s": CACHE_TTL_S,
        "stale_ttl_s": STALE_TTL_S,
        "timeout_s": UPSTREAM_TIMEOUT_S,
        "primary_provider": PRIMARY.label,
        "fallback_provider": FALLBACK.label if FALLBACK else None,
        "primary_provider_key": PRIMARY.key,
        "fallback_provider_key": FALLBACK.key if FALLBACK else "none",
        "reception_mode": RECEPTION_MODE,
        "primary_cooldown_s": PRIMARY_COOLDOWN_S,
        "weather_metar_provider": METAR_PROVIDER,
        "metar_taf_configured": bool(METAR_TAF_API_KEY),
        "checkwx_configured": bool(CHECKWX_API_KEY),
        "radar_style": RADAR_STYLE,
        "location_mode": LOCATION_MODE,
        "airport_icao": AIRPORT_ICAO,
        "influx_enabled": INFLUX_ENABLED,
        "stats_enabled": stats_available(),
        "stats_configured": STATS_ENABLED,
        "stats_sample_seconds": STATS_SAMPLE_SECONDS,
        "stats_retention_days": STATS_RETENTION_DAYS,
        "config_source": "runtime" if _runtime_config_active else "environment",
    }
    if admin:
        payload.update(
            {
                "influx_url": INFLUX_URL,
                "influx_org": INFLUX_ORG,
                "influx_bucket": INFLUX_BUCKET,
                "influx_measurement": INFLUX_MEASUREMENT,
                "influx_interval_s": INFLUX_INTERVAL_S,
                "influx_token_configured": bool(INFLUX_TOKEN),
                "local_json_url": LOCAL_JSON_URL,
            }
        )
    return payload


try:
    apply_runtime_config_from_file()
except (TypeError, ValueError) as exc:  # pragma: no cover - dépend du volume
    logger.error(
        "Configuration runtime invalide (%s), utilisation des variables d'environnement.",
        exc,
    )
    _apply_runtime_config(BASELINE_CONFIG, active=False)

# ============================================================================
# UTILITAIRES
# ============================================================================


# ============================================================================
# APPELS UPSTREAM
# ============================================================================


class UpstreamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def fetch_provider(
    client: httpx.AsyncClient,
    provider: Provider,
    *,
    fallback_used: bool,
) -> CacheEntry:
    url = provider.url_template.format(
        lat=f"{LAT:.5f}", lon=f"{LON:.5f}", dist=MAX_RADIUS_NM
    )
    started = time.monotonic()

    try:
        response = await client.get(
            url,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "radardesk/1.0 (local monitoring)",
            },
        )
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{provider.label}: délai dépassé") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"{provider.label}: erreur réseau ({exc.__class__.__name__})"
        ) from exc

    latency_ms = (time.monotonic() - started) * 1000.0

    if response.status_code >= 400:
        # Le message 403 d'Airplanes.live est explicite mais long ; on le garde
        # uniquement dans les logs pour ne pas polluer la réponse HTTP locale.
        body_preview = response.text[:300].replace("\n", " ")
        logger.warning(
            "Upstream %s HTTP %s: %s",
            provider.label,
            response.status_code,
            body_preview,
        )
        raise UpstreamError(
            f"{provider.label}: HTTP {response.status_code}",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(f"{provider.label}: réponse JSON invalide") from exc

    raw_aircraft = payload.get("ac") if isinstance(payload, dict) else None
    if not isinstance(raw_aircraft, list):
        raise UpstreamError(f"{provider.label}: champ 'ac' absent ou invalide")

    aircraft = normalize_aircraft(raw_aircraft)
    return CacheEntry(
        aircraft=aircraft,
        source=provider.label,
        fallback_used=fallback_used,
        fetched_monotonic=time.monotonic(),
        fetched_at=utc_now(),
        latency_ms=latency_ms,
    )


async def _detect_local_db(client: httpx.AsyncClient) -> None:
    global _local_db_base, _local_db_folder, _local_db_detected_at
    now = time.monotonic()
    if _local_db_detected_at and now - _local_db_detected_at < 300:
        return
    base = local_db_root(LOCAL_JSON_URL)
    try:
        response = await client.get(base)
    except httpx.HTTPError:
        return
    if response.status_code >= 400:
        return
    match = re.search(r'databaseFolder\s*=\s*"([^"]+)"', response.text)
    folder = match.group(1) if match else ""
    if folder != _local_db_folder:
        _local_db_cache.clear()
    _local_db_base = base
    _local_db_folder = folder
    _local_db_detected_at = now


async def _local_db_file(
    client: httpx.AsyncClient, key: str
) -> dict[str, Any] | None:
    if not _local_db_folder:
        return None
    url = f"{_local_db_base}{_local_db_folder}/{key}.js"
    cached = _local_db_cache.get(url)
    if cached is not None:
        return cached
    async with _local_db_lock:
        cached = _local_db_cache.get(url)
        if cached is not None:
            return cached
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        _local_db_cache[url] = data
        return data


async def _local_metadata(
    client: httpx.AsyncClient, hex_code: str
) -> list[Any] | None:
    code = hex_code.lower()
    for level in range(1, 4):
        bkey = code[:level]
        dkey = code[level:]
        if not bkey or not dkey:
            return None
        data = await _local_db_file(client, bkey)
        if data is None:
            return None
        if dkey in data:
            row = data[dkey]
            return row if isinstance(row, list) and len(row) >= 2 else None
        children = data.get("children")
        if not isinstance(children, list) or f"{bkey}{dkey[:1]}" not in children:
            return None
    return None


async def _enrich_local_aircraft(
    client: httpx.AsyncClient, raw_aircraft: list[Any]
) -> None:
    if not raw_aircraft:
        return
    await _detect_local_db(client)
    if not _local_db_folder:
        return
    for raw in raw_aircraft:
        if not isinstance(raw, dict):
            continue
        hex_code = str(raw.get("hex") or raw.get("icao") or "").strip().lower()
        if not hex_code:
            continue
        need_registration = not raw.get("r")
        need_type = not raw.get("t")
        if not need_registration and not need_type:
            continue
        row = await _local_metadata(client, hex_code)
        if not row:
            continue
        if need_registration and row[0]:
            raw["r"] = str(row[0])
        if need_type and len(row) > 1 and row[1]:
            raw["t"] = str(row[1])


async def fetch_local_snapshot(client: httpx.AsyncClient) -> CacheEntry:
    """Récupère un aircraft.json dump1090/readsb exposé par une réception locale."""
    if not LOCAL_JSON_URL:
        raise UpstreamError("local: URL aircraft.json non configurée")
    started = time.monotonic()
    try:
        response = await client.get(
            LOCAL_JSON_URL,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "radardesk/1.0 (local reception)",
            },
        )
    except httpx.TimeoutException as exc:
        raise UpstreamError("local: délai dépassé") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(
            f"local: erreur réseau ({exc.__class__.__name__})"
        ) from exc

    latency_ms = (time.monotonic() - started) * 1000.0
    if response.status_code >= 400:
        raise UpstreamError(
            f"local: HTTP {response.status_code}",
            status_code=response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError("local: réponse JSON invalide") from exc

    raw_aircraft: Any = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        raw_aircraft = payload.get("aircraft")
        if raw_aircraft is None:
            raw_aircraft = payload.get("ac")
    if not isinstance(raw_aircraft, list):
        raise UpstreamError("local: champ 'aircraft' absent ou invalide")

    # Le JSON brut de readsb ne contient pas toujours l'immatriculation et le
    # type : on les complète depuis la base tar1090 du récepteur local.
    await _enrich_local_aircraft(client, raw_aircraft)
    aircraft = normalize_aircraft(raw_aircraft)
    return CacheEntry(
        aircraft=aircraft,
        source="local",
        fallback_used=False,
        fetched_monotonic=time.monotonic(),
        fetched_at=utc_now(),
        latency_ms=latency_ms,
    )


async def _fetch_local_receiver_stats_uncached(
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    if RECEPTION_MODE != "local":
        return {"enabled": False}
    if not LOCAL_JSON_URL:
        return {"enabled": True, "error": "URL aircraft.json locale non configurée."}
    base = local_db_root(LOCAL_JSON_URL)
    (stats, stats_status), (receiver, _receiver_status) = await asyncio.gather(
        get_local_json(client, f"{base}data/stats.json"),
        get_local_json(client, f"{base}data/receiver.json"),
    )
    if stats is None:
        return {
            "enabled": True,
            "error": (
                f"stats.json indisponible (HTTP {stats_status})."
                if stats_status is not None
                else "Récepteur local injoignable."
            ),
            "receiver": receiver or {},
        }
    return local_stats_payload(stats, receiver or {})


async def fetch_local_receiver_stats(client: httpx.AsyncClient) -> dict[str, Any]:
    """Télémétrie locale avec un cache court partagé entre les endpoints.

    Le récepteur reçoit ainsi au plus une lecture de ``stats.json`` et de
    ``receiver.json`` toutes les ``ADSB_LOCAL_STATS_CACHE_S`` secondes, même si
    le desktop et un widget Homepage interrogent en parallèle.
    """
    global _local_stats_cache
    if LOCAL_STATS_CACHE_S <= 0:
        return await _fetch_local_receiver_stats_uncached(client)
    now = time.monotonic()
    cached = _local_stats_cache
    if cached is not None and now - cached[0] <= LOCAL_STATS_CACHE_S:
        return cached[1]
    async with _local_stats_cache_lock:
        cached = _local_stats_cache
        now = time.monotonic()
        if cached is not None and now - cached[0] <= LOCAL_STATS_CACHE_S:
            return cached[1]
        payload = await _fetch_local_receiver_stats_uncached(client)
        _local_stats_cache = (time.monotonic(), payload)
        return payload


async def fetch_best_snapshot(client: httpx.AsyncClient) -> CacheEntry:
    """Tente la réception locale, puis la source primaire et le secours."""
    global _primary_blocked_until

    if RECEPTION_MODE == "local":
        return await fetch_local_snapshot(client)

    errors: list[str] = []
    now = time.monotonic()

    # La source primaire est temporairement ignorée après un refus d'accès
    # (403) pour ne pas ajouter inutilement de latence toutes les 3 secondes.
    if now >= _primary_blocked_until:
        try:
            return await fetch_provider(client, PRIMARY, fallback_used=False)
        except UpstreamError as exc:
            errors.append(str(exc))
            if exc.status_code in {401, 403} and FALLBACK is not None:
                _primary_blocked_until = now + PRIMARY_COOLDOWN_S
                logger.warning(
                    "Source primaire %s suspendue pendant %.0f s (HTTP %s). "
                    "Bascule sur la source de secours.",
                    PRIMARY.label,
                    PRIMARY_COOLDOWN_S,
                    exc.status_code,
                )
            elif exc.status_code == 429 and FALLBACK is not None:
                _primary_blocked_until = now + min(60.0, PRIMARY_COOLDOWN_S)
                logger.warning("Source primaire %s limitée (HTTP 429).", PRIMARY.label)

    if FALLBACK is not None:
        try:
            snapshot = await fetch_provider(client, FALLBACK, fallback_used=True)
            logger.debug("Données servies par la source de secours %s.", FALLBACK.label)
            return snapshot
        except UpstreamError as exc:
            errors.append(str(exc))

    detail = " | ".join(errors) if errors else "aucune source disponible"
    raise UpstreamError(detail)


_last_tar1090_write = 0.0
_tar1090_write_error_at = 0.0


def write_tar1090_files(snapshot: CacheEntry) -> None:
    global _last_tar1090_write, _tar1090_write_error_at
    try:
        write_tar1090_data(
            snapshot,
            TAR1090_DATA_DIR,
            interval_s=TAR1090_WRITE_INTERVAL_S,
            lat=LAT,
            lon=LON,
            site_name=AIRPORT_ICAO or "RadarDesk",
            max_range_nm=MAX_RADIUS_NM,
        )
        _last_tar1090_write = time.monotonic()
    except OSError as exc:
        now = time.monotonic()
        if now - _tar1090_write_error_at > 300:
            logger.warning("Écriture des données tar1090 impossible: %s", exc)
            _tar1090_write_error_at = now


def maybe_write_tar1090(snapshot: CacheEntry) -> None:
    if time.monotonic() - _last_tar1090_write < 1.5:
        return
    write_tar1090_files(snapshot)


async def get_snapshot(client: httpx.AsyncClient) -> tuple[CacheEntry, bool, float]:
    """Retourne (snapshot, stale, age_secondes) avec un cache anti-rafale."""
    global _cache, _last_upstream_error, _upstream_retry_after

    now = time.monotonic()
    cached = _cache
    if cached is not None:
        maybe_write_tar1090(cached)
        age = now - cached.fetched_monotonic
        if age < CACHE_TTL_S:
            return cached, False, age
        if now < _upstream_retry_after and age <= STALE_TTL_S:
            return cached, True, age

    async with _cache_lock:
        # Un autre client a pu rafraîchir le cache pendant l'attente du verrou.
        now = time.monotonic()
        cached = _cache
        if cached is not None:
            age = now - cached.fetched_monotonic
            if age < CACHE_TTL_S:
                return cached, False, age
            if now < _upstream_retry_after and age <= STALE_TTL_S:
                return cached, True, age

        try:
            fresh = await fetch_best_snapshot(client)
            _cache = fresh
            maybe_write_tar1090(fresh)
            _upstream_retry_after = 0.0
            _last_upstream_error = None
            logger.info(
                "MAJ ADS-B: %d cible(s) via %s%s en %.0f ms",
                len(fresh.aircraft),
                fresh.source,
                " (secours)" if fresh.fallback_used else "",
                fresh.latency_ms,
            )
            return fresh, False, 0.0
        except UpstreamError as exc:
            _last_upstream_error = str(exc)
            _upstream_retry_after = time.monotonic() + UPSTREAM_FAILURE_BACKOFF_S
            logger.error("Échec de récupération ADS-B: %s", exc)

            if cached is not None:
                age = time.monotonic() - cached.fetched_monotonic
                if age <= STALE_TTL_S:
                    logger.warning(
                        "Service du cache périmé (%0.1f s) après échec upstream.",
                        age,
                    )
                    return cached, True, age
            raise


# ============================================================================
# STATISTIQUES PERSISTANTES (SQLITE) ET EXPORT INFLUXDB
# ============================================================================

_stats_summary_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_stats_summary_lock = asyncio.Lock()
_STATS_SUMMARY_CACHE_TTL_S = 5.0


def stats_available() -> bool:
    return stats_store.available()


def _stats_scope() -> str:
    return f"{LAT:.2f},{LON:.2f}"


def _stats_ranges() -> list[int]:
    ranges = sorted({*STATS_RANGE_STEPS, MAX_RADIUS_NM})
    return [value for value in ranges if 1 <= value <= MAX_RADIUS_NM]


def _effective_stats_range(requested: int | None = None) -> int:
    base = DIST_NM if requested is None else requested
    target = max(1, min(MAX_RADIUS_NM, base))
    for value in _stats_ranges():
        if value >= target:
            return value
    ranges = _stats_ranges()
    return ranges[-1] if ranges else target


def init_stats_db() -> bool:
    return stats_store.init()


def stats_health() -> dict[str, Any]:
    return stats_store.health()


def record_stats_snapshot(snapshot: CacheEntry) -> dict[str, Any] | None:
    return stats_store.record(
        snapshot,
        lat=LAT,
        lon=LON,
        max_radius_nm=MAX_RADIUS_NM,
        scope=_stats_scope(),
        ranges=_stats_ranges(),
        default_range=_effective_stats_range(),
    )


def stats_summary(
    day: str,
    days: int,
    minutes: int,
    requested_range: int | None = None,
    registration_days: int = 1,
) -> dict[str, Any]:
    return stats_store.summary(
        day,
        days,
        minutes,
        _effective_stats_range(requested_range),
        registration_days,
        _stats_scope(),
        _stats_ranges(),
    )


def reset_stats_db() -> int:
    return stats_store.reset()


async def cached_stats_summary(
    day: str,
    days: int,
    minutes: int,
    requested_range: int | None = None,
    registration_days: int = 1,
) -> dict[str, Any]:
    scope = _stats_scope()
    key = (day, days, minutes, requested_range, registration_days, scope)
    now = time.monotonic()
    async with _stats_summary_lock:
        cached = _stats_summary_cache.get(key)
        if cached is not None and now - cached[0] <= _STATS_SUMMARY_CACHE_TTL_S:
            return cached[1]
    result = await asyncio.to_thread(
        stats_summary, day, days, minutes, requested_range, registration_days
    )
    async with _stats_summary_lock:
        _stats_summary_cache[key] = (time.monotonic(), result)
        if len(_stats_summary_cache) > 64:
            _stats_summary_cache.clear()
    return result


def clear_stats_summary_cache() -> None:
    _stats_summary_cache.clear()


async def export_influx_record(
    client: httpx.AsyncClient, record: dict[str, Any]
) -> bool:
    return await influx_exporter.export(
        client,
        record,
        _effective_stats_range(record.get("default_range_nm")),
    )


async def test_influx_connection(client: httpx.AsyncClient) -> dict[str, Any]:
    return await influx_exporter.test(client)


async def tar1090_writer_loop(app: FastAPI) -> None:
    while True:
        try:
            snapshot, _stale, _age = await get_snapshot(app.state.http)
            write_tar1090_files(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - boucle réseau
            logger.debug("Écriture des données tar1090: %s", exc)
        await asyncio.sleep(TAR1090_WRITE_INTERVAL_S)


async def stats_collector_loop(app: FastAPI) -> None:
    last_influx = 0.0
    while True:
        try:
            snapshot, stale, _age = await get_snapshot(app.state.http)
            if not stale:
                record = await asyncio.to_thread(record_stats_snapshot, snapshot)
                if record and INFLUX_ENABLED:
                    now = time.monotonic()
                    if now - last_influx >= INFLUX_INTERVAL_S and (
                        await export_influx_record(app.state.http, record)
                    ):
                        last_influx = now
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - boucle réseau
            logger.warning("Collecteur de statistiques: %s", exc)
        await asyncio.sleep(STATS_SAMPLE_SECONDS)


# ============================================================================
# RÉPONSES HTTP
# ============================================================================


def apply_cache_headers(
    response: Response,
    entry: CacheEntry,
    *,
    stale: bool,
    age_s: float,
    range_nm: float,
) -> None:
    """Expose les métadonnées utiles au scope via des en-têtes HTTP."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Data-Stale"] = "1" if stale else "0"
    response.headers["X-Upstream"] = entry.source
    response.headers["X-Fallback"] = "1" if entry.fallback_used else "0"
    response.headers["X-Cache-Age"] = f"{max(0.0, age_s):.2f}"
    response.headers["X-Upstream-Latency-Ms"] = f"{entry.latency_ms:.0f}"
    response.headers["X-Last-Success"] = iso_utc(entry.fetched_at)
    response.headers["X-Reception-Mode"] = RECEPTION_MODE
    response.headers["X-Refresh-Ms"] = str(REFRESH_MS)
    response.headers["X-Range-NM"] = f"{range_nm:g}"
    response.headers["X-Max-Range-NM"] = str(MAX_RADIUS_NM)
    response.headers["X-Default-Range-NM"] = str(DIST_NM)
    response.headers["X-Center-Lat"] = f"{LAT:.6f}"
    response.headers["X-Center-Lon"] = f"{LON:.6f}"
    response.headers["X-Radar-Style"] = RADAR_STYLE
    response.headers["X-Location-Mode"] = LOCATION_MODE
    response.headers["X-Airport-ICAO"] = AIRPORT_ICAO or ""


# ============================================================================
# APPLICATION FASTAPI
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(UPSTREAM_TIMEOUT_S, connect=min(3.0, UPSTREAM_TIMEOUT_S))
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=3)
    app.state.http = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    )
    logger.info(
        "Scope ADS-B prêt — centre %.5f/%.5f, portée max %d NM (défaut %d NM), source %s%s",
        LAT,
        LON,
        MAX_RADIUS_NM,
        DIST_NM,
        PRIMARY.label,
        f" (secours: {FALLBACK.label})" if FALLBACK else "",
    )
    stats_task: asyncio.Task[None] | None = None
    tar1090_task: asyncio.Task[None] | None = None
    try:
        stats_ok = await asyncio.to_thread(init_stats_db)
        if stats_ok:
            stats_task = asyncio.create_task(stats_collector_loop(app))
            logger.info(
                "Collecteur de statistiques actif (échantillon %ds, rétention %dj).",
                STATS_SAMPLE_SECONDS,
                STATS_RETENTION_DAYS,
            )
        tar1090_task = asyncio.create_task(tar1090_writer_loop(app))
        logger.info(
            "Écriture tar1090 active (intervalle %ds).", TAR1090_WRITE_INTERVAL_S
        )
        yield
    finally:
        for task in (stats_task, tar1090_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await app.state.http.aclose()


app = FastAPI(
    title="RadarDesk ADS-B Radar",
    version="1.0.0",
    description=(
        "Proxy/cache local pour un scope radar PPI Canvas. "
        "Source primaire: Airplanes.live, source de secours optionnelle: adsb.lol."
    ),
    lifespan=lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "X-Data-Stale",
            "X-Upstream",
            "X-Fallback",
            "X-Cache-Age",
            "X-Upstream-Latency-Ms",
            "X-Last-Success",
            "X-Reception-Mode",
            "X-Refresh-Ms",
            "X-Range-NM",
            "X-Max-Range-NM",
            "X-Default-Range-NM",
            "X-Center-Lat",
            "X-Center-Lon",
            "X-Radar-Style",
            "X-Location-Mode",
            "X-Airport-ICAO",
        ],
    )

if INDEX_FILE.is_file():

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(INDEX_FILE, media_type="text/html")

else:

    @app.get("/", include_in_schema=False)
    async def backend_info() -> dict[str, str]:
        return {
            "service": "radardesk-backend",
            "health": "/api/health",
            "aircraft": "/api/aircraft",
            "docs": "/docs",
        }


@app.get("/api/config", tags=["radar"])
async def api_config() -> dict[str, Any]:
    """Configuration publique consommée par le frontend."""
    return config_payload()


@app.get("/api/stats/summary", tags=["stats"])
async def api_stats_summary(
    day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    days: int = Query(default=7, ge=1, le=90),
    minutes: int = Query(default=60, ge=10, le=1440),
    range_nm: int | None = Query(default=None, ge=1, le=250),
    registration_days: int = Query(default=1, ge=1, le=3650),
) -> dict[str, Any]:
    """Résumé des statistiques persistantes pour le jour et la portée actifs."""
    requested_day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    requested_range = _effective_stats_range(range_nm)
    try:
        datetime.strptime(requested_day, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Date invalide.") from exc
    if not stats_available():
        return {
            "enabled": False,
            "day": requested_day,
            "timezone": "UTC",
            "scope": _stats_scope(),
            "range_nm": requested_range,
            "available_ranges": _stats_ranges(),
            "samples": 0,
            "observations": 0,
            "peak": 0,
            "average": 0.0,
            "unique": 0,
            "registrations": 0,
            "unique_period": 0,
            "registrations_period": 0,
            "registration_passes": 0,
            "registration_days": int(registration_days),
            "top_registrations": [],
            "first_ts": None,
            "last_ts": None,
            "last": None,
            "hours": [],
            "recent": [],
            "daily": [],
            "sample_seconds": STATS_SAMPLE_SECONDS,
            "retention_days": STATS_RETENTION_DAYS,
            "reason": stats_store.db_error if STATS_ENABLED else "disabled",
        }
    try:
        return await cached_stats_summary(
            requested_day,
            days,
            minutes,
            requested_range,
            registration_days,
        )
    except (OSError, sqlite3.Error) as exc:
        logger.error("Lecture des statistiques impossible: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Base de statistiques temporairement indisponible.",
        ) from exc


@app.get("/api/local/stats", tags=["stats"])
async def api_local_stats(request: Request, response: Response) -> dict[str, Any]:
    """Télémétrie du récepteur local (readsb/dump1090 stats.json)."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return await fetch_local_receiver_stats(request.app.state.http)


@app.get("/api/widgets/receiver", tags=["public"])
@app.get("/api/public/receiver", include_in_schema=False)
async def api_receiver_widget(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Télémétrie compacte du récepteur local pour un widget Homepage."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    payload = await fetch_local_receiver_stats(request.app.state.http)
    now = utc_now()
    enabled = bool(payload.get("enabled"))
    error = payload.get("error")
    last = payload.get("last_minute") or {}
    receiver = payload.get("receiver") or {}
    aircraft = payload.get("aircraft") or {}
    stats_now = payload.get("stats_now")
    age_s: float | None = None
    if isinstance(stats_now, (int, float)):
        age_s = max(0.0, time.time() - float(stats_now))
    if not enabled:
        status = "disabled"
    elif error:
        status = "error"
    elif age_s is not None and age_s > LOCAL_STATS_STALE_S:
        status = "degraded"
    else:
        status = "ok"

    accepted = last.get("accepted")
    accepted_text = (
        " / ".join(str(item) for item in accepted)
        if isinstance(accepted, list) and accepted
        else None
    )
    last_update = (
        iso_utc(datetime.fromtimestamp(float(stats_now), UTC))
        if isinstance(stats_now, (int, float))
        else None
    )

    return {
        "version": 1,
        "generated_at": iso_utc(now),
        "timestamp": int(now.timestamp()),
        "status": status,
        "online": status == "ok",
        "enabled": enabled,
        "source": payload.get("source"),
        "error": error,
        "cache_age_s": round(age_s, 1) if age_s is not None else None,
        "last_update": last_update,
        "receiver_version": receiver.get("version"),
        "receiver_version_short": (
            str(receiver.get("version")).split(" ")[0]
            if receiver.get("version")
            else None
        ),
        "receiver_readsb": bool(receiver.get("readsb")),
        "receiver_refresh_ms": receiver.get("refresh_ms"),
        "center_lat": receiver.get("lat"),
        "center_lon": receiver.get("lon"),
        "signal_dbfs": last.get("signal"),
        "noise_dbfs": last.get("noise"),
        "peak_signal_dbfs": last.get("peak_signal"),
        "strong_signals": int(last.get("strong_signals") or 0),
        "messages_last_minute": int(last.get("messages") or 0),
        "message_rate": float(last.get("message_rate") or 0.0),
        "position_count": int(last.get("position_count") or 0),
        "max_range_nm": last.get("max_distance_nm"),
        "gain_db": payload.get("gain_db"),
        "estimated_ppm": payload.get("estimated_ppm"),
        "samples_processed": int(last.get("samples_processed") or 0),
        "samples_lost": int(last.get("samples_lost") or 0),
        "samples_dropped": int(last.get("samples_dropped") or 0),
        "modes": int(last.get("modes") or 0),
        "bad": int(last.get("bad") or 0),
        "unknown_icao": int(last.get("unknown_icao") or 0),
        "accepted": accepted_text,
        "aircraft_with_position": int(aircraft.get("with_position") or 0),
        "aircraft_without_position": int(aircraft.get("without_position") or 0),
    }


@app.get("/api/widgets/homepage", tags=["public"])
@app.get("/api/public/summary", include_in_schema=False)
async def api_homepage_widget(
    response: Response,
    range_nm: int | None = Query(default=None, ge=1, le=250),
) -> dict[str, Any]:
    """Métriques compactes et stables pour un widget Homepage ou un dashboard."""
    response.headers["Cache-Control"] = "no-store, max-age=0"
    requested_range = _effective_stats_range(range_nm)
    snapshot = _cache
    now_monotonic = time.monotonic()
    now_epoch = int(time.time())
    current_targets: list[AircraftTarget] = []
    cache_age: float | None = None
    status = "starting"
    source: str | None = None
    fallback_used = False
    last_update: str | None = None

    if snapshot is not None:
        cache_age = max(0.0, now_monotonic - snapshot.fetched_monotonic)
        status = "ok" if cache_age <= STALE_TTL_S else "degraded"
        source = snapshot.source
        fallback_used = snapshot.fallback_used
        last_update = iso_utc(snapshot.fetched_at)
        current_targets = [
            target
            for target in snapshot.aircraft
            if haversine_nm(LAT, LON, target.lat, target.lon) <= requested_range
        ]

    airborne = sum(1 for target in current_targets if not target.on_ground)
    ground = len(current_targets) - airborne
    emergency = sum(
        1 for target in current_targets if target.squawk in {"7500", "7600", "7700"}
    )

    stats: dict[str, Any] | None = None
    if stats_available():
        try:
            stats = await cached_stats_summary(
                datetime.now(UTC).strftime("%Y-%m-%d"),
                1,
                10,
                requested_range,
            )
        except (OSError, sqlite3.Error) as exc:
            logger.warning("API widget: statistiques indisponibles (%s).", exc)

    if last_update is None and stats and stats.get("last_ts"):
        last_update = iso_utc(
            datetime.fromtimestamp(int(stats["last_ts"]), UTC)
        )

    return {
        "version": 1,
        "generated_at": iso_utc(utc_now()),
        "timestamp": now_epoch,
        "status": status,
        "online": status == "ok",
        "source": source,
        "fallback_used": fallback_used,
        "cache_age_s": round(cache_age, 1) if cache_age is not None else None,
        "last_update": last_update,
        "center_lat": round(LAT, 5),
        "center_lon": round(LON, 5),
        "range_nm": requested_range,
        "max_range_nm": MAX_RADIUS_NM,
        "aircraft": len(current_targets),
        "airborne": airborne,
        "ground": ground,
        "emergency": emergency,
        "stats_enabled": stats_available(),
        "stats_day": datetime.now(UTC).strftime("%Y-%m-%d"),
        "unique_today": int(stats["unique"]) if stats else 0,
        "unique_registrations_today": int(stats["registrations"]) if stats else 0,
        "peak_today": int(stats["peak"]) if stats else 0,
        "contacts_today": int(stats["observations"]) if stats else 0,
        "samples_today": int(stats["samples"]) if stats else 0,
        "average_today": float(stats["average"]) if stats else 0.0,
        "last_sample_at": (
            iso_utc(datetime.fromtimestamp(int(stats["last_ts"]), UTC))
            if stats and stats.get("last_ts")
            else None
        ),
        "last_sample_aircraft": (
            int(stats["last"]["aircraft"])
            if stats and isinstance(stats.get("last"), dict)
            else None
        ),
        "stats_sample_seconds": STATS_SAMPLE_SECONDS,
        "stats_retention_days": STATS_RETENTION_DAYS,
        "influx_enabled": INFLUX_ENABLED,
    }


@app.get(
    "/api/aircraft",
    response_model=list[AircraftTarget],
    tags=["radar"],
    summary="Cibles ADS-B normalisées dans la bulle",
)
async def api_aircraft(
    request: Request,
    response: Response,
    radius_nm: int | None = Query(
        default=None,
        ge=1,
        le=250,
        description="Distance d'affichage demandée, en milles nautiques.",
    ),
) -> list[AircraftTarget]:
    """Liste les aéronefs dans le rayon demandé.

    La portée maximale est récupérée en amont une seule fois par cycle de
    cache ; la distance d'affichage peut ensuite varier sans nouvel appel
    upstream. En cas d'échec upstream, le dernier cache valide est renvoyé avec
    l'en-tête ``X-Data-Stale: 1``. Sans cache valide, une erreur 503 est
    retournée.
    """
    requested_radius = float(radius_nm if radius_nm is not None else DIST_NM)
    requested_radius = min(requested_radius, float(MAX_RADIUS_NM))

    try:
        snapshot, stale, age = await get_snapshot(request.app.state.http)
    except UpstreamError:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={
                "detail": "Flux ADS-B temporairement indisponible.",
                "source": "upstream",
            },
            headers={
                "Cache-Control": "no-store",
                "Retry-After": "3",
                "X-Data-Stale": "1",
            },
        )

    visible_aircraft = [
        target
        for target in snapshot.aircraft
        if haversine_nm(LAT, LON, target.lat, target.lon) <= requested_radius
    ]

    apply_cache_headers(
        response,
        snapshot,
        stale=stale,
        age_s=age,
        range_nm=requested_radius,
    )
    return visible_aircraft


@app.get("/api/health", tags=["diagnostic"])
async def api_health() -> dict[str, Any]:
    """État du cache, sans appel upstream supplémentaire."""
    snapshot = _cache
    if snapshot is None:
        return {
            "status": "starting",
            "source": None,
            "cache_age_s": None,
            "last_success": None,
            "last_error": _last_upstream_error,
            "config_source": "runtime" if _runtime_config_active else "environment",
            "stats_enabled": stats_available(),
            "stats_error": stats_store.db_error,
            **stats_health(),
        }

    age = time.monotonic() - snapshot.fetched_monotonic
    return {
        "status": "ok" if age <= STALE_TTL_S else "degraded",
        "source": snapshot.source,
        "fallback_used": snapshot.fallback_used,
        "cache_age_s": round(age, 2),
        "aircraft_count": len(snapshot.aircraft),
        "last_success": iso_utc(snapshot.fetched_at),
        "last_error": _last_upstream_error,
        "config_source": "runtime" if _runtime_config_active else "environment",
        "max_radius_nm": MAX_RADIUS_NM,
        "stats_enabled": stats_available(),
        "stats_error": stats_store.db_error,
        **stats_health(),
    }


async def fetch_metar_taf_station(
    client: httpx.AsyncClient,
    station: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Récupère un METAR metar-taf.com (l'API ne fait pas de batch)."""
    async with semaphore:
        response = await client.get(
            "https://api.metar-taf.com/metar",
            params={
                "api_key": METAR_TAF_API_KEY,
                "v": "2.3",
                "locale": "fr-FR",
                "id": station,
            },
            headers={"User-Agent": "radardesk/1.0 (local monitoring)"},
        )
    if response.status_code != 200:
        raise ValueError(f"metar-taf.com HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("status"):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise ValueError(f"metar-taf.com: {message or 'réponse invalide'}")
    return clean_metar_taf(payload, station)


@app.get("/api/weather/metar", tags=["weather"])
async def api_weather_metar(
    request: Request, ids: str = Query(default="")
) -> dict[str, Any]:
    """Proxy METAR avec cache par station pour les aéroports demandés."""
    requested: list[str] = []
    for item in ids.split(","):
        station = item.strip().upper()
        if re.fullmatch(r"[A-Z0-9]{3,5}", station) and station not in requested:
            requested.append(station)
    requested = requested[:WEATHER_MAX_STATIONS]

    if not requested:
        return {"stations": {}, "stale": False, "source": "aviationweather.gov"}

    now = time.monotonic()
    stations: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    async with _weather_lock:
        for station in requested:
            cached = _metar_cache.get(station)
            if cached is not None and now - cached[0] <= METAR_CACHE_TTL_S:
                stations[station] = cached[1]
            else:
                missing.append(station)

        stale = False
        if missing and METAR_PROVIDER == "metar_taf":
            if not METAR_TAF_API_KEY:
                raise HTTPException(
                    status_code=503,
                    detail="METAR_TAF_API_KEY n'est pas configuré.",
                )
            semaphore = asyncio.Semaphore(4)
            results = await asyncio.gather(
                *(
                    fetch_metar_taf_station(request.app.state.http, station, semaphore)
                    for station in missing
                ),
                return_exceptions=True,
            )
            fetched_at = time.monotonic()
            for station, result in zip(missing, results):
                if isinstance(result, Exception):
                    stale = True
                    continue
                _metar_cache[station] = (fetched_at, result)
                stations[station] = result
        elif missing and METAR_PROVIDER == "checkwx":
            if not CHECKWX_API_KEY:
                raise HTTPException(
                    status_code=503,
                    detail="CHECKWX_API_KEY n'est pas configuré.",
                )
            fetched_at = time.monotonic()
            for start in range(0, len(missing), 25):
                chunk = missing[start : start + 25]
                try:
                    response = await request.app.state.http.get(
                        f"https://api.checkwx.com/v2/metar/{','.join(chunk)}/decoded",
                        headers={
                            "X-API-Key": CHECKWX_API_KEY,
                            "User-Agent": "radardesk/1.0 (local monitoring)",
                        },
                    )
                    if response.status_code != 200:
                        stale = True
                        continue
                    payload = response.json()
                    items = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(items, list):
                        stale = True
                        continue
                    for raw in items:
                        if not isinstance(raw, dict):
                            continue
                        cleaned = clean_checkwx(raw)
                        icao = cleaned["icao"]
                        if not icao:
                            continue
                        _metar_cache[icao] = (fetched_at, cleaned)
                        if icao in requested:
                            stations[icao] = cleaned
                except (httpx.HTTPError, ValueError):
                    stale = True
        elif missing:
            try:
                response = await request.app.state.http.get(
                    "https://aviationweather.gov/api/data/metar",
                    params={
                        "ids": ",".join(missing),
                        "format": "json",
                        "hours": "2",
                    },
                    headers={"User-Agent": "radardesk/1.0 (local monitoring)"},
                )
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, list):
                        fetched_at = time.monotonic()
                        for raw in payload:
                            if not isinstance(raw, dict):
                                continue
                            icao = str(raw.get("icaoId") or "").upper()
                            if not icao:
                                continue
                            cleaned = clean_metar(raw)
                            _metar_cache[icao] = (fetched_at, cleaned)
                            if icao in requested:
                                stations[icao] = cleaned
                else:
                    stale = True
            except (httpx.HTTPError, ValueError):
                stale = True

        # Conserve les anciennes observations si le fournisseur est indisponible.
        for station in requested:
            if station not in stations:
                cached = _metar_cache.get(station)
                if cached is not None:
                    stations[station] = cached[1]
                    stale = True

    return {
        "stations": stations,
        "stale": stale,
        "source": {
            "metar_taf": "metar-taf.com",
            "checkwx": "checkwx.com",
        }.get(METAR_PROVIDER, "aviationweather.gov"),
    }


@app.get("/api/weather/radar", tags=["weather"])
async def api_weather_radar(request: Request) -> dict[str, Any]:
    """Métadonnées RainViewer pour la couche de précipitations animée."""
    global _weather_radar_cache

    now = time.monotonic()
    if (
        _weather_radar_cache is not None
        and now - _weather_radar_cache[0] <= WEATHER_RADAR_CACHE_TTL_S
    ):
        return _weather_radar_cache[1]

    async with _weather_lock:
        now = time.monotonic()
        if (
            _weather_radar_cache is not None
            and now - _weather_radar_cache[0] <= WEATHER_RADAR_CACHE_TTL_S
        ):
            return _weather_radar_cache[1]

        try:
            response = await request.app.state.http.get(
                "https://api.rainviewer.com/public/weather-maps.json",
                headers={"User-Agent": "radardesk/1.0 (local monitoring)"},
            )
            response.raise_for_status()
            payload = response.json()
            radar = payload.get("radar") if isinstance(payload, dict) else None
            if not isinstance(radar, dict):
                raise TypeError("Radar RainViewer absent.")

            frames = []
            for frame in (radar.get("past") or [])[-7:]:
                if isinstance(frame, dict) and frame.get("path"):
                    frames.append({"time": frame.get("time"), "path": frame["path"]})
            for frame in (radar.get("nowcast") or [])[:2]:
                if isinstance(frame, dict) and frame.get("path"):
                    frames.append({"time": frame.get("time"), "path": frame["path"]})

            result = {
                "host": payload.get("host"),
                "generated": payload.get("generated"),
                "frames": frames,
                "source": "rainviewer.com",
                "stale": False,
            }
            _weather_radar_cache = (time.monotonic(), result)
            return result
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            if _weather_radar_cache is not None:
                cached = dict(_weather_radar_cache[1])
                cached["stale"] = True
                return cached
            raise HTTPException(
                status_code=502,
                detail=f"Radar météo indisponible: {exc.__class__.__name__}.",
            ) from exc


def _require_internal_token(request: Request) -> None:
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADSB_INTERNAL_TOKEN n'est pas configuré.",
        )
    provided = request.headers.get("X-Internal-Token", "")
    if not secrets.compare_digest(provided, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Jeton interne invalide.")


@app.get("/internal/config", include_in_schema=False)
async def internal_config(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    return config_payload(admin=True)


@app.post("/internal/stats/reset", include_in_schema=False)
async def internal_stats_reset(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    if not stats_available():
        raise HTTPException(
            status_code=503,
            detail="Statistiques persistantes désactivées ou indisponibles.",
        )
    deleted = await asyncio.to_thread(reset_stats_db)
    clear_stats_summary_cache()
    logger.info("Statistiques réinitialisées (%d relevé(s) supprimé(s)).", deleted)
    return {"status": "reset", "deleted": deleted}


@app.post("/internal/influx/test", include_in_schema=False)
async def internal_influx_test(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    return await test_influx_connection(request.app.state.http)


@app.post("/internal/source/test", include_in_schema=False)
async def internal_source_test(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    try:
        snapshot = await fetch_best_snapshot(request.app.state.http)
    except UpstreamError as exc:
        return {"ok": False, "detail": str(exc)}
    return {
        "ok": True,
        "source": snapshot.source,
        "aircraft": len(snapshot.aircraft),
        "latency_ms": round(snapshot.latency_ms, 1),
        "reception_mode": RECEPTION_MODE,
    }


@app.post("/internal/reload", include_in_schema=False)
async def internal_reload(request: Request) -> dict[str, Any]:
    """Recharge la configuration runtime depuis le volume partagé.

    Cet endpoint n'est accessible que depuis le réseau Docker et exige le
    jeton interne partagé avec le service d'administration.
    """
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADSB_INTERNAL_TOKEN n'est pas configuré.",
        )

    provided = request.headers.get("X-Internal-Token", "")
    if not secrets.compare_digest(provided, INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Jeton interne invalide.")

    try:
        active = apply_runtime_config_from_file()
    except (TypeError, ValueError) as exc:
        logger.error("Rechargement de configuration refusé: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    clear_stats_summary_cache()
    logger.info(
        "Configuration runtime rechargée (%s).",
        "fichier runtime" if active else "valeurs d'environnement",
    )
    return {"status": "reloaded", "config": config_payload(admin=True)}


# Les fichiers statiques sont montés après les routes API, uniquement si un
# répertoire frontend est fourni explicitement (mode développement).
if ASSETS_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(ASSETS_DIR)), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL.lower())
