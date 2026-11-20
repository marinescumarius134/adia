"""Email the smog alert on evenings when the forecast shows smog tonight.

Runs in the hourly GitHub workflow after air_job.py. It only acts between 18:00 and 20:59 Romanian time,
sends at most one email per city per day (it first looks in Brevo for a campaign with the same name), and
uses the same estimate as the air page for the city centre, so the email and the page say the same thing.

    python smog_alert.py --dry-run                                # print the decision and the email, send nothing
    python smog_alert.py --dry-run --force --now 2026-11-20T16:30Z  # any time, as if it were that moment

Environment (GitHub > Settings > Secrets and variables > Actions):
    BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME (optional, default "Adia"),
    BREVO_LIST_SMOG_BUCURESTI, SITE_URL
Without BREVO_API_KEY it prints what it would do and stops. Standard library only.
"""

import argparse
import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
BREVO = "https://api.brevo.com/v3"
SEND_HOURS = range(18, 21)   # Romanian time
MIN_SMOG_HOURS = 2           # one smoggy hour in the forecast isn't worth an email
STALE_H = 4                  # same as the air page: older data, no alert
CITIES = {  # area in air.json: list variable, centre (the air page's spot), names
    "bucharest": {"key": "bucuresti", "env": "BREVO_LIST_SMOG_BUCURESTI", "lat": 44.4268, "lon": 26.1025, "ro": "București", "en": "Bucharest"},
}
NAME = {"pm25": "PM2.5", "pm10": "PM10"}

TXT = {
    "ro": {
        "nightBad": "Smog probabil la noapte, între {a} și {b}.", "nightBadAll": "Smog probabil toată noaptea.",
        "bestAt": "Cel mai bun moment să aerisești: {a}–{b}.", "anyTime": "Poți aerisi oricând până la 22:00.",
        "shortAt": "Dacă aerisești, fă-o scurt, în jurul orei {a}.", "noAir": "Și seara e mai bine să ții geamul închis.",
        "shutNight": "Noaptea, ține geamul închis.", "shutAll": "Ține geamul închis și seara, și noaptea.",
        "now": "Acum, în {city}: {p} {v} µg/m³, măsurat între {a} și {b}.",
        "go": "Vezi aerul de lângă tine", "small": "Prognoza poate greși, mai ales pentru orele mai îndepărtate. Estimare din stațiile oficiale (ANPM, prin Agenția Europeană de Mediu) și prognoza SILAM (Institutul Meteorologic Finlandez). Nu sunt sfaturi medicale.",
        "why": "Primești acest e-mail pentru că te-ai abonat la alertele de smog Adia pentru {city}.", "unsub": "Dezabonează-te",
        "subject": "Smog probabil la noapte în {city} ({a}–{b})", "subjectAll": "Smog probabil toată noaptea în {city}",
    },
    "en": {
        "nightBad": "Smog likely tonight, between {a} and {b}.", "nightBadAll": "Smog likely all night.",
        "bestAt": "Best time to air the room: {a}–{b}.", "anyTime": "You can air the room any time until 22:00.",
        "shortAt": "If you air the room, keep it brief, around {a}.", "noAir": "Better to keep the window shut this evening too.",
        "shutNight": "Keep the window shut overnight.", "shutAll": "Keep the window shut this evening and tonight.",
        "now": "Now in {city}: {p} {v} µg/m³, measured between {a} and {b}.",
        "go": "See the air near you", "small": "Forecasts can be off, especially further ahead. Estimated from the official stations (ANPM, through the European Environment Agency) and the SILAM forecast (Finnish Meteorological Institute). Not medical advice.",
        "why": "You're receiving this email because you signed up for Adia smog alerts for {city}.", "unsub": "Unsubscribe",
    },
}


# ---------- Romanian time without tzdata (EU summer time: last Sunday of March to last Sunday of October, 01:00 UTC)
def _last_sunday(year: int, month: int) -> datetime:
    d = date(year, month, 31)
    d -= timedelta(days=(d.weekday() + 1) % 7)
    return datetime(d.year, d.month, d.day, 1, tzinfo=timezone.utc)


