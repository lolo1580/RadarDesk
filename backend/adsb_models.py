"""Modèles Pydantic partagés RadarDesk."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AircraftTarget(BaseModel):
    """Cible ADS-B normalisée et prête à être affichée sur le scope."""

    hex: str = Field(description="Adresse ICAO 24 bits (hexadécimal).")
    callsign: str | None = Field(default=None, description="Indicatif de vol nettoyé.")
    registration: str | None = Field(
        default=None, description="Immatriculation de l'aéronef (ex: F-HBLV)."
    )
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
