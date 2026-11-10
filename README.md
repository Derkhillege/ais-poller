# ais-poller

Polls the EuRIS AIS WebSocket and writes every returned vessel feature either as one raw JSON
line (NDJSON) per feature, or parsed on arrival into flat vessel records in compressed
GeoParquet. NDJSON files are converted afterwards with `parse` (CSV or NDJSON).

Everything streams, so memory stays flat no matter how long the poller runs or how large the
output grows.

## Install

```sh
uv tool install .          # installs the `ais-poller` command globally
# or, for development
uv sync
uv run ais-poller --help
```

## Poll

```sh
cp ais-poller.example.toml ais-poller.toml   # edit url, interval, request and areas
ais-poller poll                              # reads ./ais-poller.toml; `poll` is the default command
ais-poller poll --config other.toml --interval 30 -o out/
```

The config holds a shared request template and a list of `[[areas]]`, each with a `name` and a
`bbox = [minLat, minLon, maxLat, maxLon]`. Every area is polled on its own WebSocket in its own
thread, with its own copy of the request (bounding box filled into
`params.filters.boundingBox`, own `id` counter) and its own output file,
`<output_path>/<name>.ndjson` unless the area sets `output`. Records are tagged with `area`.
For a single area, list just one `[[areas]]` entry.

| Flag             | Overrides    | Description                                              |
|------------------|--------------|----------------------------------------------------------|
| `-c/--config`    | –            | TOML config file (default `ais-poller.toml`)             |
| `--url`          | `url`        | WebSocket URL                                            |
| `--interval`     | `interval`   | Seconds between requests (streaming: between summary logs) |
| `-o/--output`    | `output_path` | Output directory                                         |
| `--format`       | `format`     | `ndjson` (default) or `parquet`                          |
| `--rotate`       | `rotate`     | Seconds per Parquet file (default 3600)                  |
| `--log-level`    | `$LOG_LEVEL` | Logging level (default `INFO`)                           |

Stop with Ctrl-C / SIGTERM; all areas shut down together.

### Output formats

**`format = "ndjson"`** (default, uncompressed): each line is the raw GeoJSON Feature plus a
`timestamp` UTC timestamp and the `area` name, appended as it arrives. Nothing is lost on a
hard kill. Convert with `ais-poller parse`.

