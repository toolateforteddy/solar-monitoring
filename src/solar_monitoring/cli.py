"""Command line: solar-collector {run,once,token,status,backup}."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

from .config import Config, load_config
from .gateway import Gateway, GatewayAuthError
from .store import Store
from .token import days_until_expiry, fetch_token, token_expiry

log = logging.getLogger("solar_monitoring")

TOKEN_WARN_DAYS = 30
BACKUPS_KEPT = 14


def setup_logging(cfg: Config) -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(
        cfg.db_path.with_suffix(".log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)
    if sys.stderr is not None:  # None under pythonw.exe
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        log.addHandler(console)


def check_token(token: str) -> None:
    days = days_until_expiry(token)
    if days is not None and days < TOKEN_WARN_DAYS:
        log.warning("Gateway token expires in %.0f days: run `solar-collector token`.", days)


def backup(cfg: Config, store: Store) -> Path | None:
    if cfg.backup_dir is None:
        return None
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.backup_dir / f"solar-{date.today().isoformat()}.db"
    store.backup(dest)
    for old in sorted(cfg.backup_dir.glob("solar-*.db"))[:-BACKUPS_KEPT]:
        old.unlink()
    return dest


def poll_once(gateway: Gateway, store: Store) -> None:
    snap = gateway.snapshot()
    energy = store.record(snap)
    summary = ", ".join(f"{k} {v:.0f} Wh" for k, v in sorted(energy.items()))
    log.info("snapshot %s%s", snap.ts, f": {summary}" if summary else "")


def cmd_run(cfg: Config) -> int:
    token = cfg.read_token()
    check_token(token)
    gateway = Gateway(cfg.gateway_host, token)
    store = Store(cfg.db_path)
    last_check = date.today()
    log.info("Collector started: polling %s every %ss", cfg.gateway_host, cfg.poll_interval_s)
    while True:
        started = time.monotonic()
        try:
            poll_once(gateway, store)
        except GatewayAuthError as e:
            log.error("%s", e)
            # Pick up a token renewed while we run.
            token = cfg.read_token()
            gateway = Gateway(cfg.gateway_host, token)
        except (requests.RequestException, ValueError) as e:
            log.warning("Poll failed: %s", e)

        if date.today() != last_check:
            last_check = date.today()
            check_token(token)
            try:
                dest = backup(cfg, store)
                if dest:
                    log.info("Backed up to %s", dest)
            except OSError as e:
                log.warning("Backup failed: %s", e)

        time.sleep(max(1.0, cfg.poll_interval_s - (time.monotonic() - started)))


def cmd_once(cfg: Config) -> int:
    gateway = Gateway(cfg.gateway_host, cfg.read_token())
    snap = gateway.snapshot()
    print(f"Gateway time: {datetime.fromtimestamp(snap.ts)}")
    for name, wh in sorted(snap.counters.items()):
        print(f"  {name:<18} {wh / 1000:>12.1f} kWh lifetime")
    for name, w in sorted(snap.power.items()):
        print(f"  {name:<18} {w:>12.0f} W now")
    store = Store(cfg.db_path)
    store.record(snap)
    store.close()
    return 0


def cmd_token(cfg: Config, serial: str | None) -> int:
    email = cfg.enlighten_email or input("Enlighten email: ")
    password = os.environ.get("ENLIGHTEN_PASSWORD") or getpass.getpass("Enlighten password: ")
    if serial is None:
        serial = Gateway(cfg.gateway_host).serial()
        if serial is None:
            print("Could not read the gateway serial; pass --serial.", file=sys.stderr)
            return 1
    token = fetch_token(email, password, serial)
    cfg.token_file.write_text(token, encoding="utf-8")
    print(f"Saved token for gateway {serial} to {cfg.token_file}")
    expiry = token_expiry(token)
    if expiry:
        print(f"It expires {expiry:%Y-%m-%d}.")
    return 0


def cmd_status(cfg: Config) -> int:
    store = Store(cfg.db_path)
    status = store.status(gap_threshold_s=3 * cfg.poll_interval_s)
    if status.last_ts is None:
        print("No data yet.")
        return 0
    age = time.time() - status.last_ts
    print(f"Snapshots: {status.snapshots}; last {datetime.fromtimestamp(status.last_ts)} "
          f"({age / 60:.0f} min ago)")
    print(f"Gaps healed from counters (> {3 * cfg.poll_interval_s}s): {len(status.gaps)}")
    for start, end in status.gaps[-10:]:
        print(f"  {datetime.fromtimestamp(start)} to {datetime.fromtimestamp(end)}")
    if cfg.token_file.exists():
        days = days_until_expiry(cfg.read_token())
        if days is not None:
            print(f"Gateway token expires in {days:.0f} days.")
    print("Last 7 days (kWh):")
    for day, metric, kwh in store.daily_totals(7):
        print(f"  {day}  {metric:<12} {kwh:>8.2f}")
    return 0


def cmd_backup(cfg: Config) -> int:
    if cfg.backup_dir is None:
        print("Set [collector] backup_dir in the config first.", file=sys.stderr)
        return 1
    print(f"Backed up to {backup(cfg, Store(cfg.db_path))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solar-collector")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="poll the gateway forever")
    sub.add_parser("once", help="take one reading and print it")
    token = sub.add_parser("token", help="get a gateway token from Enphase")
    token.add_argument("--serial", help="gateway serial (read from the gateway if omitted)")
    sub.add_parser("status", help="show data health and recent daily totals")
    sub.add_parser("backup", help="copy the database to backup_dir")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.command == "run":
        setup_logging(cfg)
        return cmd_run(cfg)
    if args.command == "once":
        return cmd_once(cfg)
    if args.command == "token":
        return cmd_token(cfg, args.serial)
    if args.command == "status":
        return cmd_status(cfg)
    return cmd_backup(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
