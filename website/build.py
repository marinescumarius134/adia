"""Make the complete site from the source pages.

The source pages (landing.html and *-app.html) are written for the claude.ai artifact preview, which adds
the <!doctype>, <html>, <head> and <body> around them. This script adds them for a real host.

    python build.py --out _site               # the site, for Netlify (pages, fonts, site.js, _headers, _redirects)
    python build.py --data-host --out _pages  # the data host, for GitHub Pages: data/air.json and a pointer page
    python build.py --preview --out <dir>     # like the site, but the forms only say "preview" (claude.ai previews)

Why two hosts: Netlify's free plan charges 15 of its 300 monthly credits for every production deploy, so the
hourly data can't be a Netlify deploy. GitHub Actions publishes data/air.json to GitHub Pages every hour, and
the Netlify site passes /data/* through to it (a proxy rule in _redirects). Set DATA_URL on Netlify
(Site configuration > Environment variables), for example https://<user>.github.io/<repo>/data
Without DATA_URL (on your own computer) the local data/air.json is copied instead.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
PAGES = {
    "landing.html": "index.html",
    "aer-app.html": "aer.html",
    "metoda-app.html": "metoda.html",
    "confidentialitate-app.html": "confidentialitate.html",
}
STATIC = ["site.js", "fonts"]

# Security headers. The CSP also keeps the promise in the privacy notice: nothing loads from other companies.
HEADERS = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: geolocation=(self), camera=(), microphone=()
  X-Frame-Options: DENY
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'
/fonts/*
  Cache-Control: public, max-age=31536000, immutable
/site.js
  Cache-Control: public, max-age=300
"""


def wrap(src: str, forms: bool) -> str:
    head, body = src.split("<header", 1)
    attrs = ' lang="ro"' + (' data-forms="netlify"' if forms else "")
    return (
        f"<!doctype html>\n<html{attrs}>\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        f"{head.strip()}\n</head>\n<body>\n<header{body.rstrip()}\n</body>\n</html>\n"
    )


def site(out: Path, preview: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    same = out.resolve() == HERE.resolve()
    for src, dst in PAGES.items():
        (out / dst).write_text(wrap((HERE / src).read_text(encoding="utf-8"), forms=not (same or preview)), encoding="utf-8")
        print("wrote", out / dst)
    if same:
        return
    for name in STATIC:
        s, d = HERE / name, out / name
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    (out / "_headers").write_text(HEADERS, encoding="utf-8")

    data_url = os.environ.get("DATA_URL", "").strip().rstrip("/")
    if data_url:
        if not data_url.startswith("https://"):
            sys.exit(f"DATA_URL must start with https:// (got {data_url!r})")
        (out / "_redirects").write_text(f"/data/*  {data_url}/:splat  200!\n", encoding="utf-8")
        print("data proxied from", data_url)
    elif os.environ.get("NETLIFY") == "true":   # Netlify always sets this during its builds
        sys.exit("DATA_URL isn't set. In Netlify: Site configuration > Environment variables > DATA_URL = "
                 "https://<user>.github.io/<repo>/data (see website/README.md).")
    elif (HERE / "data" / "air.json").exists():
        (out / "data").mkdir(exist_ok=True)
        shutil.copy2(HERE / "data" / "air.json", out / "data" / "air.json")
        print("copied", out / "data" / "air.json", "(local preview; on Netlify set DATA_URL)")
    else:
        print("note: no data/air.json and no DATA_URL; the air page will say the data couldn't be loaded")


def data_host(out: Path) -> None:
    src = HERE / "data" / "air.json"
    if not src.exists():
        sys.exit("data/air.json is missing: run air_job.py first")
    (out / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out / "data" / "air.json")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    url = os.environ.get("SITE_URL", "").strip().rstrip("/")
    go = (f'<meta http-equiv="refresh" content="0; url={url}/">\n' if url.startswith("https://") else "")
    link = f'<p><a href="{url}/">{url}</a></p>' if url.startswith("https://") else ""
    (out / "index.html").write_text(
        "<!doctype html>\n<html lang=\"ro\">\n<head>\n<meta charset=\"utf-8\">\n<title>Adia data</title>\n"
        f"{go}</head>\n<body>\n<p>Adia: date orare despre aer (data/air.json).</p>\n{link}\n</body>\n</html>\n",
        encoding="utf-8",
    )
    print("wrote", out / "data" / "air.json", "and", out / "index.html")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--data-host", action="store_true", help="only the data file and a pointer page (GitHub Pages)")
    ap.add_argument("--preview", action="store_true", help="forms only say 'preview' (for the claude.ai previews)")
    a = ap.parse_args()
    if a.data_host:
        data_host(Path(a.out))
    else:
        site(Path(a.out), a.preview)


if __name__ == "__main__":
    main()
