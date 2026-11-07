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
import time
from contextlib import asynccontextmanager, closing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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

LOG_LEVEL = os.getenv("ADSB_LOG_LEVEL", "INFO").upper()
CORS_ORIGINS_RAW = os.getenv("ADSB_CORS_ORIGINS", "*")

BASE_DIR = Path(__file__).resolve().parent
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


# Statistiques persistantes de l'antenne (SQLite).
STATS_DB_PATH = Path(os.getenv("ADSB_STATS_DB_PATH", "/stats/adsb-stats.db"))
STATS_ENABLED = _env_flag("ADSB_STATS_ENABLED", True)
STATS_SAMPLE_SECONDS = int(os.getenv("ADSB_STATS_SAMPLE_SECONDS", "30"))
STATS_RETENTION_DAYS = int(os.getenv("ADSB_STATS_RETENTION_DAYS", "180"))
STATS_RANGE_STEPS = (5, 10, 25, 50, 75, 100, 125, 150, 200, 250)
if not 5 <= STATS_SAMPLE_SECONDS <= 3600:
    raise RuntimeError("ADSB_STATS_SAMPLE_SECONDS doit être compris entre 5 et 3600.")
if not 1 <= STATS_RETENTION_DAYS <= 3650:
    raise RuntimeError("ADSB_STATS_RETENTION_DAYS doit être compris entre 1 et 3650.")

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
if not CORS_ORIGINS:
    CORS_ORIGINS = ["*"]

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


class AircraftTarget(BaseModel):
    """Cible ADS-B normalisée et prête à être affichée sur le scope."""

    hex: str = Field(description="Adresse ICAO 24 bits (hexadécimal).")
    callsign: str | None = Field(default=None, description="Indicatif de vol nettoyé.")
    lat: float = Field(description="Latitude WGS-84.")
    lon: float = Field(description="Longitude WGS-84.")
    alt_baro: int | None = Field(
        default=None,
        description="Altitude barométrique en pieds. Null si au sol ou inconnue.",
    )
    gs: float | None = Field(default=None, description="Vitesse sol en nœuds.")
    track: float | None = Field(default=None, description="Cap sol en degrés (0-360).")
    squawk: str | None = Field(default=None, description="Code transpondeur Mode A.")
    type: str | None = Field(
        default=None, description="Type d'aéronef (ex: A320, B738)."
    )
    on_ground: bool = Field(default=False, description="Aéronef détecté au sol.")


class CacheEntry(BaseModel):
    aircraft: list[AircraftTarget]
    source: str
    fallback_used: bool
    fetched_monotonic: float
    fetched_at: datetime
    latency_ms: float


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
    global METAR_PROVIDER, RADAR_STYLE, LOCATION_MODE, AIRPORT_ICAO
    global INFLUX_ENABLED, INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET
    global INFLUX_TOKEN, INFLUX_MEASUREMENT, INFLUX_INTERVAL_S
    global _cache, _primary_blocked_until, _upstream_retry_after
    global _last_upstream_error, _runtime_config_active

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
    PRIMARY = PROVIDERS[PRIMARY_PROVIDER]
    FALLBACK = PROVIDERS[FALLBACK_PROVIDER] if FALLBACK_PROVIDER != "none" else None
    _cache = None
    _primary_blocked_until = 0.0
    _upstream_retry_after = 0.0
    _last_upstream_error = None
    _runtime_config_active = active


def apply_runtime_config_from_file() -> bool:
    """Applique la surcharge si elle existe, sinon les valeurs d'environnement."""
    values = read_runtime_config()
    _apply_runtime_config(values or BASELINE_CONFIG, active=values is not None)
    return values is not None


