from backend.influx_export import InfluxExporter, InfluxSettings


def settings(**overrides):
    values = {
        "enabled": True,
        "url": "http://influxdb:8086",
        "org": "radardesk",
        "bucket": "adsb",
        "token": "secret",
        "measurement": "adsb_stats",
        "airport_icao": "LSGG",
        "location_mode": "airport",
    }
    values.update(overrides)
    return InfluxSettings(**values)


def test_influx_ready_and_write_url():
    exporter = InfluxExporter(settings())
    assert exporter.ready() is True
    assert exporter.write_url() == (
        "http://influxdb:8086/api/v2/write"
        "?org=radardesk&bucket=adsb&precision=ns"
    )
    assert InfluxExporter(settings(enabled=False)).ready() is False
    assert InfluxExporter(settings(url="not-a-url")).ready() is False


def test_influx_line_contains_tags_and_fields():
    record = {
        "ts": 1_700_000_000,
        "source": "local",
        "counts": {
            "50": {
                "aircraft": 12,
                "airborne": 11,
                "ground": 1,
                "emergency": 0,
                "max_alt": 38_000,
                "average_alt": 21_500.5,
                "max_distance": 42.25,
            }
        },
        "unique_default": 120,
        "unique_registrations_default": 110,
        "registration_passes_default": 340,
    }
    line = InfluxExporter(settings()).line(record, 50)
    assert line.startswith("adsb_stats,")
    assert "source=local" in line
    assert "location=LSGG" in line
    assert "range_nm=50" in line
    assert "aircraft=12i" in line
    assert "unique_registrations_today=110i" in line
    assert line.endswith("1700000000000000000")