**`format = "parquet"`** (compressed): every feature is parsed on arrival (same columns as
[Parse](#parse), `base_date_time` as a UTC timestamp) and streamed into zstd Parquet, around
50× smaller than the raw NDJSON. The layout is a Hive-partitioned dataset with one directory
per area and day:

```
data/area=moerdijk/year=2026/month=09/day=26/part-20260926T010000Z.parquet
data/area=moerdijk/year=2026/month=09/day=26/.part-20260926T020000Z.parquet.tmp   ← still being written
```

Each area thread writes its own files, and a new file starts every `rotate` seconds (aligned to
UTC). A Parquet file is only readable once it is closed, so the open file is a hidden `.tmp` that
is renamed on rotation and on Ctrl-C / SIGTERM. A hard kill (SIGKILL, power loss) loses the open
window. Files carry GeoParquet metadata and a WKB `geometry` point column next to
`latitude`/`longitude`.

Read the whole dataset in parallel with any engine; `area` and `date` come from the paths:

```sql
-- DuckDB
SELECT area, count(*), count(DISTINCT mmsi)
FROM read_parquet('data/**/*.parquet', hive_partitioning = true) GROUP BY area;
```

```python
pl.scan_parquet("data/**/*.parquet", hive_partitioning=True)  # Polars (lazy)
pd.read_parquet("data")  # pandas (pyarrow)
spark.read.parquet("data")  # Spark
```

QGIS (3.28+, GDAL ≥ 3.5) opens a single `part-*.parquet` directly as a point layer.

## Parse

```sh
ais-poller parse data.json -o vessels.csv
ais-poller parse data.json --format ndjson > vessels.ndjson
```

Output columns:

| Column           | Source                                                        |
|------------------|---------------------------------------------------------------|
| `mmsi`           | `MMSI`, falling back to `TrackID` / feature id when `0`       |
| `base_date_time` | `timestamp`                                                |
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
ais-poller inspect data/area=moerdijk     # a Parquet dataset directory (or single .parquet)
ais-poller inspect --json data/*.ndjson   # machine-readable
```

Streams each file or dataset and reports record/tick counts, time span, median tick interval, records per
tick, distinct vessels, lat/lon extent, records per area, bad lines, out-of-order ticks and every
gap longer than 3× the median interval.

## Testing against the live server

The devcontainer cannot reach the EuRIS site, so live runs happen on a local machine:

```sh
ais-poller poll 2> poll.log     # let it run, then Ctrl-C
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

### License headers

Every source file carries the GPL notice, kept in place by a pre-commit hook. Enable it once
per clone:

```sh
git config core.hooksPath .githooks
```

The hook inserts the notice into staged files that are missing it and re-stages them. A file
with unstaged changes is never re-staged: the notice is written to the working tree and the
commit is aborted so you can review it. The same tool runs by hand:

```sh
python3 scripts/add_license_headers.py            # whole repo, insert or refresh
python3 scripts/add_license_headers.py --check    # report only, exit 1 if any differ
```

The notice text is not hardcoded: project name, description and author come from the
`[project]` table of `pyproject.toml` and the year from `[tool.ais-poller] copyright-year`
(`src/ais_poller/metadata.py` resolves them, falling back to the installed distribution
metadata when there is no source tree). Change any of those and re-run the script — headers
that no longer match are rewritten in place, so the CLI notice and the file headers stay in
step.

It covers `.py`, `.sh`, `.toml`, `.yml`/`.yaml`, `.cfg`/`.ini` and extensionless scripts with a
shebang, and skips generated, vendored and data directories (see `SKIP_DIRS` in the script).

## License

ais-poller is free software under the GNU General Public License, version 3 or (at your option)
any later version — see [LICENSE](LICENSE). Started interactively it prints the short GPL
notice to stderr; `ais-poller license`, `ais-poller license --warranty` and
`ais-poller license --conditions` print it and the details on demand.

## What EuRIS actually sends

The AISTracks socket is undocumented, so every streaming run records the message kinds it sees
to `<output_dir>/protocol/<area>.json` — one entry per distinct message signature with its
count, first and last sighting, and one truncated sample (shapes, not a position archive).
Catalogues are written after each connection, so an interrupted run still leaves its evidence.

```sh
ais-poller protocol data/protocol              # table of message kinds, most frequent first
ais-poller protocol data/protocol --markdown   # evidence section for docs/euris-protocol.md
```

[`docs/euris-protocol.md`](docs/euris-protocol.md) records what is **observed** versus what is
still **assumed**, and is how an assumption gets promoted: paste the generated section in.

## Area handoffs

A subscription only reports tracks inside its own bounding box, so EuRIS announces a vessel
leaving one with an `outOfBoundingBox` notification. Those are recorded per area as JSON lines
in `<output_dir>/events/<area>.ndjson`, deliberately outside the vessel dataset:

```json
{"event": "exit", "area": "moerdijk", "observed_at": "2026-09-26T10:00:00+00:00",
 "track_id": "…", "mmsi": 244660000, "latitude": 51.7, "longitude": 4.3, "raw": {…}}
```

With adjoining areas, pairing an exit with the next ping of the same track elsewhere gives the
crossing:

```sh
ais-poller handoffs data                      # data/ holds events/ and the area datasets
ais-poller handoffs data --window 1800 --json # widen the match window, machine-readable
```

```text
exits: 2  handoffs: 1
       track  from         to              gap_s  exit_at
        t-1   moerdijk     rotterdam        30.0  2026-09-26T10:00:00+00:00
```

Each exit is matched to the *earliest* ping of that track in a different area within
`--window` seconds after it (and `--tolerance` seconds before it, for overlapping boxes or clock
skew), on either `track_id` or `mmsi`. It reads NDJSON and Parquet areas alike and streams them,
so memory grows with the number of exits, not the number of records.

## Conformance with `ais-shader`

The Parquet output follows the GeoParquet dataset specification of
[TUDelft-CITG/ais-colab](https://github.com/TUDelft-CITG/ais-colab)
(`docs/geoparquet_specification.md`): WKB `Point` geometry in WGS84 with GeoParquet `geo`
metadata, the spec's default column names and types (`mmsi`, `base_date_time` as
`timestamp[us, UTC]`, `longitude`, `latitude`, `sog`, `cog`, `heading`, `length`, `beam`,
`shiptypeAIS`, `status`), and Hive `year=`/`month=`/`day=` partition directories that
`ais_shader.data_loader.detect_hive_partitioning()` discovers.

### Testing conformance

```sh
ais-poller check data/area=moerdijk        # a dataset directory, or single .parquet files
ais-poller check local-runs/2026-09-26/*.parquet
```

It reports the file and row count, the partition keys it found, and every deviation from the
spec (missing or wrongly typed columns, absent `geo` metadata, a non-Hive layout, values outside
the documented ranges such as `sog` over 102.2 kn or a `heading` that is neither < 360 nor 511).
Exit status is 0 when the dataset conforms and 1 when it does not, so it drops into CI or a
post-run script. The same checks run in `uv run pytest tests/test_conformance.py`, which writes
a small dataset through the real sink, re-reads it with `pyarrow.dataset(..., partitioning="hive")`
the way the pipeline does, and asserts that a flat or metadata-less dataset is rejected.
