"""Service d'administration RadarDesk.

Ce conteneur est séparé du backend radar public. Il fournit :

- une interface web protégée par HTTP Basic Auth ;
- l'état du backend radar (santé, source, cache, nombre de cibles) ;
- l'édition à chaud de la configuration runtime ;
- la persistance de cette configuration dans le volume partagé ``/data`` ;
- le déclenchement du rechargement du backend radar via son endpoint interne.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

# ============================================================================
# CONFIGURATION
# ============================================================================

HOST = os.getenv("ADMIN_HOST", "0.0.0.0")
PORT = int(os.getenv("ADMIN_PORT", "8000"))

ADMIN_USER = os.getenv("ADSB_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADSB_ADMIN_PASSWORD", "admin") or "admin"
DEFAULT_CREDENTIALS = ADMIN_USER == "admin" and ADMIN_PASSWORD == "admin"
INTERNAL_TOKEN = os.getenv("ADSB_INTERNAL_TOKEN", "").strip()

RADAR_BACKEND_URL = os.getenv("RADAR_BACKEND_URL", "http://backend:8000").rstrip("/")
RUNTIME_CONFIG_FILE = Path(os.getenv("ADSB_RUNTIME_CONFIG_FILE", "/data/runtime.json"))
RADAR_PUBLIC_PORT = int(os.getenv("RADAR_PUBLIC_PORT", "8081"))
REQUEST_TIMEOUT_S = float(os.getenv("ADMIN_REQUEST_TIMEOUT", "6.0"))
LOG_LEVEL = os.getenv("ADSB_LOG_LEVEL", "INFO").upper()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

if not INTERNAL_TOKEN:
    raise RuntimeError("ADSB_INTERNAL_TOKEN doit être défini.")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("radardesk-admin")
if DEFAULT_CREDENTIALS:
    logger.warning(
        "Identifiants admin par défaut (admin/admin) : à changer hors usage local."
    )

# ============================================================================
# MODÈLE DE CONFIGURATION RUNTIME
# ============================================================================


class RuntimeConfigPayload(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    dist_nm: int = Field(ge=1, le=250)
    max_radius_nm: int = Field(ge=1, le=250)
    refresh_ms: int = Field(ge=500, le=60_000)
    cache_ttl_s: float = Field(ge=0, le=60)
    stale_ttl_s: float = Field(ge=0, le=3600)
    primary_provider: Literal["airplanes_live", "adsb_lol"]
    fallback_provider: Literal["airplanes_live", "adsb_lol", "none"]
    primary_cooldown_s: float = Field(ge=0, le=86_400)
    weather_metar_provider: Literal["aviationweather", "metar_taf", "checkwx"]
    radar_style: Literal["phosphor", "mono", "atc"]

    @model_validator(mode="after")
    def validate_coherence(self) -> RuntimeConfigPayload:
        if self.dist_nm > self.max_radius_nm:
            raise ValueError("Le rayon par défaut doit être ≤ la portée maximale.")
        if self.cache_ttl_s > self.stale_ttl_s:
            raise ValueError("Le cache TTL doit être ≤ au cache périmé.")
        if (
            self.fallback_provider != "none"
            and self.fallback_provider == self.primary_provider
        ):
            raise ValueError(
                "La source de secours doit être différente de la primaire."
            )
        return self


# ============================================================================
# AUTHENTIFICATION BASIC
# ============================================================================


def credentials_are_valid(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(
        password, ADMIN_PASSWORD
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=3.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=3),
    )
    RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Administration prête — radar=%s, runtime=%s",
        RADAR_BACKEND_URL,
        RUNTIME_CONFIG_FILE,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="RadarDesk Admin",
    version="1.0.0",
    description="Administration du backend RadarDesk.",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    # Le healthcheck Docker ne doit pas dépendre des credentials.
    if request.url.path == "/healthz":
        return await call_next(request)

    if not credentials_are_valid(request.headers.get("Authorization")):
        return Response(
            status_code=401,
            content="Authentification RadarDesk Admin requise.",
            media_type="text/plain",
            headers={
                "WWW-Authenticate": 'Basic realm="RadarDesk Admin", charset="UTF-8"',
                "Cache-Control": "no-store",
            },
        )

    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# ============================================================================
# OUTILS RADAR / FICHIER RUNTIME
# ============================================================================


async def call_radar(
    request: Request,
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any] | None = None,
) -> httpx.Response:
    try:
        return await request.app.state.http.request(
            method,
            f"{RADAR_BACKEND_URL}{path}",
            json=json_payload,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend radar injoignable ({exc.__class__.__name__}).",
        ) from exc


async def radar_json(
    request: Request,
    path: str,
) -> dict[str, Any]:
    response = await call_radar(request, "GET", path)
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Backend radar: HTTP {response.status_code}.",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Réponse interne du backend invalide.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Réponse interne inattendue.")
    return payload


async def reload_radar(request: Request) -> dict[str, Any]:
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADSB_INTERNAL_TOKEN n'est pas configuré.",
        )
    try:
        response = await request.app.state.http.post(
            f"{RADAR_BACKEND_URL}/internal/reload",
            headers={"X-Internal-Token": INTERNAL_TOKEN},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Backend radar injoignable ({exc.__class__.__name__}).",
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": "Réponse de rechargement invalide."}

    if response.status_code != 200:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(
            status_code=502,
            detail=f"Rechargement refusé: {detail or response.status_code}.",
        )
    return payload if isinstance(payload, dict) else {"status": "reloaded"}


def runtime_snapshot() -> bytes | None:
    if not RUNTIME_CONFIG_FILE.is_file():
        return None
    return RUNTIME_CONFIG_FILE.read_bytes()


def restore_runtime_snapshot(snapshot: bytes | None) -> None:
    if snapshot is None:
        RUNTIME_CONFIG_FILE.unlink(missing_ok=True)
        return
    RUNTIME_CONFIG_FILE.write_bytes(snapshot)
    os.chmod(RUNTIME_CONFIG_FILE, 0o644)


def write_runtime_config(payload: RuntimeConfigPayload) -> None:
    RUNTIME_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = RUNTIME_CONFIG_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o644)
    os.replace(temporary, RUNTIME_CONFIG_FILE)


# ============================================================================
# ROUTES
# ============================================================================


@app.get("/healthz", include_in_schema=False)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok\n")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/api/status", tags=["admin"])
async def api_status(request: Request) -> JSONResponse:
    """État consolidé du backend radar et de la configuration."""
    radar_health: dict[str, Any] | None = None
    radar_config: dict[str, Any] | None = None
    error: str | None = None

    try:
        radar_health = await radar_json(request, "/api/health")
        radar_config = await radar_json(request, "/api/config")
    except HTTPException as exc:
        error = str(exc.detail)

    return JSONResponse(
        content={
            "online": error is None,
            "error": error,
            "radar": radar_health,
            "config": radar_config,
            "runtime_file_exists": RUNTIME_CONFIG_FILE.is_file(),
            "radar_public_port": RADAR_PUBLIC_PORT,
            "default_credentials": DEFAULT_CREDENTIALS,
        }
    )


@app.get("/api/config", tags=["admin"])
async def api_get_config(request: Request) -> dict[str, Any]:
    return await radar_json(request, "/api/config")


@app.put("/api/config", tags=["admin"])
async def api_put_config(
    request: Request,
    payload: RuntimeConfigPayload,
) -> dict[str, Any]:
    """Écrit la configuration runtime puis recharge le backend à chaud."""
    snapshot = runtime_snapshot()
    try:
        write_runtime_config(payload)
        reloaded = await reload_radar(request)
    except HTTPException:
        restore_runtime_snapshot(snapshot)
        raise
    except OSError as exc:
        restore_runtime_snapshot(snapshot)
        raise HTTPException(
            status_code=500,
            detail=f"Écriture de la configuration impossible: {exc}",
        ) from exc

    logger.info(
        "Configuration mise à jour par l'administrateur (%.5f/%.5f, %d-%d NM).",
        payload.lat,
        payload.lon,
        payload.dist_nm,
        payload.max_radius_nm,
    )
    return {
        "status": "saved",
        "config": reloaded.get("config"),
        "runtime_file_exists": RUNTIME_CONFIG_FILE.is_file(),
    }


@app.post("/api/config/reset", tags=["admin"])
async def api_reset_config(request: Request) -> dict[str, Any]:
    """Supprime la surcharge et retourne aux variables d'environnement."""
    snapshot = runtime_snapshot()
    try:
        RUNTIME_CONFIG_FILE.unlink(missing_ok=True)
        reloaded = await reload_radar(request)
    except HTTPException:
        restore_runtime_snapshot(snapshot)
        raise
    except OSError as exc:
        restore_runtime_snapshot(snapshot)
        raise HTTPException(
            status_code=500,
            detail=f"Suppression de la configuration impossible: {exc}",
        ) from exc

    logger.info("Configuration runtime réinitialisée vers l'environnement.")
    return {
        "status": "reset",
        "config": reloaded.get("config"),
        "runtime_file_exists": RUNTIME_CONFIG_FILE.is_file(),
    }


@app.post("/api/reload", tags=["admin"])
async def api_reload(request: Request) -> dict[str, Any]:
    """Recharge le fichier runtime existant sans le modifier."""
    result = await reload_radar(request)
    return {
        "status": "reloaded",
        "config": result.get("config"),
        "runtime_file_exists": RUNTIME_CONFIG_FILE.is_file(),
    }


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="admin-static")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL.lower())
