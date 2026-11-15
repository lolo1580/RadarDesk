import base64

import pytest
from pydantic import ValidationError

from admin import server


def payload(**overrides):
    values = {
        "lat": 46.818,
        "lon": 6.94,
        "dist_nm": 50,
        "max_radius_nm": 150,
        "refresh_ms": 3000,
        "cache_ttl_s": 4.0,
        "stale_ttl_s": 120.0,
        "primary_provider": "airplanes_live",
        "fallback_provider": "adsb_lol",
        "primary_cooldown_s": 600.0,
        "reception_mode": "local",
        "local_json_url": "http://192.168.30.13:8080/data/aircraft.json",
        "weather_metar_provider": "aviationweather",
        "radar_style": "phosphor",
        "location_mode": "coords",
        "influx_enabled": False,
        "influx_url": "",
        "influx_org": "",
        "influx_bucket": "",
        "influx_token": "",
        "influx_measurement": "adsb_stats",
        "influx_interval_s": 30,
    }
    values.update(overrides)
    return values


def test_credentials_are_valid_and_invalid():
    token = base64.b64encode(b"test-admin:test-password").decode()
    assert server.credentials_are_valid(f"Basic {token}") is True
    bad = base64.b64encode(b"test-admin:wrong").decode()
    assert server.credentials_are_valid(f"Basic {bad}") is False
    assert server.credentials_are_valid(None) is False


def test_runtime_payload_accepts_local_config():
    config = server.RuntimeConfigPayload(**payload())
    assert config.reception_mode == "local"
    assert config.local_json_url.endswith("aircraft.json")


def test_runtime_payload_rejects_incoherent_values():
    with pytest.raises(ValidationError):
        server.RuntimeConfigPayload(**payload(dist_nm=100, max_radius_nm=50))
    with pytest.raises(ValidationError):
        server.RuntimeConfigPayload(
            **payload(reception_mode="local", local_json_url="")
        )
    with pytest.raises(ValidationError):
        server.RuntimeConfigPayload(
            **payload(local_json_url="ftp://receiver/aircraft.json")
        )