def ro_time(t: datetime) -> datetime:
    summer = _last_sunday(t.year, 3) <= t < _last_sunday(t.year, 10)
    return t + timedelta(hours=3 if summer else 2)


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------- the air page's estimate (aer-app.html: estimate() and tonight()), for one point
def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def estimate(D: dict, area: dict, lat: float, lon: float) -> dict:
    P = area["pollutant"]
    st = sorted(((s, km(lat, lon, s["lat"], s["lon"])) for s in D["stations"] if s["area"] == area["name"] and s.get(P)), key=lambda x: x[1])
    n = len(st[0][0][P]) if st else 0
    obs, med = [], []
    for i in range(n):
        num = den = 0.0
        here = []
        for s, d in st:
            v = s[P][i]
            if v is None:
                continue
            w = 1 / max(d, 0.5) ** 2
            num += w * v
            den += w
            here.append(v)
        obs.append(num / den if den else None)
        here.sort()
        k = len(here)
        med.append((here[k // 2] if k % 2 else (here[k // 2 - 1] + here[k // 2]) / 2) if k else None)
    L = n - 1
    while L >= 0 and obs[L] is None:
        L -= 1
    so = sm = 0.0
    for i in range(max(0, n - 24), n):
        if obs[i] is not None and med[i] is not None:
            so += obs[i]
            sm += med[i]
    ratio = min(max(so / sm, 0.6), 1.6) if sm > 0 else 1.0
    F = (area.get("forecast") or {}).get(P)
    b = (area.get("bias") or {}).get(P) or 0
    length = len(F) if F else n

    def base(j):
        return ratio * max(0.0, F[j] + b)

    est = []
    for j in range(length):
        if j <= L:
            est.append(obs[j] if j < n else None)
        elif not F or F[j] is None or L < 0:
            est.append(None)
        else:
            start = base(L) if F[L] is not None else obs[L]
            est.append(max(0.0, base(j) + (obs[L] - start) * math.exp(-(j - L) / 6)))
    return {"P": P, "obs": obs, "L": L, "est": est, "F": bool(F)}


def tonight(D: dict, e: dict, now: datetime, lang: str = "ro") -> dict | None:
    """The night's smog hours and the airing advice, worded exactly like the air page. None = no forecast."""
    t0 = parse(D["start"])
    good, smog = D["limits"][e["P"]]
    est = e["est"]
    hour = lambda j: ro_time(t0 + timedelta(hours=j)).hour  # noqa: E731
    hh = lambda j: f"{hour(j):02d}:00"  # noqa: E731
    cur = math.floor((now - t0).total_seconds() / 3600)
    if not e["F"] or not 0 <= cur < len(est) or all(x is None for x in est[cur:]):
        return None

    def find(frm, test):
        for j in range(frm, len(est)):
            if test(hour(j)):
                return j
        return -1

    def rng(a, b):
        return [j for j in range(max(a, 0), min(b, len(est))) if est[j] is not None]

    in_night = lambda h: h >= 22 or h < 7  # noqa: E731
    started = in_night(hour(cur))
    n_start = cur if started else find(cur, lambda h: h == 22)
    n_end = -1 if n_start < 0 else find(n_start, lambda h: h == 7)
    if n_start < 0 or n_end < 0:
        return None
    evening = [] if started else rng(cur, n_start)
    night = rng(n_start, n_end)
    if not night:
        return None
    X = TXT[lang]
    air = ""
    if evening:
        good_h = [j for j in evening if est[j] <= good]
        ok_h = [j for j in evening if est[j] <= smog]
        if len(good_h) == len(evening):
            air = X["anyTime"]
        elif good_h:
            j = min(good_h, key=lambda j: (est[j], j))
            air = X["bestAt"].format(a=hh(j), b=hh(j + 1))
        elif ok_h:
            j = min(ok_h, key=lambda j: (est[j], j))
            air = X["shortAt"].format(a=hh(j))
        else:
            air = X["noAir"]
    bad = [j for j in night if est[j] > smog]
    whole = len(bad) == len(night)
    big = X["nightBadAll"] if whole else (X["nightBad"].format(a=hh(bad[0]), b=hh(bad[-1] + 1)) if bad else "")
    sub = X["shutAll"] if air == X["noAir"] else " ".join(x for x in (air, X["shutNight"]) if x)
    return {"bad": bad, "whole": whole, "big": big, "sub": sub, "a": hh(bad[0]) if bad else "", "b": hh(bad[-1] + 1) if bad else "",
            "now_v": e["obs"][e["L"]], "now_a": hh(e["L"]), "now_b": hh(e["L"] + 1)}


# ---------- the email
def operator() -> str:
    m = re.search(r'operator:\s*"([^"]*)"', (HERE / "site.js").read_text(encoding="utf-8"))
    return m.group(1).strip() if m else ""


def email_html(city: dict, P: str, ro: dict, en: dict, site: str) -> str:
    e = html.escape
    def block(x: dict, lang: str) -> str:
        X = TXT[lang]
        v = f"{x['now_v']:.0f}"
        link = f"{site}/aer.html?src=alerta#{lang}"   # the site opens in English unless the link says #ro
        return (
            f'<h1 style="font:700 22px/1.3 Arial,sans-serif;margin:0 0 10px">{e(x["big"])}</h1>'
            f'<p style="font:600 17px/1.45 Arial,sans-serif;margin:0 0 12px">{e(x["sub"])}</p>'
            f'<p style="font:15px/1.5 Arial,sans-serif;margin:0 0 16px;color:#333">{e(X["now"].format(city=city[lang], p=NAME[P], v=v, a=x["now_a"], b=x["now_b"]))}</p>'
            f'<p style="margin:0 0 18px"><a href="{e(link)}" style="display:inline-block;background:#a8520a;color:#fff;text-decoration:none;'
            f'font:600 15px Arial,sans-serif;padding:11px 16px;border-radius:8px">{e(X["go"])} →</a></p>'
            f'<p style="font:13px/1.5 Arial,sans-serif;margin:0;color:#555">{e(X["small"])}</p>'
        )
    op = operator()
    foot = " ".join(
        f'{e(TXT[l]["why"].format(city=city[l]))} <a href="{{{{ unsubscribe }}}}" style="color:#555">{e(TXT[l]["unsub"])}</a>.'
        for l in ("ro", "en")
    ) + (f" Adia · {e(op)}" if op else " Adia")
    return (
        '<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '</head><body style="margin:0;padding:24px;background:#f3f4f7">'
        '<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;color:#121726">'
        f'{block(ro, "ro")}<hr style="border:0;border-top:1px solid #ddd;margin:22px 0">'
        f'<div lang="en">{block(en, "en")}</div>'
        f'<p style="font:12px/1.5 Arial,sans-serif;margin:22px 0 0;color:#666">{foot}</p>'
        "</div></body></html>"
    )


def subject(city: dict, ro: dict) -> str:
    X = TXT["ro"]
    return X["subjectAll"].format(city=city["ro"]) if ro["whole"] else X["subject"].format(city=city["ro"], a=ro["a"], b=ro["b"])


# ---------- Brevo
def brevo(method: str, path: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BREVO + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"api-key": key, "accept": "application/json", "content-type": "application/json", "user-agent": "adia-smog-alert"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Brevo {method} {path}: {err.code} {err.read().decode()[:300]}") from None
    return json.loads(txt) if txt.strip() else {}


def send(name: str, subj: str, body: str, list_id: int, env: dict) -> str:
    key = env["BREVO_API_KEY"]
    recent = brevo("GET", "/emailCampaigns?limit=50&offset=0&sort=desc&excludeHtmlContent=true", key).get("campaigns", [])
    same = [c for c in recent if c.get("name") == name]
    if same and same[0].get("status") != "draft":
        return f"already sent today ({same[0].get('status')})"
    if same:
        cid = same[0]["id"]
    else:
        cid = brevo("POST", "/emailCampaigns", key, {
            "name": name, "subject": subj,
            "sender": {"name": env.get("BREVO_SENDER_NAME") or "Adia", "email": env["BREVO_SENDER_EMAIL"]},
            "htmlContent": body, "recipients": {"listIds": [list_id]},
        })["id"]
    brevo("POST", f"/emailCampaigns/{cid}/sendNow", key)
    return f"sent (campaign {cid})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data" / "air.json"))
    ap.add_argument("--now", help="pretend it is this UTC time, e.g. 2026-11-20T16:30Z")
    ap.add_argument("--force", action="store_true", help="ignore the 18:00-20:59 window")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--html", help="with --dry-run: also write the email to this file")
    a = ap.parse_args()

    now = parse(a.now) if a.now else datetime.now(timezone.utc)
    local = ro_time(now)
    if local.hour not in SEND_HOURS and not a.force:
        print(f"{local:%H:%M} Romanian time: alerts go out between 18:00 and 20:59 only")
        return 0
    env = dict(os.environ)
    live = bool(env.get("BREVO_API_KEY")) and not a.dry_run
    if not live and not a.dry_run:
        print("BREVO_API_KEY isn't set: no alerts are sent (see website/email/README.md)")
    if live and not (env.get("BREVO_SENDER_EMAIL") and env.get("SITE_URL")):
        print("set BREVO_SENDER_EMAIL and SITE_URL to send alerts")
        return 1
    site = (env.get("SITE_URL") or "https://example.netlify.app").rstrip("/")
    D = json.loads(Path(a.data).read_text(encoding="utf-8"))
    problems = 0
    for area in D["areas"]:
        city = CITIES.get(area["name"])
        if not city:
            continue
        e = estimate(D, area, city["lat"], city["lon"])
        if e["L"] < 0:
            print(f"{city['en']}: no measurements, no alert")
            continue
        age_h = (now - (parse(D["start"]) + timedelta(hours=e["L"] + 1))).total_seconds() / 3600
        if age_h > STALE_H:
            print(f"{city['en']}: newest reading is {age_h:.1f} h old, no alert")
            continue
        ro, en = tonight(D, e, now, "ro"), tonight(D, e, now, "en")
        if ro is None:
            print(f"{city['en']}: no usable forecast for tonight, no alert")
            continue
        if len(ro["bad"]) < MIN_SMOG_HOURS:
            print(f"{city['en']}: {len(ro['bad'])} smog hour(s) tonight, no alert")
            continue
        subj, body = subject(city, ro), email_html(city, e["P"], ro, en, site)
        name = f"smog-{local:%Y-%m-%d}-{city['key']}"
        print(f"{city['en']}: {len(ro['bad'])} smog hours tonight -> {name}: {subj}")
        print("   ", ro["big"], ro["sub"])
        if a.html:
            Path(a.html).with_name(f"{Path(a.html).stem}-{city['key']}.html").write_text(body, encoding="utf-8")
        if not live:
            continue
        list_id = int(env.get(city["env"]) or 0)
        if list_id <= 0:
            print(f"    {city['env']} isn't set: skipped")
            continue
        try:
            print("   ", send(name, subj, body, list_id, env))
        except (RuntimeError, urllib.error.URLError, KeyError) as err:
            print("    failed:", err)
            problems += 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
