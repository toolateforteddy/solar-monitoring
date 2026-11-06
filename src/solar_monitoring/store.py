"""SQLite storage for gateway snapshots and the energy intervals derived from them."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .gateway import Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_counter (
    ts      INTEGER NOT NULL,          -- unix seconds, UTC
    counter TEXT    NOT NULL,
    wh      REAL    NOT NULL,          -- lifetime Wh
    PRIMARY KEY (ts, counter)
);
CREATE TABLE IF NOT EXISTS power_sample (
    ts     INTEGER NOT NULL,
    metric TEXT    NOT NULL,
    w      REAL    NOT NULL,
    PRIMARY KEY (ts, metric)
);
CREATE TABLE IF NOT EXISTS energy_reading (
    source   TEXT    NOT NULL,         -- gateway, enphase_cloud, eversource
    metric   TEXT    NOT NULL,         -- production, consumption, grid_import, grid_export
    ts_start INTEGER NOT NULL,
    ts_end   INTEGER NOT NULL,
    wh       REAL    NOT NULL,
    PRIMARY KEY (source, metric, ts_start)
);
"""


def intervals(prev: Snapshot, cur: Snapshot) -> dict[str, float]:
    """Energy per metric between two snapshots.

    A counter that went backwards (gateway or meter reset) is dropped for
    this interval rather than recorded as negative energy.
    """
    delta = {
        name: cur.counters[name] - prev.counters[name]
        for name in cur.counters.keys() & prev.counters.keys()
        if cur.counters[name] >= prev.counters[name]
    }
    out: dict[str, float] = {}
    production = delta.get("production")
    if production is not None:
        out["production"] = production

    if "net_import" in delta and "net_export" in delta:
        out["grid_import"] = delta["net_import"]
        out["grid_export"] = delta["net_export"]
        if "total_consumption" in delta:
            out["consumption"] = delta["total_consumption"]
        elif production is not None:
            out["consumption"] = production + delta["net_import"] - delta["net_export"]
    elif "total_consumption" in delta:
        out["consumption"] = delta["total_consumption"]
        if production is not None:
            # Only the interval's net is known; fine at minute-scale polling.
            net = delta["total_consumption"] - production
            out["grid_import"] = max(net, 0.0)
            out["grid_export"] = max(-net, 0.0)
    return out


@dataclass
class Status:
    last_ts: int | None
    snapshots: int
    gaps: list[tuple[int, int]]


class Store:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def last_snapshot(self) -> Snapshot | None:
        row = self.conn.execute("SELECT MAX(ts) FROM gateway_counter").fetchone()
        if row[0] is None:
            return None
        ts = row[0]
        counters = dict(
            self.conn.execute("SELECT counter, wh FROM gateway_counter WHERE ts = ?", (ts,))
        )
        return Snapshot(ts=ts, counters=counters)

    def record(self, snap: Snapshot) -> dict[str, float]:
        """Save a snapshot and the interval since the previous one.

        Returns the interval's energy per metric (empty for the first snapshot
        or a repeated gateway timestamp).
        """
        prev = self.last_snapshot()
        if prev is not None and snap.ts <= prev.ts:
            return {}
        energy = intervals(prev, snap) if prev else {}
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO gateway_counter VALUES (?, ?, ?)",
                [(snap.ts, k, v) for k, v in snap.counters.items()],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO power_sample VALUES (?, ?, ?)",
                [(snap.ts, k, v) for k, v in snap.power.items()],
            )
            if prev is not None:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO energy_reading VALUES ('gateway', ?, ?, ?, ?)",
                    [(m, prev.ts, snap.ts, wh) for m, wh in energy.items()],
                )
        return energy

    def status(self, gap_threshold_s: int) -> Status:
        last_ts, count = self.conn.execute(
            "SELECT MAX(ts), COUNT(DISTINCT ts) FROM gateway_counter"
        ).fetchone()
        gaps = self.conn.execute(
            "SELECT DISTINCT ts_start, ts_end FROM energy_reading "
            "WHERE source = 'gateway' AND ts_end - ts_start > ? ORDER BY ts_start",
            (gap_threshold_s,),
        ).fetchall()
        return Status(last_ts=last_ts, snapshots=count, gaps=[tuple(g) for g in gaps])

    def daily_totals(self, days: int = 7) -> list[tuple[str, str, float]]:
        """(local date, metric, kWh) for the last `days` days, by interval end."""
        return self.conn.execute(
            "SELECT date(ts_end, 'unixepoch', 'localtime') AS day, metric, "
            "ROUND(SUM(wh) / 1000.0, 2) FROM energy_reading "
            "WHERE source = 'gateway' AND ts_end >= strftime('%s', 'now', ?) "
            "GROUP BY day, metric ORDER BY day, metric",
            (f"-{days} days",),
        ).fetchall()

    def backup(self, dest: str | Path) -> None:
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()
