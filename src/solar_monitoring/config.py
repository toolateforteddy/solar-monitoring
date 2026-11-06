"""Loads config.toml. Relative paths resolve against the config file's folder."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    gateway_host: str
    token_file: Path
    db_path: Path
    poll_interval_s: int = 60
    enlighten_email: str | None = None
    backup_dir: Path | None = None

    def read_token(self) -> str:
        if not self.token_file.exists():
            raise FileNotFoundError(
                f"No gateway token at {self.token_file}. Run `solar-collector token` first."
            )
        return self.token_file.read_text(encoding="utf-8").strip()


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    base = path.parent

    def resolve(p: str) -> Path:
        candidate = Path(p).expanduser()
        return candidate if candidate.is_absolute() else base / candidate

    gateway = raw.get("gateway", {})
    collector = raw.get("collector", {})
    enlighten = raw.get("enlighten", {})

    if not gateway.get("host"):
        raise ValueError("config: [gateway] host is required")

    backup_dir = collector.get("backup_dir") or None
    return Config(
        gateway_host=gateway["host"],
        token_file=resolve(gateway.get("token_file", "gateway.token")),
        db_path=resolve(collector.get("db_path", "solar.db")),
        poll_interval_s=int(collector.get("poll_interval_s", 60)),
        enlighten_email=enlighten.get("email") or None,
        backup_dir=resolve(backup_dir) if backup_dir else None,
    )
