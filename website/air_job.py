"""Hourly data for the "Aerul de lângă tine" page: writes data/air.json.

Sources (all free for this use, with credit on the page):
  * now:      official stations of Romania's national air-quality monitoring network, through the
              European Environment Agency's Air Quality Download Service (CC BY). These are
              provisional (unverified) values. Fetched by eea.py next to this file.
  * forecast: FMI SILAM Europe air-quality forecast (CC BY 4.0), one point per area, from FMI's
              public THREDDS server (no account).

The browser does the rest: it finds the visitor's spot, weights the nearby stations by distance
and starts the forecast from that local reading. Nothing about the visitor is sent anywhere.

    python air_job.py                    # write data/air.json
    python air_job.py --refresh-stations # rebuild data/stations.json (needs the aq-compare app next to this folder)
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl

import eea
from eea import http

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATIONS = DATA / "stations.json"
SILAM = "https://thredds.silam.fmi.fi/thredds/ncss/grid/silam_europe_v6_1/silam_europe_v6_1_best.ncd"
SILAM_VARS = {"pm25": "cnc_PM2_5", "pm10": "cnc_PM10"}
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
PAST_H, AHEAD_H = 48, 48  # hours of readings sent to the page, hours of forecast after the latest reading
SCALE_H = 7 * 24  # the forecast is compared with the stations over this many hours
LABELS = {"bucharest": "București", "buzau": "Buzău"}
# The page's own limits, µg/m³: [good up to, smog above]. PM2.5 25 is the alert level used everywhere
# in the project. Buzău only measures PM10; hourly PM2.5 is about 0.78 x PM10 at the Bucharest
# stations, so PM10 20 / 30 is close to PM2.5 15 / 25.
LIMITS = {"pm25": [15, 25], "pm10": [20, 30]}
TYPES = {"traffic": "traffic", "background": "background", "industrial": "industrial"}
# The two areas: centre and radius in km (the same as the analysis app's config.toml)
AREAS = [
    {"name": "bucharest", "lat": 44.4268, "lon": 26.1025, "radius_km": 15.0},
    {"name": "buzau", "lat": 45.15, "lon": 26.82, "radius_km": 8.0},
]
# European Air Quality Index, revised hourly bands (EEA): [(upper limit µg/m³, level), ...]
EAQI = {
    "pm25": [(5, "Good"), (15, "Fair"), (50, "Moderate"), (90, "Poor"), (140, "Very poor"), (math.inf, "Extremely poor")],
    "pm10": [(15, "Good"), (45, "Fair"), (120, "Moderate"), (195, "Poor"), (270, "Very poor"), (math.inf, "Extremely poor")],
}


# ---------------------------------------------------------------- station list


def _place(lat: float, lon: float) -> str | None:
    """Neighbourhood name from OpenStreetMap (Nominatim), e.g. 'Drumul Taberei'."""
    r = http().get(NOMINATIM, params=dict(format="jsonv2", lat=lat, lon=lon, zoom=14, addressdetails=1), timeout=30)
    if not r.ok:
        return None
    a = r.json().get("address", {})
    name = next((a[k] for k in ("suburb", "quarter", "neighbourhood", "village", "town", "city_district") if a.get(k)), None)
    # OpenStreetMap sometimes has the old cedilla letters; Romanian uses the comma-below ones
    return name.translate(str.maketrans("şţŞŢ", "șțȘȚ")) if name else None


def refresh_stations() -> None:
    """Official stations per area from the analysis app's station list (run `python -m aqcompare discover` first).

    Only run on the computer that has the aq-compare app next to this folder; the hourly job doesn't need it.
    """
    sys.path.insert(0, str(HERE.parent / "aq-compare"))
    from aqcompare.common import load_stations

    old = {r["code"]: r for r in json.loads(STATIONS.read_text(encoding="utf-8"))} if STATIONS.exists() else {}
    rows = []
    for s in load_stations().filter(pl.col("source") == "eea").iter_rows(named=True):
        code = json.loads(s["extra"])["code"]
        kind, pollutants = (s["info"].split(";") + [""])[:2]
        place = old.get(code, {}).get("place")
        if place is None:
            place = _place(s["lat"], s["lon"])
            time.sleep(1.1)  # Nominatim allows one request per second
        rows.append(dict(code=code, name=s["name"], place=place, lat=round(s["lat"], 5), lon=round(s["lon"], 5), area=s["area"],
                         type=next((v for k, v in TYPES.items() if k in kind), "other"),
                         pm25="PM2.5" in pollutants, pm10="PM10" in pollutants))
    DATA.mkdir(exist_ok=True)
    STATIONS.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(rows)} stations to {STATIONS}")


# ---------------------------------------------------------------- quality control


def clean(meas: pl.DataFrame, rows: list[dict]) -> tuple[pl.DataFrame, dict[str, int]]:
    """Drop readings that are very likely wrong. The values are provisional, so the network has not checked them yet.

    * zero or negative: below what the instruments can resolve, or a fault
    * stuck: the same value 6 hours or more in a row
    * spike: more than 3x the median of the area's other stations that hour, plus 30 µg/m³
    """
    area_of = {f"eea:{r['code']}": r["area"] for r in rows}
    df = meas.with_columns(pl.col("station_id").replace_strict(area_of, default=None).alias("area")).sort("station_id", "pollutant", "ts")
    n0 = df.height
    df = df.filter(pl.col("value") > 0)
    dropped = {"zero_or_negative": n0 - df.height}

    run = (pl.col("value") != pl.col("value").shift(1)).fill_null(True).cum_sum().over("station_id", "pollutant")
    df = df.with_columns(run.alias("run"))
    df = df.with_columns(pl.len().over("station_id", "pollutant", "run").alias("run_len"))
    n1 = df.height
    df = df.filter(pl.col("run_len") < 6).drop("run", "run_len")
    dropped["stuck"] = n1 - df.height

    hourly = df.group_by("area", "pollutant", "ts").agg(pl.col("value").median().alias("med"), pl.len().alias("n"))
    df = df.join(hourly, on=["area", "pollutant", "ts"], how="left")
    spike = (pl.col("n") >= 3) & (pl.col("value") > 3 * pl.col("med") + 30)
    n2 = df.height
    df = df.filter(~spike).drop("med", "n", "area")
    dropped["spike"] = n2 - df.height
    return df, dropped


# ---------------------------------------------------------------- forecast


def silam(lat: float, lon: float, start: datetime, end: datetime) -> dict[str, list]:
    """Hourly SILAM forecast in µg/m³ at one point, {pollutant: [(ts, value), ...]}; times are plain UTC."""
    params = [("var", v) for v in SILAM_VARS.values()] + [
        ("latitude", f"{lat:.3f}"), ("longitude", f"{lon:.3f}"), ("vertCoord", "12.5"), ("accept", "csv"),
        ("time_start", start.strftime("%Y-%m-%dT%H:%M:%SZ")), ("time_end", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ]
    r = http().get(SILAM, params=params, timeout=120)
    r.raise_for_status()
    df = pl.read_csv(io.StringIO(r.text))
    ts = df[df.columns[0]].str.strip_chars_end("Z").str.to_datetime("%Y-%m-%dT%H:%M:%S").dt.cast_time_unit("us")
    out = {}
    for p, var in SILAM_VARS.items():
        col = next((c for c in df.columns if c.startswith(var + "[")), None)
        if col is not None:
            out[p] = list(zip(ts.to_list(), (df[col] * 1e9).to_list()))  # kg/m³ -> µg/m³
    return out


def series(points: list[tuple[datetime, float]] | pl.DataFrame, axis: list[datetime]) -> list[float | None]:
    """Values on the hour axis, None where missing, rounded to 0.1."""
    if isinstance(points, pl.DataFrame):
        points = list(zip(points["ts"].to_list(), points["value"].to_list()))
    by_ts = {t: v for t, v in points if v is not None and math.isfinite(v)}
    return [round(by_ts[t], 1) if t in by_ts else None for t in axis]


def bias(obs_median: dict[datetime, float], forecast: list[tuple[datetime, float]]) -> float:
    """Typical gap between the stations and SILAM over the last week (median of station median - SILAM), in µg/m³.

    Added, not multiplied: over the last 30 days SILAM's level was right on average (x0.97), but in a
    clean week its small values can be 40% low, and a multiplier would then inflate the next smog
    episode by the same 40%. Limited to +-10 µg/m³.
    """
    gaps = [obs_median[t] - v for t, v in forecast if t in obs_median and v is not None and math.isfinite(v)]
    if len(gaps) < 48:
        return 0.0
    return round(min(max(float(np.median(gaps)), -10.0), 10.0), 1)


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh-stations", action="store_true")
    ap.add_argument("--out", default=str(DATA / "air.json"))
    args = ap.parse_args()
    if args.refresh_stations:
        refresh_stations()

    rows = json.loads(STATIONS.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    meas = eea.fetch({r["code"] for r in rows}, ["pm25", "pm10"], now - timedelta(hours=SCALE_H + 6), now + timedelta(hours=1))
    if meas.is_empty():
        raise SystemExit("EEA returned no readings: keeping the previous page live")
    meas, dropped = clean(meas, rows)   # times are plain UTC from here on (no time-zone database needed)

    per_hour = meas.group_by("ts").agg(pl.col("station_id").n_unique().alias("n"))
    ok = per_hour.filter(pl.col("n") >= max(1, len(rows) / 2))
    last = (ok if ok.height else per_hour)["ts"].max()  # newest hour with at least half of the stations
    axis = [last - timedelta(hours=PAST_H - 1 - i) for i in range(PAST_H)]  # readings: 48 h up to `last`
    faxis = [last - timedelta(hours=PAST_H - 1 - i) for i in range(PAST_H + AHEAD_H)]  # forecast: same start, 48 h further

    out_stations = []
    for r in rows:
        sid = f"eea:{r['code']}"
        entry = dict(code=r["code"], name=r["name"], place=r.get("place"), type=r.get("type"), lat=r["lat"], lon=r["lon"], area=r["area"])
        for p in ("pm25", "pm10"):
            vals = series(meas.filter((pl.col("station_id") == sid) & (pl.col("pollutant") == p)), axis)
            if any(v is not None for v in vals):
                entry[p] = vals
        if "pm25" in entry or "pm10" in entry:
            out_stations.append(entry)

    areas = []
    for a in AREAS:
        here = {f"eea:{r['code']}" for r in rows if r["area"] == a["name"]}
        has = {p for p in ("pm25", "pm10") if any(s.get(p) for s in out_stations if s["area"] == a["name"])}
        main_p = "pm25" if "pm25" in has else "pm10"  # Buzău has no PM2.5 station
        area = dict(name=a["name"], label=LABELS.get(a["name"], a["name"].capitalize()), lat=a["lat"], lon=a["lon"],
                    radius_km=a["radius_km"], pollutant=main_p, forecast=None, bias=None)
        try:
            f = silam(a["lat"], a["lon"], last - timedelta(hours=SCALE_H), faxis[-1])
            area["forecast"] = {p: series(f[p], faxis) for p in f}
            area["bias"] = {}
            for p in f:
                m = meas.filter(pl.col("station_id").is_in(list(here)) & (pl.col("pollutant") == p))
                med = dict(m.group_by("ts").agg(pl.col("value").median()).iter_rows())
                area["bias"][p] = bias(med, f[p])
        except Exception as e:  # the page still shows the readings without a forecast
            print(f"SILAM forecast failed for {a['name']}: {e}", file=sys.stderr)
        areas.append(area)

    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731  (t is UTC)
    doc = dict(
        generated=iso(datetime.now(timezone.utc)),
        latest=iso(last),
        start=iso(axis[0]),  # start (UTC) of the hour of the first value in every series; one value per hour
        limits=LIMITS,
        bands={p: [[None if math.isinf(hi) else hi, label] for hi, label in EAQI[p]] for p in ("pm25", "pm10")},
        dropped=dropped,
        areas=areas,
        stations=out_stations,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_now = sum(1 for s in out_stations for p in ("pm25", "pm10") if s.get(p) and s[p][-1] is not None)
    print(f"wrote {out}: latest hour {iso(last)}, {len(out_stations)} stations ({n_now} readings that hour), "
          f"forecast for {sum(a['forecast'] is not None for a in areas)}/{len(areas)} areas, "
          f"bias {[a['bias'] for a in areas]}, dropped {dropped}, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
