import pytest

from solar_monitoring.gateway import Snapshot
from solar_monitoring.store import Store, intervals


def snap(ts, **counters):
    return Snapshot(ts=ts, counters=counters, power={"production": 1000.0})


def test_net_ct_intervals_derive_consumption():
    prev = snap(0, production=1000, net_import=500, net_export=200)
    cur = snap(60, production=1100, net_import=520, net_export=260)
    assert intervals(prev, cur) == {
        "production": 100,
        "grid_import": 20,
        "grid_export": 60,
        "consumption": 60,  # 100 produced + 20 imported - 60 exported
    }


def test_total_ct_intervals_split_net_by_sign():
    prev = snap(0, production=1000, total_consumption=800)
    exporting = snap(60, production=1100, total_consumption=830)
    assert intervals(prev, exporting) == {
        "production": 100,
        "consumption": 30,
        "grid_import": 0.0,
        "grid_export": 70,
    }
    importing = snap(60, production=1010, total_consumption=850)
    assert intervals(prev, importing)["grid_import"] == 40


def test_counter_reset_drops_that_counter():
    prev = snap(0, production=1000, net_import=500, net_export=200)
    cur = snap(60, production=5, net_import=520, net_export=260)
    out = intervals(prev, cur)
    assert "production" not in out
    assert "consumption" not in out
    assert out["grid_import"] == 20


def test_production_only():
    assert intervals(snap(0, production=10), snap(60, production=25)) == {"production": 15}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "solar.db")
    yield s
    s.close()


def test_record_builds_intervals_and_skips_repeats(store):
    assert store.record(snap(1000, production=0)) == {}
    assert store.record(snap(1060, production=50)) == {"production": 50}
    assert store.record(snap(1060, production=50)) == {}  # same gateway timestamp
    rows = store.conn.execute("SELECT metric, ts_start, ts_end, wh FROM energy_reading").fetchall()
    assert rows == [("production", 1000, 1060, 50.0)]


def test_gap_is_healed_by_counters_and_reported(tmp_path):
    path = tmp_path / "solar.db"
    first = Store(path)
    first.record(snap(1000, production=0))
    first.close()

    # Desktop slept for 8 hours; a new process picks up from the stored snapshot.
    second = Store(path)
    second.record(snap(1000 + 8 * 3600, production=4000))
    second.record(snap(1060 + 8 * 3600, production=4010))
    status = second.status(gap_threshold_s=180)
    assert status.snapshots == 3
    assert status.gaps == [(1000, 1000 + 8 * 3600)]
    total = second.conn.execute("SELECT SUM(wh) FROM energy_reading").fetchone()[0]
    assert total == 4010
    second.close()


def test_backup_copies_data(store, tmp_path):
    store.record(snap(1000, production=0))
    store.record(snap(1060, production=50))
    store.backup(tmp_path / "copy.db")
    copy = Store(tmp_path / "copy.db")
    assert copy.conn.execute("SELECT COUNT(*) FROM energy_reading").fetchone()[0] == 1
    copy.close()
