import json
from pathlib import Path

import pytest

from solar_monitoring.gateway import (
    Gateway,
    parse_meter_readings,
    parse_production_json,
    parse_serial,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_meter_readings_with_net_consumption_ct():
    snap = parse_meter_readings(load("meters.json"), load("meters_readings.json"))
    assert snap.ts == 1762430400
    assert snap.counters == {
        "production": 10312345.678,
        "net_import": 4200000.25,
        "net_export": 5400000.75,
    }
    assert snap.power == {"production": 4321.5, "net": -2900.0}


def test_total_consumption_ct():
    meters = load("meters.json")
    meters[1]["measurementType"] = "total-consumption"
    snap = parse_meter_readings(meters, load("meters_readings.json"))
    assert snap.counters["total_consumption"] == 4200000.25
    assert "net_import" not in snap.counters


def test_disabled_meters_are_ignored():
    meters = [dict(m, state="disabled") for m in load("meters.json")]
    assert parse_meter_readings(meters, load("meters_readings.json")) is None


def test_production_json_fallback():
    snap = parse_production_json(load("production.json"))
    assert snap.ts == 1762430400
    assert snap.counters == {"production": 10298000.0}
    assert snap.power == {"production": 4100.0}


def test_production_json_without_inverters():
    with pytest.raises(ValueError):
        parse_production_json({"production": []})


def test_parse_serial():
    assert parse_serial((FIXTURES / "info.xml").read_text()) == "122412345678"


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_snapshot_falls_back_when_no_meters(monkeypatch):
    responses = {
        "/ivp/meters": FakeResponse(200, []),
        "/production.json?details=1": FakeResponse(200, load("production.json")),
    }
    gw = Gateway("envoy.local", "tok")
    monkeypatch.setattr(gw.session, "get", lambda url, timeout: responses[url.removeprefix(gw.base)])
    assert gw.snapshot().counters == {"production": 10298000.0}
    assert gw.session.headers["Authorization"] == "Bearer tok"


def test_snapshot_raises_auth_error_on_401(monkeypatch):
    from solar_monitoring.gateway import GatewayAuthError

    gw = Gateway("envoy.local", "expired")
    monkeypatch.setattr(gw.session, "get", lambda url, timeout: FakeResponse(401, "Unauthorized"))
    with pytest.raises(GatewayAuthError):
        gw.snapshot()
