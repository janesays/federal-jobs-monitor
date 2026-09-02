#!/usr/bin/env python3
"""
build_json.py — aggregate the SQLite store into monitor_data.json,
the exact shape the dashboard consumes. Run after each snapshot:

    python build_json.py [--db monitor.db] [--out monitor_data.json]
                         [--drp drp_separations.csv] [--weeks 104]

Weekly counts come from `historic` (open_date) unioned with `postings`
first_seen for the recent live window. Baseline/sd per department are
computed from the pre-2025-02 portion of history (pre-freeze), falling
back to the full window if too thin. Edit TRACKED to choose departments.
"""
import argparse
import csv
import datetime as dt
import json
import sqlite3
import statistics as stats
from collections import defaultdict

# Departments shown on the dashboard: code -> short display name.
# Codes are OPM top-level org codes as used by USAJOBS OrganizationCodes.
TRACKED = {
    "VA": "Veterans Affairs",
    "DD": "Defense (civilian)",
    "HS": "Homeland Security",
    "HE": "Health & Human Services",
    "TR": "Treasury / IRS",
    "IN": "Interior",
    "EP": "Environmental Protection",
    "SZ": "Social Security Admin.",
}

BASELINE_END = "2025-02-01"   # baseline = weekly mean before this date
DRP_CUTOFF = "2025-10-01"     # backfill window start (DRP separations effective)

PUBLIC_PATHS = {"public"}
VET_PATHS = {"vet", "disability", "mspouse", "nguard", "special-authorities"}


def week_floor(d: dt.date) -> str:
    return (d - dt.timedelta(days=d.weekday())).isoformat()  # Monday of week


def load_weekly(con, weeks_back):
    """dept -> {week_monday: count}, using historic + recent live postings."""
    end = dt.date.today()
    start = end - dt.timedelta(weeks=weeks_back)
    counts = defaultdict(lambda: defaultdict(int))
    q = """
        SELECT department_code, open_date FROM historic
        WHERE open_date >= ? AND department_code != ''
        UNION ALL
        SELECT department_code, first_seen FROM postings
        WHERE first_seen >= ? AND department_code != ''
          AND control_number NOT IN (SELECT control_number FROM historic)
    """
    s = start.isoformat()
    for dept, day in con.execute(q, (s, s)):
        if dept not in TRACKED or not day:
            continue
        try:
            wk = week_floor(dt.date.fromisoformat(day[:10]))
        except ValueError:
            continue
        counts[dept][wk] += 1
    # canonical week axis (drop current partial week)
    wk0 = dt.date.fromisoformat(week_floor(start))
    axis = []
    w = wk0
    while w <= end - dt.timedelta(days=7):
        axis.append(w.isoformat())
        w += dt.timedelta(days=7)
    return axis, counts


def hiring_path_quarters(con):
    """Quarterly composition of hiring paths from historic announcements."""
    rows = con.execute(
        "SELECT open_date, hiring_paths FROM historic WHERE open_date >= '2024-07-01'"
    ).fetchall()
    agg = defaultdict(lambda: defaultdict(int))
    for day, paths in rows:
        if not day:
            continue
        d = dt.date.fromisoformat(day[:10])
        fy = d.year + (1 if d.month >= 10 else 0)
        q = ((d.month - 10) % 12) // 3 + 1
        key = (fy, q, f"FY{str(fy)[2:]} Q{q}")
        ps = set((paths or "").split(","))
        if ps & PUBLIC_PATHS:
            agg[key]["public"] += 1
        elif ps & VET_PATHS:
            agg[key]["vets"] += 1
        elif paths:
            agg[key]["internal"] += 1
        else:
            agg[key]["other"] += 1
    out = []
    for key in sorted(agg)[-8:]:
        c = agg[key]
        tot = sum(c.values()) or 1
        out.append({
            "q": key[2],
            "public": round(100 * c["public"] / tot),
            "internal": round(100 * c["internal"] / tot),
            "vets": round(100 * c["vets"] / tot),
            "other": round(100 * c["other"] / tot),
        })
    return out


def series_yoy(con, top_n=8):
    today = dt.date.today()
    q_start = dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    prior_start = q_start.replace(year=q_start.year - 1)
    prior_end = today.replace(year=today.year - 1)

    def count_by_series(a, b):
        rows = con.execute(
            "SELECT series FROM historic WHERE open_date >= ? AND open_date <= ? "
            "UNION ALL SELECT series FROM postings WHERE open_date >= ? AND open_date <= ?",
            (a, b, a, b),
        ).fetchall()
        c = defaultdict(int)
        for (s,) in rows:
            for code in (s or "").split(","):
                if code:
                    c[code] += 1
        return c

    now = count_by_series(q_start.isoformat(), today.isoformat())
    prior = count_by_series(prior_start.isoformat(), prior_end.isoformat())
    top = sorted(now, key=now.get, reverse=True)[:top_n]
    return [
        {"code": s, "name": f"Series {s}", "now": now[s], "prior": max(prior.get(s, 0), 1)}
        for s in top
    ]


def reposts(con, min_reposts=3):
    rows = con.execute(
        """
        SELECT department_code, series, title, COUNT(*) AS n,
               CAST(SUM(julianday(close_date) - julianday(open_date)) AS INT) AS days
        FROM historic
        WHERE open_date >= date('now', '-12 months') AND title != ''
        GROUP BY department_code, series, lower(title)
        HAVING n >= ?
        ORDER BY n DESC LIMIT 12
        """,
        (min_reposts,),
    ).fetchall()
    return [
        {"agency": d, "series": (s or "").split(",")[0], "title": t[:60],
         "reposts": n, "days": days or 0}
        for d, s, t, n, days in rows
    ]


def load_drp(path):
    out = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                out[row["dept_code"].strip()] = {"seps": int(row["drp_separations"])}
    except FileNotFoundError:
        print(f"  note: {path} not found; backfill panel will be empty")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="monitor.db")
    ap.add_argument("--out", default="monitor_data.json")
    ap.add_argument("--drp", default="drp_separations.csv")
    ap.add_argument("--weeks", type=int, default=104)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    axis, counts = load_weekly(con, args.weeks)

    agencies = {}
    for code, name in TRACKED.items():
        series = [counts[code].get(w, 0) for w in axis]
        pre = [v for w, v in zip(axis, series) if w < BASELINE_END and v > 0]
        sample = pre if len(pre) >= 8 else [v for v in series if v > 0] or [1]
        base = stats.mean(sample)
        sd = (stats.pstdev(sample) / base) if base else 0.15
        agencies[code] = {
            "name": name,
            "baseline": round(base, 1),
            "sd": round(min(max(sd, 0.05), 0.5), 3),
            "series": series,
        }

    data = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "weeks": axis,
        "agencies": agencies,
        "paths": hiring_path_quarters(con),
        "seriesYoY": series_yoy(con),
        "reposts": reposts(con),
        "drp": load_drp(args.drp),
    }
    with open(args.out, "w") as f:
        json.dump(data, f)
    print(f"wrote {args.out}: {len(axis)} weeks, {len(agencies)} departments")


if __name__ == "__main__":
    main()