def config_payload() -> dict[str, Any]:
    """Configuration publique, utilisée par le frontend et l'administration."""
    return {
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
        "primary_cooldown_s": PRIMARY_COOLDOWN_S,
        "weather_metar_provider": METAR_PROVIDER,
        "metar_taf_configured": bool(METAR_TAF_API_KEY),
        "checkwx_configured": bool(CHECKWX_API_KEY),
        "radar_style": RADAR_STYLE,
        "location_mode": LOCATION_MODE,
        "airport_icao": AIRPORT_ICAO,
        "influx_enabled": INFLUX_ENABLED,
        "influx_url": INFLUX_URL,
        "influx_org": INFLUX_ORG,
        "influx_bucket": INFLUX_BUCKET,
        "influx_measurement": INFLUX_MEASUREMENT,
        "influx_interval_s": INFLUX_INTERVAL_S,
        "influx_token_configured": bool(INFLUX_TOKEN),
        "stats_enabled": stats_available(),
        "stats_configured": STATS_ENABLED,
        "stats_sample_seconds": STATS_SAMPLE_SECONDS,
        "stats_retention_days": STATS_RETENTION_DAYS,
        "config_source": "runtime" if _runtime_config_active else "environment",
    }


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


def normalize_aircraft(raw_aircraft: list[dict[str, Any]]) -> list[AircraftTarget]:
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

        distance = haversine_nm(LAT, LON, lat, lon)
        if distance > MAX_RADIUS_NM:
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


async def fetch_best_snapshot(client: httpx.AsyncClient) -> CacheEntry:
    """Tente la source primaire puis, si nécessaire, la source de secours."""
    global _primary_blocked_until

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


async def get_snapshot(client: httpx.AsyncClient) -> tuple[CacheEntry, bool, float]:
    """Retourne (snapshot, stale, age_secondes) avec un cache anti-rafale."""
    global _cache, _last_upstream_error, _upstream_retry_after

    now = time.monotonic()
    cached = _cache
    if cached is not None:
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

_stats_db_ok = False
_stats_db_error: str | None = None
_last_stats_recorded_ts = 0
_last_stats_cleanup_day = ""
_influx_last_error_at = 0.0


def stats_available() -> bool:
    return STATS_ENABLED and _stats_db_ok


def _stats_scope() -> str:
    return f"{LAT:.2f},{LON:.2f}"


def _stats_ranges() -> list[int]:
    ranges = sorted({*STATS_RANGE_STEPS, MAX_RADIUS_NM})
    return [value for value in ranges if 1 <= value <= MAX_RADIUS_NM]


def _effective_stats_range(requested: int | None = None) -> int:
    base = DIST_NM if requested is None else int(round(requested))
    target = max(1, min(MAX_RADIUS_NM, base))
    for value in _stats_ranges():
        if value >= target:
            return value
    ranges = _stats_ranges()
    return ranges[-1] if ranges else target


