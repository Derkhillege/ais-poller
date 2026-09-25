# ais-poller

Polls the EuRIS AIS WebSocket and appends every returned vessel feature as one JSON line
(NDJSON) to a file, then parses those files into flat vessel records (CSV or NDJSON).

Both commands stream line by line, so memory stays flat no matter how long the poller runs
or how large the output file grows.

## Install

```sh
uv tool install .          # installs the `ais-poller` command globally
# or, for development
uv sync
uv run ais-poller --help
```

## Poll

### Multiple areas in parallel (TOML config)

```sh
cp ais-poller.example.toml ais-poller.toml   # edit url, interval, request and areas
ais-poller poll --config ais-poller.toml
```

The config holds a shared request template and a list of `[[areas]]`, each with a `name` and a
`bbox = [minLat, minLon, maxLat, maxLon]`. Every area is polled on its own WebSocket in its own
thread, with its own copy of the request (bounding box filled into
`params.filters.boundingBox`, own `id` counter) and its own output file,
`<output_dir>/<name>.ndjson` unless the area sets `output`. Records are tagged with `area`.
`--url`, `--interval` and `-o` (the output directory) override the file.

### Single area (env vars)

```sh
ais-poller poll            # `poll` is the default command, so plain `ais-poller` works too
ais-poller poll --interval 30 -o out/ais.ndjson --env-file prod.env
```

Without `--config`, configuration comes from env vars (or a `.env` file, see `.env.example`) and
`REQUEST_PARAMS` is sent as is. CLI flags override them.

| Env var          | Flag           | Required | Description                                                  |
|------------------|----------------|----------|--------------------------------------------------------------|
| `EURIS_WSS`      | `--url`        | yes      | WebSocket URL                                                |
| `REQUEST_PARAMS` | –              | yes      | JSON-RPC request object; its numeric `id` increments each tick |
| `INTERVAL`       | `--interval`   | yes      | Seconds between requests                                     |
| `OUTPUT_PATH`    | `-o/--output`  | no       | NDJSON output path (default `data.json`)                     |
| `LOG_LEVEL`      | `--log-level`  | no       | Logging level (default `INFO`)                               |

Each line is the raw GeoJSON Feature plus a `requested_at` UTC timestamp and the `area` name.
Stop with Ctrl-C / SIGTERM; all areas shut down together.

## Parse

```sh
ais-poller parse data.json -o vessels.csv
ais-poller parse data.json --format ndjson > vessels.ndjson
```

Output columns:

| Column           | Source                                                        |
|------------------|---------------------------------------------------------------|
| `mmsi`           | `MMSI`, falling back to `TrackID` / feature id when `0`       |
| `base_date_time` | `requested_at`                                                |
| `latitude`, `longitude` | `Lat`/`Lon`, falling back to the geometry              |
| `sog`, `cog`     | `SOG`, `COG`                                                  |
| `heading`        | `TH` if > 0, else `COG`                                       |
| `length`, `beam` | `DimA+DimB`, `DimC+DimD` (empty when 0)                       |
| `shiptypeAIS`    | `VT`, falling back to `VG`                                    |
| `status`, `status_label` | `ST` with its AIS navigational status label (15 = undefined) |
| `vessel_name`    | `Name`, falling back to `Track <mmsi>`                        |

Lines that are not valid JSON or cannot be parsed are skipped and counted in a warning.

## Inspect

```sh
ais-poller inspect data/*.ndjson          # per-file summary
ais-poller inspect --json data/*.ndjson   # machine-readable
```

Streams each file and reports record/tick counts, time span, median tick interval, records per
tick, distinct vessels, lat/lon extent, records per area, bad lines, out-of-order ticks and every
gap longer than 3× the median interval.

## Testing against the live server

The devcontainer cannot reach the EuRIS site, so live runs happen on a local machine:

```sh
ais-poller poll --config ais-poller.toml 2> poll.log     # let it run, then Ctrl-C
ais-poller inspect data/*.ndjson > inspect.txt
```

To have the results analysed in the devcontainer, copy them into `local-runs/<date>/`
(gitignored): `poll.log`, `inspect.txt`, the config used, and ideally the NDJSON files
(or a `head -n 2000` sample of each).

## Development

```sh
uv run pytest              # fast suite (uses tests/fixtures/sample.ndjson)
uv run pytest -m slow      # full data.json test set, if present
uv run ruff check . && uv run ruff format --check .
```
