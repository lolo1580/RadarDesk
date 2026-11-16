"""Stockage SQLite des statistiques de réception RadarDesk."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from adsb_models import AircraftTarget, CacheEntry
from adsb_utils import haversine_nm, iso_utc, utc_now

logger = logging.getLogger("radardesk.stats")


class StatsStore:
    """Encapsule le schéma, la collecte, la rétention et les sauvegardes."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool,
        sample_seconds: int,
        retention_days: int,
        backup_keep: int,
        pass_gap_seconds: int,
    ) -> None:
        self.db_path = Path(db_path)
        self.enabled = enabled
        self.sample_seconds = sample_seconds
        self.retention_days = retention_days
        self.backup_keep = backup_keep
        self.pass_gap_seconds = pass_gap_seconds
        self.db_ok = False
        self.db_error: str | None = None
        self.last_recorded_ts = 0
        self.last_sample_ts = 0
        self.last_backup_at: datetime | None = None
        self.last_backup_day: str | None = None
        self.last_cleanup_day = ""

    def available(self) -> bool:
        return self.enabled and self.db_ok

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _schema(self) -> None:
        with closing(self._connect()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 3:
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
                    registration TEXT,
                    type TEXT,
                    max_alt INTEGER,
                    min_distance REAL,
                    observations INTEGER NOT NULL DEFAULT 0,
                    passes INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, scope, hex)
                );
                CREATE INDEX IF NOT EXISTS idx_aircraft_seen_day_scope
                    ON aircraft_seen(day, scope);
                """
            )
            # Migration non destructive des bases déjà remplies : les colonnes
            # d'immatriculation et de passages sont ajoutées sans perdre
            # l'historique existant.
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(aircraft_seen)")
            }
            if "registration" not in columns:
                conn.execute("ALTER TABLE aircraft_seen ADD COLUMN registration TEXT")
            if "observations" not in columns:
                conn.execute(
                    "ALTER TABLE aircraft_seen ADD COLUMN observations INTEGER NOT NULL DEFAULT 0"
                )
            if "passes" not in columns:
                conn.execute(
                    "ALTER TABLE aircraft_seen ADD COLUMN passes INTEGER NOT NULL DEFAULT 0"
                )
                # Les lignes historiques n'ont pas de découpage de passages : on
                # compte au minimum un passage par aéronef et par jour.
                conn.execute("UPDATE aircraft_seen SET passes = 1 WHERE passes = 0")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_aircraft_seen_day_scope_reg
                    ON aircraft_seen(day, scope, registration)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_aircraft_seen_scope_hex
                    ON aircraft_seen(scope, hex)
                """
            )
            conn.execute("PRAGMA user_version = 5")
            conn.commit()

    def cleanup(self, force: bool = False) -> int:
        if not self.db_ok:
            return 0
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if not force and self.last_cleanup_day == today:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).strftime(
            "%Y-%m-%d"
        )
        try:
            with closing(self._connect()) as conn:
                cursor = conn.execute("DELETE FROM snapshots WHERE day < ?", (cutoff,))
                deleted = max(0, cursor.rowcount)
                conn.execute("DELETE FROM aircraft_seen WHERE day < ?", (cutoff,))
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Nettoyage des statistiques impossible: %s", exc)
            return 0
        self.last_cleanup_day = today
        self.backup()
        if deleted:
            logger.info(
                "Statistiques: %d ancien(s) relevé(s) supprimé(s) avant %s.",
                deleted,
                cutoff,
            )
        return deleted

    def backup(self, force: bool = False) -> Path | None:
        """Copie la base SQLite dans /stats/backups, une fois par jour.

        La copie utilise l'API de sauvegarde SQLite (cohérente même en WAL), puis
        les anciennes copies sont élaguées selon ``ADSB_STATS_BACKUP_KEEP``.
        """
        if not self.db_ok:
            return None
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if not force and self.last_backup_day == today:
            return None
        backup_dir = self.db_path.parent / "backups"
        target = backup_dir / f"adsb-stats-{today}.db"
        temporary = backup_dir / f"adsb-stats-{today}.tmp"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            if target.exists():
                self.last_backup_at = datetime.fromtimestamp(
                    target.stat().st_mtime, UTC
                )
            else:
                with (
                    closing(self._connect()) as source,
                    closing(sqlite3.connect(temporary)) as destination,
                ):
                    source.backup(destination)
                temporary.replace(target)
                self.last_backup_at = utc_now()
                logger.info("Sauvegarde SQLite créée: %s", target)
            for old_backup in sorted(backup_dir.glob("adsb-stats-*.db"))[
                : -self.backup_keep
            ]:
                old_backup.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            self.last_backup_day = today
            return target
        except (OSError, sqlite3.Error) as exc:
            temporary.unlink(missing_ok=True)
            logger.warning("Sauvegarde SQLite impossible: %s", exc)
            return None

    def health(self) -> dict[str, Any]:
        """Indicateurs de fraîcheur de la collecte et des sauvegardes SQLite."""
        now = time.time()
        sample_ts = self.last_sample_ts
        sample_age = round(max(0.0, now - sample_ts), 1) if sample_ts else None
        stale_after = max(120.0, 2.0 * self.sample_seconds)
        stale = bool(
            self.available() and (sample_age is None or sample_age > stale_after)
        )
        backup_age = (
            round(max(0.0, now - self.last_backup_at.timestamp()), 1)
            if self.last_backup_at is not None
            else None
        )
        return {
            "stats_last_sample_at": (
                iso_utc(datetime.fromtimestamp(sample_ts, UTC)) if sample_ts else None
            ),
            "stats_last_sample_age_s": sample_age,
            "stats_stale": stale,
            "stats_stale_after_s": stale_after,
            "stats_backup_at": (
                iso_utc(self.last_backup_at)
                if self.last_backup_at is not None
                else None
            ),
            "stats_backup_age_s": backup_age,
            "stats_backup_keep": self.backup_keep,
        }

    def init(self) -> bool:
        if not self.enabled:
            self.db_ok = False
            self.db_error = None
            return False
        try:
            self._schema()
            self.db_ok = True
            self.db_error = None
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()
                self.last_sample_ts = int(row[0] or 0) if row is not None else 0
            self.last_cleanup_day = datetime.now(UTC).strftime("%Y-%m-%d")
            self.cleanup(force=True)
            logger.info("Base de statistiques SQLite prête: %s", self.db_path)
        except (OSError, sqlite3.Error) as exc:
            self.db_ok = False
            self.db_error = str(exc)
            logger.error("Base de statistiques indisponible (%s): %s", self.db_path, exc)
        return self.db_ok

    def record(
        self,
        snapshot: CacheEntry,
        *,
        lat: float,
        lon: float,
        max_radius_nm: int,
        scope: str,
        ranges: list[int],
        default_range: int,
    ) -> dict[str, Any] | None:
        if not self.db_ok:
            return None
        ts = int(snapshot.fetched_at.timestamp())
        if ts <= self.last_recorded_ts:
            return None
        day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
        located: list[tuple[AircraftTarget, float]] = []
        for target in snapshot.aircraft:
            distance = haversine_nm(lat, lon, target.lat, target.lon)
            if distance <= max_radius_nm:
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
            with closing(self._connect()) as conn:
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
                        lat,
                        lon,
                        float(max_radius_nm),
                    ),
                )
                if cursor.rowcount == 0:
                    self.last_recorded_ts = ts
                    return None
                for target, distance in located:
                    target_alt = (
                        target.alt_baro
                        if not target.on_ground and target.alt_baro is not None
                        else None
                    )
                    conn.execute(
                        f"""
                        INSERT INTO aircraft_seen
                            (day, scope, hex, first_seen, last_seen, callsign,
                             registration, type, max_alt, min_distance, observations,
                             passes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                        ON CONFLICT(day, scope, hex) DO UPDATE SET
                            last_seen = excluded.last_seen,
                            callsign = COALESCE(excluded.callsign, aircraft_seen.callsign),
                            registration = COALESCE(
                                excluded.registration, aircraft_seen.registration
                            ),
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
                            END,
                            observations = aircraft_seen.observations + 1,
                            passes = aircraft_seen.passes + CASE
                                WHEN excluded.last_seen - aircraft_seen.last_seen
                                     > {self.pass_gap_seconds}
                                THEN 1 ELSE 0
                            END
                        """,
                        (
                            day,
                            scope,
                            str(target.hex).lower(),
                            ts,
                            ts,
                            target.callsign,
                            target.registration,
                            target.type,
                            target_alt,
                            round(distance, 2),
                        ),
                    )
                    if target.registration:
                        # Complète les jours antérieurs du même aéronef dès que
                        # son immatriculation est connue, sans écraser une valeur
                        # déjà enregistrée.
                        conn.execute(
                            """
                            UPDATE aircraft_seen
                            SET registration = ?
                            WHERE scope = ? AND hex = ? AND registration IS NULL
                            """,
                            (target.registration, scope, str(target.hex).lower()),
                        )
                unique_default = conn.execute(
                    """
                    SELECT COUNT(*) FROM aircraft_seen
                    WHERE day = ? AND scope = ? AND min_distance <= ?
                    """,
                    (day, scope, default_range),
                ).fetchone()[0]
                unique_registrations_default = conn.execute(
                    """
                    SELECT COUNT(DISTINCT registration) FROM aircraft_seen
                    WHERE day = ? AND scope = ? AND min_distance <= ?
                      AND registration IS NOT NULL AND registration <> ''
                    """,
                    (day, scope, default_range),
                ).fetchone()[0]
                registration_passes_default = conn.execute(
                    """
                    SELECT COALESCE(SUM(passes), 0) FROM aircraft_seen
                    WHERE day = ? AND scope = ? AND min_distance <= ?
                      AND registration IS NOT NULL AND registration <> ''
                    """,
                    (day, scope, default_range),
                ).fetchone()[0]
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.error("Écriture des statistiques impossible: %s", exc)
            return None

        self.last_recorded_ts = ts
        self.last_sample_ts = ts
        self.cleanup()
        default_counts = counts.get(str(default_range), {})
        return {
            "ts": ts,
            "day": day,
            "scope": scope,
            "counts": counts,
            "default_range_nm": default_range,
            "unique_default": int(unique_default),
            "unique_registrations_default": int(unique_registrations_default),
            "registration_passes_default": int(registration_passes_default),
            "source": snapshot.source,
            "center_lat": lat,
            "center_lon": lon,
            "max_range_nm": float(max_radius_nm),
            **default_counts,
        }

    def summary(
        self,
        day: str,
        days: int,
        minutes: int,
        range_nm: int,
        registration_days: int,
        scope: str,
        available_ranges: list[int],
    ) -> dict[str, Any]:
        now = int(time.time())
        recent_start = (now // 60 - (minutes - 1)) * 60
        today = datetime.now(UTC).date()
        registration_days = max(1, int(registration_days))
        try:
            period_end = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            period_end = today
        period_start = (
            period_end - timedelta(days=registration_days - 1)
        ).strftime("%Y-%m-%d")
        first_daily_day = today - timedelta(days=days - 1)
        daily_start = first_daily_day.strftime("%Y-%m-%d")
        daily_days = [
            (first_daily_day + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(days)
        ]
        range_key = str(range_nm)

        with closing(self._connect()) as conn:
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
            registrations = conn.execute(
                """
                SELECT COUNT(DISTINCT registration) FROM aircraft_seen
                WHERE day = ? AND scope = ? AND min_distance <= ?
                  AND registration IS NOT NULL AND registration <> ''
                """,
                (day, scope, range_nm),
            ).fetchone()[0]
            period_totals = conn.execute(
                """
                SELECT COUNT(DISTINCT hex) AS unique_count,
                       COUNT(DISTINCT CASE
                           WHEN registration IS NOT NULL AND registration <> ''
                           THEN registration
                       END) AS registration_count,
                       COALESCE(SUM(CASE
                           WHEN registration IS NOT NULL AND registration <> ''
                           THEN passes ELSE 0
                       END), 0) AS pass_count
                FROM aircraft_seen
                WHERE scope = ? AND day >= ? AND day <= ? AND min_distance <= ?
                """,
                (scope, period_start, day, range_nm),
            ).fetchone()
            top_registration_rows = conn.execute(
                """
                SELECT registration,
                       SUM(passes) AS passes,
                       COUNT(DISTINCT day) AS active_days,
                       SUM(observations) AS observations,
                       MAX(callsign) AS callsign,
                       MAX(type) AS type,
                       MIN(first_seen) AS first_seen,
                       MAX(last_seen) AS last_seen,
                       MIN(min_distance) AS min_distance
                FROM aircraft_seen
                WHERE scope = ? AND day >= ? AND day <= ? AND min_distance <= ?
                  AND registration IS NOT NULL AND registration <> ''
                GROUP BY registration
                ORDER BY passes DESC, last_seen DESC
                LIMIT 200
                """,
                (scope, period_start, day, range_nm),
            ).fetchall()
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
            "registrations": int(registrations),
            "unique_period": int(period_totals["unique_count"] or 0),
            "registrations_period": int(period_totals["registration_count"] or 0),
            "registration_passes": int(period_totals["pass_count"] or 0),
            "registration_days": int(registration_days),
            "top_registrations": [
                {
                    "registration": row["registration"],
                    "passes": int(row["passes"] or 0),
                    "active_days": int(row["active_days"] or 0),
                    "observations": int(row["observations"] or 0),
                    "callsign": row["callsign"],
                    "type": row["type"],
                    "min_distance": row["min_distance"],
                    "first_seen": int(row["first_seen"]),
                    "last_seen": int(row["last_seen"]),
                }
                for row in top_registration_rows
            ],
            "first_ts": first_ts,
            "last_ts": last_ts,
            "last": last,
            "hours": hours,
            "recent": recent,
            "daily": daily,
            "sample_seconds": self.sample_seconds,
            "retention_days": self.retention_days,
        }

    def reset(self) -> int:
        if not self.db_ok:
            return 0
        with closing(self._connect()) as conn:
            deleted = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            conn.execute("DELETE FROM snapshots")
            conn.execute("DELETE FROM aircraft_seen")
            conn.commit()
        self.last_recorded_ts = 0
        self.last_sample_ts = 0
        return int(deleted)
