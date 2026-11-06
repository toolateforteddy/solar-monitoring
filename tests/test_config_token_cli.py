import base64
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from solar_monitoring import cli
from solar_monitoring.config import load_config
from solar_monitoring.gateway import Snapshot
from solar_monitoring.token import days_until_expiry, token_expiry


def make_jwt(exp: int) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'ES256'})}.{part({'exp': exp, 'enphaseUser': 'owner'})}.sig"


def test_token_expiry():
    exp = int(datetime(2027, 11, 1, tzinfo=timezone.utc).timestamp())
    token = make_jwt(exp)
    assert token_expiry(token) == datetime(2027, 11, 1, tzinfo=timezone.utc)
    now = datetime(2027, 10, 22, tzinfo=timezone.utc)
    assert days_until_expiry(token, now) == pytest.approx(10)


def test_token_expiry_unreadable():
    assert token_expiry("not-a-jwt") is None


def write_config(tmp_path, extra=""):
    path = tmp_path / "config.toml"
    path.write_text(
        '[gateway]\nhost = "192.168.1.50"\ntoken_file = "gateway.token"\n'
        f'[collector]\npoll_interval_s = 30\ndb_path = "data/solar.db"\n{extra}'
    )
    return path


def test_config_paths_resolve_next_to_config(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.gateway_host == "192.168.1.50"
    assert cfg.token_file == tmp_path / "gateway.token"
    assert cfg.db_path == tmp_path / "data" / "solar.db"
    assert cfg.poll_interval_s == 30
    assert cfg.backup_dir is None


def test_config_requires_host(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[gateway]\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_once_and_status(tmp_path, monkeypatch, capsys):
    path = write_config(tmp_path)
    (tmp_path / "data").mkdir()
    exp = int((datetime.now(timezone.utc) + timedelta(days=300)).timestamp())
    (tmp_path / "gateway.token").write_text(make_jwt(exp))

    snaps = iter([
        Snapshot(ts=1762430400, counters={"production": 1000.0}, power={"production": 900.0}),
        Snapshot(ts=1762430460, counters={"production": 1015.0}, power={"production": 900.0}),
    ])
    monkeypatch.setattr(cli.Gateway, "snapshot", lambda self: next(snaps))

    assert cli.main(["--config", str(path), "once"]) == 0
    assert cli.main(["--config", str(path), "once"]) == 0
    assert "production" in capsys.readouterr().out

    assert cli.main(["--config", str(path), "status"]) == 0
    out = capsys.readouterr().out
    assert "Snapshots: 2" in out
    assert re.search(r"expires in (299|300) days", out)
