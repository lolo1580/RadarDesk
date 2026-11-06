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
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    global METAR_PROVIDER
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
    try:
        yield
    finally:
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
    }


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

    return {
        "icao": raw.get("icaoId"),
        "name": raw.get("name"),
        "observed": raw.get("reportTime"),
        "flight_category": (raw.get("fltCat") or "UNKNOWN").upper(),
        "wind_direction": raw.get("wdir"),
        "wind_speed_kt": raw.get("wspd"),
        "wind_gust_kt": raw.get("wgst"),
        "visibility_sm": raw.get("visib"),
        "temperature_c": raw.get("temp"),
        "dewpoint_c": raw.get("dewp"),
        "altimeter_hpa": raw.get("altim"),
        "weather": raw.get("wxString") or "",
        "clouds": clouds,
        "raw": raw.get("rawOb") or "",
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
