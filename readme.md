# drv60870

A project for GOST R IEC 60870-5-104 communication with no external dependencies:

- `server.py` — server (RTU/slave)
- `client.py` — client (SCADA/master, up to 8 connections to RTUs)

## About the Project

`drv60870` is an educational and practical project for communication according to GOST R IEC 60870-5-104.
The project includes two roles:

- `server.py` — RTU simulator (slave), accepts SCADA connections and provides telemetry/accepts commands
- `client.py` — SCADA client (master), connects to RTUs, performs STARTDT and general interrogation

The main goal is a local test bench for 104 communication verification and SCADA scenario debugging without external dependencies.

## Requirements

- Python 3.10+
- Python standard library only

## Project Structure

```text
drv60870/
├── client.py          # IEC-104 client (up to 8 connections, CLI)
├── common.py          # config, state, storage, signal loading
├── const.py           # protocol constants
├── control_client.py  # client CLI commands
├── control_server.py  # server CLI commands
├── gen_dpl.py         # DPL generator for WinCC OA
├── imit.py            # signal simulation generators
├── log_viewer.py      # log viewer
├── protocol.py        # I/S/U frame and ASDU parsing/building
├── server.py          # IEC-104 server (accepts SCADA connections)
├── KP_1/, KP_2/       # RTU instance directories
│   ├── config.toml
│   └── signals.csv
├── PU_1/, PU_2/, PU_3/ # SCADA driver instance directories
│   ├── config.toml
│   └── run.cmd
├── readme.md
└── todo.md
```

## Configuration

- `config.toml`:
  - network: `bind_ip`, `port`, `allow_ip`, `max_clients`
  - protocol: `ca`, `t3`, `k`, `w`, `strict_coa`, `max_rx_buf`
  - logging: `name`, `file`, `levels`, `rotation`
  - client: `history_file` — TSV file for signal change history
  - `[[conn]]` — connection definitions for auto-connect (client only):
    - `name`, `ip`, `port`, `ca`, `auto_start`, `auto_gi`
- `signals.csv`:
  - signal fields: `id`, `ca`, `ioa`, `asdu`, `name`, `val`, `threshold`

`strict_coa`:

- `true` — strict mode: incoming ASDU is ignored when COA does not match.
- `false` — compatible mode: GI (`C_IC_NA_1`) is accepted even with COA mismatch; other ASDUs are ignored.

## Running

Run from the specific RTU directory (e.g., `KP_1`) to use local `config.toml` and `signals.csv`.

Server:

- `python ../server.py`

Client:

- `python ../client.py`

## CLI Commands

Server (`control_server.py`):

- `clients`
- `addr <name_pattern>`
- `set <value> <id> [quality]`
- `setioa <value> <ioa>`
- `imit_rand <cnt_time> <cnt_id>`
- `imit_ladder <cnt_step> <time_step> <val_step> <val_min> <val_max> <name_pattern>`
- `log_level <file|console> <DEBUG|INFO|WARNING|ERROR|CRITICAL>`
- `help`, `exit`

Client (`control_client.py`):

- `conn <name> <ip> <port> <ca>`
- `start <name>`
- `gi <name>`
- `disc <name>`
- `load` — auto-connect from `[[conn]]` in config.toml
- `clients`
- `help`, `exit`

## Supported Communication

- U-frames: `STARTDT`, `STOPDT`, `TESTFR`
- I/S-frames: acknowledgments by `w`, limitation by `k`
- Commands:
  - `C_IC_NA_1` (general interrogation)
  - `C_SC_NA_1` (single command)
  - `C_SE_NC_1` (floating-point setpoint)

## Notes

- Signal names are case-insensitive during search.
  