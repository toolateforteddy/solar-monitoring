"""Client for the IQ Gateway (Envoy) local API.

Each poll produces a `Snapshot`: the gateway's lifetime energy counters (Wh)
plus instantaneous power (W). Counters are the source of truth; energy per
interval is the difference between two snapshots, so a missed poll loses
detail but never energy.

Counter names:
  production         energy produced (production CT, else inverter total)
  net_import         energy drawn from the grid (net-consumption CT)
  net_export         energy sent to the grid (net-consumption CT)
  total_consumption  energy used by the house (total-consumption CT)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import requests
import urllib3

# The gateway serves HTTPS with a self-signed certificate.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class Snapshot:
    ts: int
    counters: dict[str, float] = field(default_factory=dict)
    power: dict[str, float] = field(default_factory=dict)


class GatewayAuthError(RuntimeError):
    pass


def parse_meter_readings(meters: list[dict], readings: list[dict]) -> Snapshot | None:
    """Build a snapshot from /ivp/meters and /ivp/meters/readings.

    Returns None when no CT meters are enabled (systems without CTs).
    """
    kinds = {
        m["eid"]: m.get("measurementType")
        for m in meters
        if m.get("state") == "enabled"
    }
    snap = Snapshot(ts=0)
    for r in readings:
        kind = kinds.get(r.get("eid"))
        if kind is None:
            continue
        snap.ts = max(snap.ts, int(r.get("timestamp") or 0))
        delivered = float(r.get("actEnergyDlvd", 0.0))
        received = float(r.get("actEnergyRcvd", 0.0))
        demand = float(r.get("instantaneousDemand", r.get("activePower", 0.0)))
        if kind == "production":
            snap.counters["production"] = delivered
            snap.power["production"] = demand
        elif kind == "net-consumption":
            snap.counters["net_import"] = delivered
            snap.counters["net_export"] = received
            snap.power["net"] = demand
        elif kind == "total-consumption":
            snap.counters["total_consumption"] = delivered
            snap.power["consumption"] = demand
    return snap if snap.counters else None


def parse_production_json(data: dict) -> Snapshot:
    """Fallback for systems without CTs: the inverters' lifetime total."""
    for entry in data.get("production", []):
        if entry.get("type") == "inverters":
            return Snapshot(
                ts=int(entry.get("readingTime") or 0),
                counters={"production": float(entry["whLifetime"])},
                power={"production": float(entry.get("wNow", 0.0))},
            )
    raise ValueError("production.json has no inverters entry")


def parse_serial(info_xml: str) -> str | None:
    match = re.search(r"<sn>\s*([0-9A-Za-z]+)\s*</sn>", info_xml)
    return match.group(1) if match else None


class Gateway:
    def __init__(self, host: str, token: str | None = None, timeout: float = 15):
        self.base = f"https://{host}"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = False
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self._meters: list[dict] | None = None

    def _get(self, path: str) -> requests.Response:
        resp = self.session.get(self.base + path, timeout=self.timeout)
        if resp.status_code in (401, 403):
            raise GatewayAuthError(
                f"Gateway refused the token ({resp.status_code}). "
                "It may have expired: run `solar-collector token` for a new one."
            )
        resp.raise_for_status()
        return resp

    def serial(self) -> str | None:
        return parse_serial(self._get("/info").text)

    def snapshot(self) -> Snapshot:
        if self._meters is None:
            self._meters = self._get("/ivp/meters").json()
        snap = None
        if self._meters:
            snap = parse_meter_readings(self._meters, self._get("/ivp/meters/readings").json())
        if snap is None:
            snap = parse_production_json(self._get("/production.json?details=1").json())
        if not snap.ts:
            snap.ts = int(time.time())
        return snap