def _stats_connect() -> sqlite3.Connection:
    STATS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATS_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _stats_schema() -> None:
    with closing(_stats_connect()) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != 3:
            conn.executescript(
                """
                DROP TABLE IF EXISTS snapshots;
                DROP TABLE IF EXISTS aircraft_seen;
                """
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                day TEXT NOT NULL,
                scope TEXT NOT NULL,
                counts_json TEXT NOT NULL,
                source TEXT,
                center_lat REAL NOT NULL,
                center_lon REAL NOT NULL,
                max_range_nm REAL NOT NULL,
                UNIQUE(ts, scope)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_day_scope_ts
                ON snapshots(day, scope, ts);
            CREATE TABLE IF NOT EXISTS aircraft_seen (
                day TEXT NOT NULL,
                scope TEXT NOT NULL,
                hex TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                callsign TEXT,
                type TEXT,
                max_alt INTEGER,
                min_distance REAL,
                PRIMARY KEY(day, scope, hex)
            );
            CREATE INDEX IF NOT EXISTS idx_aircraft_seen_day_scope
                ON aircraft_seen(day, scope);
            """
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()


def cleanup_stats_db(force: bool = False) -> int:
    global _last_stats_cleanup_day
    if not _stats_db_ok:
        return 0
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if not force and _last_stats_cleanup_day == today:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=STATS_RETENTION_DAYS)).strftime(
        "%Y-%m-%d"
    )
    try:
        with closing(_stats_connect()) as conn:
            cursor = conn.execute("DELETE FROM snapshots WHERE day < ?", (cutoff,))
            deleted = max(0, cursor.rowcount)
            conn.execute("DELETE FROM aircraft_seen WHERE day < ?", (cutoff,))
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Nettoyage des statistiques impossible: %s", exc)
        return 0
    _last_stats_cleanup_day = today
    if deleted:
        logger.info(
            "Statistiques: %d ancien(s) relevé(s) supprimé(s) avant %s.",
            deleted,
            cutoff,
        )
    return deleted


def init_stats_db() -> bool:
    global _stats_db_ok, _stats_db_error, _last_stats_cleanup_day
    if not STATS_ENABLED:
        _stats_db_ok = False
        _stats_db_error = None
        return False
    try:
        _stats_schema()
        _stats_db_ok = True
        _stats_db_error = None
        _last_stats_cleanup_day = datetime.now(UTC).strftime("%Y-%m-%d")
        cleanup_stats_db(force=True)
        logger.info("Base de statistiques SQLite prête: %s", STATS_DB_PATH)
    except (OSError, sqlite3.Error) as exc:
        _stats_db_ok = False
        _stats_db_error = str(exc)
        logger.error("Base de statistiques indisponible (%s): %s", STATS_DB_PATH, exc)
    return _stats_db_ok


def record_stats_snapshot(snapshot: CacheEntry) -> dict[str, Any] | None:
    global _last_stats_recorded_ts
    if not _stats_db_ok:
        return None
    ts = int(snapshot.fetched_at.timestamp())
    if ts <= _last_stats_recorded_ts:
        return None
    day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
    scope = _stats_scope()
    ranges = _stats_ranges()
    default_range = _effective_stats_range()
    located: list[tuple[AircraftTarget, float]] = []
    for target in snapshot.aircraft:
        distance = haversine_nm(LAT, LON, target.lat, target.lon)
        if distance <= MAX_RADIUS_NM:
            located.append((target, distance))
    counts: dict[str, Any] = {}
    for range_nm in ranges:
        subset = [
            (target, distance)
            for target, distance in located
            if distance <= range_nm
        ]
        airborne = sum(1 for target, _distance in subset if not target.on_ground)
        ground = len(subset) - airborne
        emergency = sum(
            1
            for target, _distance in subset
            if target.squawk in {"7500", "7600", "7700"}
        )
        altitudes = [
            target.alt_baro
            for target, _distance in subset
            if not target.on_ground and target.alt_baro is not None
        ]
        distances = [distance for _target, distance in subset]
        counts[str(range_nm)] = {
            "aircraft": len(subset),
            "airborne": airborne,
            "ground": ground,
            "emergency": emergency,
            "max_alt": max(altitudes, default=None),
            "average_alt": (
                round(sum(altitudes) / len(altitudes), 1) if altitudes else None
            ),
            "max_distance": (
                round(max(distances), 2) if distances else None
            ),
        }
    try:
        with closing(_stats_connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO snapshots
                    (ts, day, scope, counts_json, source, center_lat, center_lon,
                     max_range_nm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    day,
                    scope,
                    json.dumps(counts, separators=(",", ":"), sort_keys=True),
                    snapshot.source,
                    LAT,
                    LON,
                    float(MAX_RADIUS_NM),
                ),
            )
            if cursor.rowcount == 0:
                _last_stats_recorded_ts = ts
                return None
            for target, distance in located:
                target_alt = (
                    target.alt_baro
                    if not target.on_ground and target.alt_baro is not None
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO aircraft_seen
                        (day, scope, hex, first_seen, last_seen, callsign, type,
                         max_alt, min_distance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day, scope, hex) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        callsign = COALESCE(excluded.callsign, aircraft_seen.callsign),
                        type = COALESCE(excluded.type, aircraft_seen.type),
                        max_alt = CASE
                            WHEN excluded.max_alt IS NULL THEN aircraft_seen.max_alt
                            WHEN aircraft_seen.max_alt IS NULL THEN excluded.max_alt
                            ELSE MAX(aircraft_seen.max_alt, excluded.max_alt)
                        END,
                        min_distance = CASE
                            WHEN excluded.min_distance IS NULL THEN aircraft_seen.min_distance
                            WHEN aircraft_seen.min_distance IS NULL THEN excluded.min_distance
                            ELSE MIN(aircraft_seen.min_distance, excluded.min_distance)
                        END
                    """,
                    (
                        day,
                        scope,
                        str(target.hex).lower(),
                        ts,
                        ts,
                        target.callsign,
                        target.type,
                        target_alt,
                        round(distance, 2),
                    ),
                )
            unique_default = conn.execute(
                """
                SELECT COUNT(*) FROM aircraft_seen
                WHERE day = ? AND scope = ? AND min_distance <= ?
                """,
                (day, scope, default_range),
            ).fetchone()[0]
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        logger.error("Écriture des statistiques impossible: %s", exc)
        return None

    _last_stats_recorded_ts = ts
    cleanup_stats_db()
    default_counts = counts.get(str(default_range), {})
    return {
        "ts": ts,
        "day": day,
        "scope": scope,
        "counts": counts,
        "default_range_nm": default_range,
        "unique_default": int(unique_default),
        "source": snapshot.source,
        "center_lat": LAT,
        "center_lon": LON,
        "max_range_nm": float(MAX_RADIUS_NM),
        **default_counts,
    }

