"""Export InfluxDB v2 des échantillons de statistiques RadarDesk."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger("radardesk.influx")


@dataclass(frozen=True, slots=True)
class InfluxSettings:
    enabled: bool
    url: str
    org: str
    bucket: str
    token: str
    measurement: str
    airport_icao: str | None
    location_mode: str


def _line_escape(value: Any) -> str:
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


class InfluxExporter:
    def __init__(self, settings: InfluxSettings) -> None:
        self.settings = settings
        self.last_error_at = 0.0

    def configure(self, settings: InfluxSettings) -> None:
        self.settings = settings

    def ready(self) -> bool:
        settings = self.settings
        if not settings.enabled:
            return False
        parsed = urlparse(settings.url)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and settings.org
            and settings.bucket
            and settings.token
            and re.fullmatch(r"[A-Za-z0-9_.-]+", settings.measurement)
        )

    def write_url(self) -> str:
        base = self.settings.url.rstrip("/")
        query = (
            f"org={quote(self.settings.org, safe='')}"
            f"&bucket={quote(self.settings.bucket, safe='')}"
            "&precision=ns"
        )
        if base.endswith("/api/v2/write"):
            return f"{base}?{query}"
        return f"{base}/api/v2/write?{query}"

    def line(self, record: dict[str, Any], default_range: int) -> str:
        settings = self.settings
        location = (
            settings.airport_icao
            if settings.location_mode == "airport" and settings.airport_icao
            else "GPS"
        )
        counts = record.get("counts")
        entry = counts.get(str(default_range)) if isinstance(counts, dict) else None
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
            (
                f"unique_registrations_today="
                f"{int(record.get('unique_registrations_default') or 0)}i"
            ),
            (
                f"registration_passes_today="
                f"{int(record.get('registration_passes_default') or 0)}i"
            ),
        ]
        if entry.get("max_alt") is not None:
            fields.append(f"max_alt_ft={int(entry['max_alt'])}i")
        if entry.get("average_alt") is not None:
            fields.append(f"average_alt_ft={float(entry['average_alt']):.1f}")
        if entry.get("max_distance") is not None:
            fields.append(f"max_distance_nm={float(entry['max_distance']):.2f}")
        timestamp_ns = int(record.get("ts") or time.time()) * 1_000_000_000
        return (
            f"{settings.measurement},{','.join(tags)} "
            f"{','.join(fields)} {timestamp_ns}"
        )

    async def _post(
        self, client: httpx.AsyncClient, line: str
    ) -> tuple[bool, int | None, str]:
        settings = self.settings
        if not self.ready():
            return False, None, "InfluxDB désactivé ou incomplet."
        try:
            response = await client.post(
                self.write_url(),
                headers={
                    "Authorization": f"Token {settings.token}",
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

    async def export(
        self,
        client: httpx.AsyncClient,
        record: dict[str, Any],
        default_range: int,
    ) -> bool:
        if not self.ready():
            return False
        ok, status, detail = await self._post(
            client, self.line(record, default_range)
        )
        if not ok:
            now = time.monotonic()
            if now - self.last_error_at > 60:
                logger.warning(
                    "Export InfluxDB échoué (HTTP %s): %s",
                    status if status is not None else "-",
                    detail,
                )
                self.last_error_at = now
        return ok

    async def test(self, client: httpx.AsyncClient) -> dict[str, Any]:
        settings = self.settings
        if not settings.enabled:
            return {"ok": False, "detail": "Export InfluxDB désactivé."}
        if not self.ready():
            return {
                "ok": False,
                "detail": (
                    "Configuration InfluxDB incomplète "
                    "(URL, org, bucket, token, mesure)."
                ),
            }
        line = (
            f"{settings.measurement},test=true,source=radardesk "
            f"test=1i {time.time_ns()}"
        )
        ok, status, detail = await self._post(client, line)
        return {"ok": ok, "status": status, "detail": detail}
