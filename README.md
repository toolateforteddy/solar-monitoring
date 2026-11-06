# solar-monitoring

Tracks a home Enphase solar system toward its break-even date. Phase 1 is the
collector: it polls the IQ Gateway (Envoy) on the local network and stores its
lifetime energy counters in SQLite. Energy per interval is the difference
between two readings, so when the PC sleeps or is off, the next reading still
accounts for every Wh; only the detail inside the gap is lost.

Design doc: https://claude.ai/artifact/UALytqnEnDBW7eM8o2xqkf

## Setup on Windows

Needs Python 3.11 or newer ([python.org](https://www.python.org/downloads/)).

```powershell
git clone https://github.com/toolateforteddy/solar-monitoring
cd solar-monitoring
py -m venv .venv
.venv\Scripts\pip install -e .
copy config.example.toml config.toml
notepad config.toml   # set the gateway IP and your Enlighten email
```

Find the gateway IP in your router's device list (it often shows as `envoy`),
or try `https://envoy.local` in a browser.

Get a gateway token (valid about a year; the collector warns 30 days before
expiry). It asks for your Enlighten password and writes `gateway.token`,
which git ignores:

```powershell
.venv\Scripts\solar-collector token
```

Check a single reading, then install the background task that starts at logon:

```powershell
.venv\Scripts\solar-collector once
powershell -ExecutionPolicy Bypass -File scripts\install-windows-task.ps1
```

## Commands

| Command | What it does |
| --- | --- |
| `solar-collector run` | Poll forever (what the scheduled task runs). Logs to `solar.log`. |
| `solar-collector once` | One reading, printed and saved. |
| `solar-collector token` | Fetch a new gateway token from Enphase. |
| `solar-collector status` | Last reading, gaps, token expiry, daily kWh for the last week. |
| `solar-collector backup` | Copy the database to `backup_dir` (also done daily by `run`; keeps 14). |

## What gets stored

- `gateway_counter`: lifetime Wh per counter at each poll (production, and
  grid import/export or house consumption if consumption CTs are installed).
- `power_sample`: instantaneous watts at each poll.
- `energy_reading`: Wh per interval for `production`, `consumption`,
  `grid_import`, `grid_export`, derived from the counters.

## Development

```bash
pip install -e ".[dev]"
pytest
```