def stats_summary(
    day: str,
    days: int,
    minutes: int,
    requested_range: int | None = None,
) -> dict[str, Any]:
    scope = _stats_scope()
    range_nm = _effective_stats_range(requested_range)
    available_ranges = _stats_ranges()
    now = int(time.time())
    recent_start = (now // 60 - (minutes - 1)) * 60
    today = datetime.now(UTC).date()
    first_daily_day = today - timedelta(days=days - 1)
    daily_start = first_daily_day.strftime("%Y-%m-%d")
    daily_days = [
        (first_daily_day + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days)
    ]
    range_key = str(range_nm)

    with closing(_stats_connect()) as conn:
        day_rows = conn.execute(
            """
            SELECT ts, counts_json, source
            FROM snapshots
            WHERE day = ? AND scope = ?
            ORDER BY ts
            """,
            (day, scope),
        ).fetchall()
        unique = conn.execute(
            """
            SELECT COUNT(*) FROM aircraft_seen
            WHERE day = ? AND scope = ? AND min_distance <= ?
            """,
            (day, scope, range_nm),
        ).fetchone()[0]
        recent_rows = conn.execute(
            """
            SELECT ts, counts_json
            FROM snapshots
            WHERE scope = ? AND ts >= ?
            ORDER BY ts
            """,
            (scope, recent_start),
        ).fetchall()
        daily_rows = conn.execute(
            """
            SELECT day, ts, counts_json
            FROM snapshots
            WHERE scope = ? AND day >= ?
            ORDER BY day, ts
            """,
            (scope, daily_start),
        ).fetchall()
        daily_unique_rows = conn.execute(
            """
            SELECT day, COUNT(*) AS unique_count
            FROM aircraft_seen
            WHERE scope = ? AND day >= ? AND min_distance <= ?
            GROUP BY day
            """,
            (scope, daily_start, range_nm),
        ).fetchall()

    day_entries: list[tuple[int, int, dict[str, Any], str | None]] = []
    hourly: dict[int, dict[str, Any]] = {}
    for row in day_rows:
        try:
            counts = json.loads(row["counts_json"])
        except (TypeError, ValueError):
            continue
        entry = counts.get(range_key) if isinstance(counts, dict) else None
        if not isinstance(entry, dict):
            continue
        ts = int(row["ts"])
        aircraft = int(entry.get("aircraft") or 0)
        day_entries.append((ts, aircraft, entry, row["source"]))
        hour = datetime.fromtimestamp(ts, UTC).hour
        bucket = hourly.setdefault(
            hour, {"hour": hour, "samples": 0, "sum": 0, "peak": 0}
        )
        bucket["samples"] += 1
        bucket["sum"] += aircraft
        bucket["peak"] = max(bucket["peak"], aircraft)

    samples = len(day_entries)
    observations = sum(entry[1] for entry in day_entries)
    peak = max((entry[1] for entry in day_entries), default=0)
    first_ts = day_entries[0][0] if day_entries else None
    last_ts = day_entries[-1][0] if day_entries else None
    last = None
    if day_entries:
        last_ts_value, _aircraft, last_counts, source = day_entries[-1]
        last = {
            "ts": last_ts_value,
            **last_counts,
            "source": source,
        }

    hours = []
    for hour in range(24):
        bucket = hourly.get(hour)
        if bucket:
            hours.append(
                {
                    "hour": hour,
                    "samples": bucket["samples"],
                    "average": round(bucket["sum"] / bucket["samples"], 2),
                    "peak": bucket["peak"],
                }
            )
        else:
            hours.append({"hour": hour, "samples": 0, "average": 0.0, "peak": 0})

    recent_buckets: dict[int, dict[str, Any]] = {}
    for row in recent_rows:
        try:
            counts = json.loads(row["counts_json"])
        except (TypeError, ValueError):
            continue
        entry = counts.get(range_key) if isinstance(counts, dict) else None
        if not isinstance(entry, dict):
            continue
        minute = (int(row["ts"]) // 60) * 60
        if minute < recent_start:
            continue
        aircraft = int(entry.get("aircraft") or 0)
        bucket = recent_buckets.setdefault(
            minute, {"ts": minute, "samples": 0, "sum": 0, "peak": 0}
        )
        bucket["samples"] += 1
        bucket["sum"] += aircraft
        bucket["peak"] = max(bucket["peak"], aircraft)
    recent = [
        {
            "ts": bucket["ts"],
            "samples": bucket["samples"],
            "average": round(bucket["sum"] / bucket["samples"], 2),
            "peak": bucket["peak"],
        }
        for bucket in sorted(recent_buckets.values(), key=lambda item: item["ts"])
    ]

    daily_snapshot: dict[str, dict[str, Any]] = {}
    for row in daily_rows:
        try:
            counts = json.loads(row["counts_json"])
        except (TypeError, ValueError):
            continue
        entry = counts.get(range_key) if isinstance(counts, dict) else None
        if not isinstance(entry, dict):
            continue
        daily_day = row["day"]
        aircraft = int(entry.get("aircraft") or 0)
        bucket = daily_snapshot.setdefault(
            daily_day, {"samples": 0, "observations": 0, "peak": 0}
        )
        bucket["samples"] += 1
        bucket["observations"] += aircraft
        bucket["peak"] = max(bucket["peak"], aircraft)
    unique_by_day = {row["day"]: int(row["unique_count"]) for row in daily_unique_rows}
    daily = []
    for daily_day in daily_days:
        bucket = daily_snapshot.get(daily_day)
        samples_day = bucket["samples"] if bucket else 0
        observations_day = bucket["observations"] if bucket else 0
        daily.append(
            {
                "day": daily_day,
                "samples": samples_day,
                "observations": observations_day,
                "peak": bucket["peak"] if bucket else 0,
                "average": (
                    round(observations_day / samples_day, 2) if samples_day else 0.0
                ),
                "unique": unique_by_day.get(daily_day, 0),
            }
        )

    return {
        "enabled": True,
        "day": day,
        "timezone": "UTC",
        "scope": scope,
        "range_nm": range_nm,
        "available_ranges": available_ranges,
        "samples": samples,
        "observations": observations,
        "peak": peak,
        "average": round(observations / samples, 2) if samples else 0.0,
        "unique": int(unique),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "last": last,
        "hours": hours,
        "recent": recent,
        "daily": daily,
        "sample_seconds": STATS_SAMPLE_SECONDS,
        "retention_days": STATS_RETENTION_DAYS,
    }

def reset_stats_db() -> int:
    global _last_stats_recorded_ts
    if not _stats_db_ok:
        return 0
    with closing(_stats_connect()) as conn:
        deleted = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        conn.execute("DELETE FROM snapshots")
        conn.execute("DELETE FROM aircraft_seen")
        conn.commit()
    _last_stats_recorded_ts = 0
    return int(deleted)


def _line_escape(value: Any) -> str:
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _influx_ready() -> bool:
    if not INFLUX_ENABLED:
        return False
    parsed = urlparse(INFLUX_URL)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and INFLUX_ORG
        and INFLUX_BUCKET
        and INFLUX_TOKEN
        and re.fullmatch(r"[A-Za-z0-9_.-]+", INFLUX_MEASUREMENT)
    )


def _influx_write_url() -> str:
    base = INFLUX_URL.rstrip("/")
    query = (
        f"org={quote(INFLUX_ORG, safe='')}"
        f"&bucket={quote(INFLUX_BUCKET, safe='')}"
        "&precision=ns"
    )
    if base.endswith("/api/v2/write"):
        return f"{base}?{query}"
    return f"{base}/api/v2/write?{query}"


def _influx_line(record: dict[str, Any]) -> str:
    location = AIRPORT_ICAO if LOCATION_MODE == "airport" and AIRPORT_ICAO else "GPS"
    default_range = int(record.get("default_range_nm") or _effective_stats_range())
    counts = record.get("counts")
    entry = (
        counts.get(str(default_range))
        if isinstance(counts, dict)
        else None
    )
    if not isinstance(entry, dict):
        entry = record
    tags = [
        f"source={_line_escape(record.get('source') or 'unknown')}",
        f"location={_line_escape(location)}",
        f"range_nm={default_range}",
    ]
    fields = [
        f"aircraft={int(entry.get('aircraft') or 0)}i",
        f"airborne={int(entry.get('airborne') or 0)}i",
        f"ground={int(entry.get('ground') or 0)}i",
        f"emergency={int(entry.get('emergency') or 0)}i",
        f"unique_today={int(record.get('unique_default') or 0)}i",
    ]
    if entry.get("max_alt") is not None:
        fields.append(f"max_alt_ft={int(entry['max_alt'])}i")
    if entry.get("average_alt") is not None:
        fields.append(f"average_alt_ft={float(entry['average_alt']):.1f}")
    if entry.get("max_distance") is not None:
        fields.append(f"max_distance_nm={float(entry['max_distance']):.2f}")
    timestamp_ns = int(record.get("ts") or time.time()) * 1_000_000_000
    return (
        f"{INFLUX_MEASUREMENT},{','.join(tags)} "
        f"{','.join(fields)} {timestamp_ns}"
    )


async def _influx_post(
    client: httpx.AsyncClient, line: str
) -> tuple[bool, int | None, str]:
    if not _influx_ready():
        return False, None, "InfluxDB désactivé ou incomplet."
    try:
        response = await client.post(
            _influx_write_url(),
            headers={
                "Authorization": f"Token {INFLUX_TOKEN}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            content=f"{line}\n",
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        return False, None, f"InfluxDB injoignable ({exc.__class__.__name__})."
    if 200 <= response.status_code < 300:
        return True, response.status_code, "ok"
    detail = response.text.strip().replace("\n", " ")[:300]
    return False, response.status_code, detail or "écriture refusée"


async def export_influx_record(
    client: httpx.AsyncClient, record: dict[str, Any]
) -> bool:
    global _influx_last_error_at
    if not _influx_ready():
        return False
    ok, status, detail = await _influx_post(client, _influx_line(record))
    if not ok:
        now = time.monotonic()
        if now - _influx_last_error_at > 60:
            logger.warning(
                "Export InfluxDB échoué (HTTP %s): %s",
                status if status is not None else "-",
                detail,
            )
            _influx_last_error_at = now
    return ok


async def test_influx_connection(client: httpx.AsyncClient) -> dict[str, Any]:
    if not INFLUX_ENABLED:
        return {"ok": False, "detail": "Export InfluxDB désactivé."}
    if not _influx_ready():
        return {
            "ok": False,
            "detail": "Configuration InfluxDB incomplète (URL, org, bucket, token, mesure).",
        }
    line = (
        f"{INFLUX_MEASUREMENT},test=true,source=radardesk "
        f"test=1i {time.time_ns()}"
    )
    ok, status, detail = await _influx_post(client, line)
    return {"ok": ok, "status": status, "detail": detail}


async def stats_collector_loop(app: FastAPI) -> None:
    last_influx = 0.0
    while True:
        try:
            snapshot, stale, _age = await get_snapshot(app.state.http)
            if not stale:
                record = await asyncio.to_thread(record_stats_snapshot, snapshot)
                if record and INFLUX_ENABLED:
                    now = time.monotonic()
                    if now - last_influx >= INFLUX_INTERVAL_S:
                        if await export_influx_record(app.state.http, record):
                            last_influx = now
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - dépend du réseau
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
    try:
        stats_ok = await asyncio.to_thread(init_stats_db)
        if stats_ok:
            stats_task = asyncio.create_task(stats_collector_loop(app))
            logger.info(
                "Collecteur de statistiques actif (échantillon %ds, rétention %dj).",
                STATS_SAMPLE_SECONDS,
                STATS_RETENTION_DAYS,
            )
        yield
    finally:
        if stats_task is not None:
            stats_task.cancel()
            with suppress(asyncio.CancelledError):
                await stats_task
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
            "first_ts": None,
            "last_ts": None,
            "last": None,
            "hours": [],
            "recent": [],
            "daily": [],
            "sample_seconds": STATS_SAMPLE_SECONDS,
            "retention_days": STATS_RETENTION_DAYS,
            "reason": _stats_db_error if STATS_ENABLED else "disabled",
        }
    try:
        return await asyncio.to_thread(
            stats_summary, requested_day, days, minutes, requested_range
        )
    except (OSError, sqlite3.Error) as exc:
        logger.error("Lecture des statistiques impossible: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Base de statistiques temporairement indisponible.",
        ) from exc


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
            stats = await asyncio.to_thread(
                stats_summary,
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
            "stats_error": _stats_db_error,
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
        "stats_error": _stats_db_error,
    }


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


@app.post("/internal/stats/reset", include_in_schema=False)
async def internal_stats_reset(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    if not stats_available():
        raise HTTPException(
            status_code=503,
            detail="Statistiques persistantes désactivées ou indisponibles.",
        )
    deleted = await asyncio.to_thread(reset_stats_db)
    logger.info("Statistiques réinitialisées (%d relevé(s) supprimé(s)).", deleted)
    return {"status": "reset", "deleted": deleted}


@app.post("/internal/influx/test", include_in_schema=False)
async def internal_influx_test(request: Request) -> dict[str, Any]:
    _require_internal_token(request)
    return await test_influx_connection(request.app.state.http)


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

    logger.info(
        "Configuration runtime rechargée (%s).",
        "fichier runtime" if active else "valeurs d'environnement",
    )
    return {"status": "reloaded", "config": config_payload()}


# Les fichiers statiques sont montés après les routes API, uniquement si un
# répertoire frontend est fourni explicitement (mode développement).
if ASSETS_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(ASSETS_DIR)), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL.lower())
