import base64
import json
import time
from datetime import UTC, datetime

import pytest

from backend import server


def test_haversine_zero_and_one_degree():
    assert server.haversine_nm(46.818, 6.94, 46.818, 6.94) == pytest.approx(0.0)
    assert server.haversine_nm(0, 0, 1, 0) == pytest.approx(60.04, abs=0.2)


def test_effective_stats_range_snaps_to_standard_steps(monkeypatch):
    monkeypatch.setattr(server, "DIST_NM", 50)
    monkeypatch.setattr(server, "MAX_RADIUS_NM", 150)
    assert server._effective_stats_range(48) == 50
    assert server._effective_stats_range(51) == 75
    assert server._effective_stats_range(500) == 150


def test_local_db_root_handles_aircraft_json_paths(monkeypatch):
    monkeypatch.setattr(
        server,
        "LOCAL_JSON_URL",
        "http://192.168.30.13:8080/data/aircraft.json",
    )
    assert server._local_db_root() == "http://192.168.30.13:8080/"
    monkeypatch.setattr(
        server,
        "LOCAL_JSON_URL",
        "https://receiver.local/readsb/aircraft.json",
    )
    assert server._local_db_root() == "https://receiver.local/readsb/"


def test_local_stats_payload_computes_rates_and_conversions():
    stats = {
        "now": 1_700_000_000,
        "aircraft_with_pos": 4,
        "aircraft_without_pos": 2,
        "aircraft_count_by_type": {"A320": 2},
        "gain_db": 48.0,
        "estimated_ppm": -2.5,
        "last1min": {
            "start": 100,
            "end": 160,
            "messages": 600,
            "max_distance": 18_520,
            "position_count_total": 12,
            "local": {
                "signal": -12.2,
                "noise": -30.4,
                "peak_signal": -2.0,
                "strong_signals": 44,
                "samples_processed": 1000,
                "modes": 2000,
                "bad": 3,
                "unknown_icao": 4,
                "accepted": [1900, 100],
                "samples_lost": 0,
                "samples_dropped": 1,
            },
        },
    }
    receiver = {
        "version": "3.16.21 wiedehopf git: abc",
        "refresh": 1000,
        "lat": 46.818,
        "lon": 6.94,
        "readsb": True,
    }
    payload = server._local_stats_payload(stats, receiver)
    assert payload["enabled"] is True
    assert payload["source"] == "local"
    assert payload["last_minute"]["message_rate"] == pytest.approx(10.0)
    assert payload["last_minute"]["max_distance_nm"] == pytest.approx(10.0)
    assert payload["last_minute"]["accepted"] == [1900, 100]
    assert payload["aircraft"]["with_position"] == 4
    assert payload["receiver"]["readsb"] is True
    assert payload["receiver"]["version"].startswith("3.16.21")
    json.dumps(payload)


def test_stats_database_records_and_reports(tmp_path, monkeypatch):
    db_path = tmp_path / "stats.db"
    monkeypatch.setattr(server, "STATS_DB_PATH", db_path)
    monkeypatch.setattr(server, "STATS_ENABLED", True)
    monkeypatch.setattr(server, "_stats_db_ok", False)
    monkeypatch.setattr(server, "_stats_db_error", None)
    monkeypatch.setattr(server, "_last_stats_recorded_ts", 0)
    monkeypatch.setattr(server, "_last_stats_sample_ts", 0)
    monkeypatch.setattr(server, "_last_stats_backup_day", None)
    monkeypatch.setattr(server, "_last_stats_backup_at", None)
    monkeypatch.setattr(server, "LAT", 46.818)
    monkeypatch.setattr(server, "LON", 6.94)
    monkeypatch.setattr(server, "DIST_NM", 50)
    monkeypatch.setattr(server, "MAX_RADIUS_NM", 150)
    assert server.init_stats_db() is True

    now = datetime.now(UTC)
    snapshot = server.CacheEntry(
        aircraft=[
            server.AircraftTarget(
                hex="abc123",
                callsign="TEST123",
                registration="F-TEST",
                lat=46.828,
                lon=6.94,
                alt_baro=12_000,
            )
        ],
        source="local",
        fallback_used=False,
        fetched_monotonic=time.monotonic(),
        fetched_at=now,
        latency_ms=1.0,
    )
    record = server.record_stats_snapshot(snapshot)
    assert record is not None
    assert record["aircraft"] == 1
    summary = server.stats_summary(now.date().isoformat(), 1, 60, 50, 1)
    assert summary["unique"] == 1
    assert summary["registrations"] == 1
    assert summary["samples"] == 1
    backups = list((tmp_path / "backups").glob("adsb-stats-*.db"))
    assert len(backups) == 1
    for day in range(1, 6):
        (tmp_path / "backups" / f"adsb-stats-2020-01-0{day}.db").write_bytes(b"x")
    server.backup_stats_db(force=True)
    assert len(list((tmp_path / "backups").glob("adsb-stats-*.db"))) <= 3
    health = server.stats_health()
    assert health["stats_last_sample_age_s"] is not None
    assert health["stats_backup_at"] is not None
    assert health["stats_backup_keep"] == 3


def test_parse_metar_visibility():
    assert server.parse_metar_visibility_sm("CAVOK") == pytest.approx(6.21)
    assert server.parse_metar_visibility_sm("10SM") == pytest.approx(10.0)
    assert server.parse_metar_visibility_sm("1/2SM") == pytest.approx(0.5)
    assert server.parse_metar_visibility_sm("9999") == pytest.approx(6.21)
    assert server.parse_metar_visibility_sm("////") is None
