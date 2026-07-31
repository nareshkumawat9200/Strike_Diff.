#!/usr/bin/env python3
"""
Downloads the latest NSE F&O bhavcopy and writes data/latest.json for the site.

Walks back from T-1 until a bhavcopy exists (skips weekends/holidays), aggregates
per-strike volume + OI for every (symbol, expiry), and emits a compact JSON the
browser can recompute from live -- so the liquidity slider still works client-side.
"""

import io
import json
import os
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MAX_LOOKBACK = 10          # trading-day search window (covers long holiday weekends)
# Resolve relative to this file (scripts/ -> ../data/), so the job works no
# matter which directory it is invoked from.
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "latest.json")


def fetch(ymd):
    """Return the bhavcopy CSV text for YYYYMMDD, or None if not published."""
    req = Request(URL.format(ymd=ymd), headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/",
    })
    try:
        with urlopen(req, timeout=90) as r:
            blob = r.read()
    except HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    except URLError:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(z.namelist()[0]).decode("utf-8", "ignore")


def build(csv_text):
    """Aggregate option rows into per-(symbol, expiry) strike lists."""
    import csv as csvmod
    rows = csvmod.DictReader(io.StringIO(csv_text))

    groups = {}
    for r in rows:
        if r.get("OptnTp") not in ("CE", "PE"):
            continue
        typ = r.get("FinInstrmTp")
        if typ not in ("STO", "IDO"):
            continue
        try:
            strike = float(r["StrkPric"])
        except (TypeError, ValueError):
            continue

        key = (r["TckrSymb"], r["XpryDt"])
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"type": typ, "lot": 0, "strikes": defaultdict(lambda: [0, 0])}
        try:
            g["lot"] = max(g["lot"], int(r.get("NewBrdLotQty") or 0))
        except ValueError:
            pass

        s = g["strikes"][strike]
        s[0] += float(r.get("TtlTradgVol") or 0)
        s[1] += float(r.get("OpnIntrst") or 0)

    out = []
    for (sym, exp), g in groups.items():
        strikes = [[_num(k), int(v[0]), int(v[1])] for k, v in sorted(g["strikes"].items())]
        out.append({"s": sym, "e": exp, "t": g["type"], "l": g["lot"], "k": strikes})
    out.sort(key=lambda x: (x["s"], x["e"]))
    return out


def _num(x):
    """Keep strikes compact: 380.0 -> 380, 12.5 stays 12.5."""
    return int(x) if float(x).is_integer() else round(float(x), 4)


def main():
    # Start at today: an evening run picks up the same day's file once NSE
    # publishes it, otherwise this falls back to T-1, T-2, ... automatically.
    start = date.today()
    for i in range(MAX_LOOKBACK):
        d = start - timedelta(days=i)
        if d.weekday() >= 5:                      # skip Sat/Sun outright
            continue
        ymd = d.strftime("%Y%m%d")
        print(f"trying {ymd} ...", flush=True)
        text = fetch(ymd)
        if text is None:
            continue

        groups = build(text)
        if not groups:
            print(f"  {ymd}: no option rows, skipping")
            continue

        payload = {
            "tradeDate": d.isoformat(),
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "groups": groups,
        }
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        size = os.path.getsize(OUT)
        print(f"wrote {OUT}: trade date {d}, {len(groups)} contracts, {size/1024:.0f} KB")
        return 0

    print(f"ERROR: no bhavcopy found in the last {MAX_LOOKBACK} days", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
