"""Official air-quality readings from the EEA Air Quality Download Service (no key, CC BY 4.0).

Romania's national monitoring network (ANPM) reports here. Dataset 1 is the provisional "up-to-date" stream,
published within a few hours; dataset 2 is the verified data that follows the next year. Hourly times in the
files are UTC+1 and are converted to plain UTC. A trimmed, standalone copy of the EEA code of the analysis app
used to build this project, so the hourly job needs nothing outside this folder.

API: https://eeadmz1-downloads-api-appservice.azurewebsites.net/swagger/index.html
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://eeadmz1-downloads-api-appservice.azurewebsites.net"
POLLUTANTS = {"pm25": ("PM2.5", 6001), "pm10": ("PM10", 5)}   # our name -> (EEA notation, EEA pollutant code)
USER_AGENT = "adia-air-job/1.0 (hourly outdoor air page)"

_session: requests.Session | None = None


def http() -> requests.Session:
    """One shared session that retries on rate limits and server errors."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        retry = Retry(total=4, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None)
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
    return _session


def _plain_utc(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _urls(countries: list[str], pollutants: list[str], dataset: int, start: datetime, end: datetime) -> list[str]:
    body = {
        "countries": countries,
        "cities": [],
        "pollutants": pollutants,
        "dataset": dataset,
        "dateTimeStart": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dateTimeEnd": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregationType": "hour",
    }
    r = http().post(f"{API}/ParquetFile/urls", json=body, timeout=180)
    r.raise_for_status()
    return [line.strip() for line in r.text.splitlines() if line.strip().startswith("http")]


def fetch(codes: set[str], pollutants: list[str], start: datetime, end: datetime) -> pl.DataFrame:
    """Hourly readings for the given station codes: station_id ("eea:<code>"), pollutant, ts (plain UTC), value.

    Only valid values (EEA validity > 0). A station with several sampling points for one pollutant is averaged.
    """
    start, end = _plain_utc(start), _plain_utc(end)
    countries = sorted({c[:2] for c in codes})
    names = [POLLUTANTS[p][0] for p in pollutants if p in POLLUTANTS]
    datasets = [1] + ([2] if start.year < datetime.now(timezone.utc).year else [])
    folder = Path(tempfile.mkdtemp(prefix="eea_"))   # always fresh files, never a cached copy

    frames = []
    for ds in datasets:
        urls = [u for u in _urls(countries, names, ds, start, end) if any(f"{c}_" in u.rsplit("/", 1)[-1] for c in codes)]
        print(f"  eea: dataset {ds}, {len(urls)} series", flush=True)
        for url in urls:
            r = http().get(url, timeout=180)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            fname = url.rsplit("/", 1)[-1]
            path = folder / f"ds{ds}_{fname}"
            path.write_bytes(r.content)
            code = next(c for c in codes if f"{c}_" in fname)
            df = pl.read_parquet(path, columns=["Samplingpoint", "Pollutant", "Start", "Value", "AggType", "Validity"])
            frames.append(df.with_columns(pl.lit(code).alias("code"), pl.lit(ds).alias("ds")))
    if not frames:
        return pl.DataFrame(schema={"station_id": pl.Utf8, "pollutant": pl.Utf8, "ts": pl.Datetime("us"), "value": pl.Float64})

    inv = {v[1]: k for k, v in POLLUTANTS.items()}
    df = (
        pl.concat(frames, how="vertical_relaxed")
        .filter((pl.col("AggType") == "hour") & (pl.col("Validity") > 0))   # -1 / -99 = invalid
        .with_columns(
            (pl.col("Start").cast(pl.Datetime("us")) - pl.duration(hours=1)).alias("ts"),   # UTC+1 -> UTC
            pl.col("Value").cast(pl.Float64).alias("value"),
            pl.col("Pollutant").replace_strict(inv, default=None, return_dtype=pl.Utf8).alias("pollutant"),
            ("eea:" + pl.col("code")).alias("station_id"),
        )
        .filter(pl.col("pollutant").is_not_null() & (pl.col("ts") >= start) & (pl.col("ts") < end))
        .sort("ds")
        .unique(["Samplingpoint", "ts"], keep="last")   # verified (dataset 2) wins over provisional for the same hour
    )
    return df.group_by(["station_id", "pollutant", "ts"]).agg(pl.col("value").mean())
