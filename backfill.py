#!/usr/bin/env python3
"""
backfill.py — pull the HistoricJoa archive (closed announcements) into SQLite.

Run once per date range to build your trendline history; it's what lets the
dashboard show baselines instead of starting from zero. Uses the same env
vars as snapshot.py. HistoricJoa pages with a continuation token.

    python backfill.py --start 2024-01-01 --end 2026-08-01 [--db monitor.db]

Pull a year at a time if you're patient-limited; rows are idempotent.
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time

import requests

HOST = "https://data.usajobs.gov"
TIMEOUT = 90
SLEEP = 0.8

SCHEMA = """
CREATE TABLE IF NOT EXISTS historic (
    control_number    TEXT PRIMARY KEY,   -- usajobsControlNumber
    announcement_no   TEXT,
    title             TEXT,
    department_code   TEXT,               -- hiringDepartmentCode
    department_name   TEXT,
    agency_code       TEXT,               -- hiringAgencyCode (subelement)
    agency_name       TEXT,
    series            TEXT,
    pay_plan          TEXT,
    grade_low         TEXT,
    grade_high        TEXT,
    salary_min        REAL,
    salary_max        REAL,
    hiring_paths      TEXT,
    who_may_apply     TEXT,
    service_type      TEXT,               -- competitive / excepted / SES
    open_date         TEXT,               -- positionOpenDate
    close_date        TEXT,               -- positionCloseDate
    total_openings    TEXT                -- totalOpenings (may be 'MANY'/'FEW')
);
CREATE INDEX IF NOT EXISTS ix_hist_dept ON historic(department_code);
CREATE INDEX IF NOT EXISTS ix_hist_open ON historic(open_date);
CREATE INDEX IF NOT EXISTS ix_hist_series ON historic(series);
"""


def headers():
    # HistoricJoa is public — no key required. We still send User-Agent
    # (and the key if you've set it) as good API citizenship.
    h = {"Host": "data.usajobs.gov",
         "User-Agent": os.environ.get("USAJOBS_EMAIL", "hiring-monitor")}
    key = os.environ.get("USAJOBS_API_KEY")
    if key:
        h["Authorization-Key"] = key
    return h


def parse_row(j):
    cats = j.get("jobcategories") or j.get("JobCategories") or []
    series = ",".join(c.get("series", "") for c in cats)
    paths = ",".join(h.get("hiringPath", "") for h in (j.get("hiringPaths") or []))
    return {
        "control_number": str(j.get("usajobsControlNumber", "")),
        "announcement_no": j.get("announcementNumber", ""),
        "title": j.get("positionTitle", ""),
        "department_code": j.get("hiringDepartmentCode", ""),
        "department_name": j.get("hiringDepartmentName", ""),
        "agency_code": j.get("hiringAgencyCode", ""),
        "agency_name": j.get("hiringAgencyName", ""),
        "series": series,
        "pay_plan": j.get("payScale", "") or "",
        "grade_low": j.get("minimumGrade", ""),
        "grade_high": j.get("maximumGrade", ""),
        "salary_min": float(j.get("minimumSalary") or 0),
        "salary_max": float(j.get("maximumSalary") or 0),
        "hiring_paths": ",".join(h.get("hiringPath", "") for h in (j.get("hiringpaths") or [])),
        "who_may_apply": j.get("appointmentType", ""),
        "service_type": j.get("serviceType", ""),
        "open_date": (j.get("positionOpenDate") or "")[:10],
        "close_date": (j.get("positionCloseDate") or "")[:10],
        "total_openings": str(j.get("totalOpenings", "")),
    }


def pull_range(hdrs, con, start, end):
    """Pull one open-date range, following paging.next links."""
    url = f"{HOST}/api/historicjoa"
    params = {"StartPositionOpenDate": start, "EndPositionOpenDate": end}
    total, page = 0, 0
    while True:
        r = requests.get(url, headers=hdrs, params=params, timeout=TIMEOUT)
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        body = r.json()
        rows = body.get("data", [])
        for j in rows:
            row = parse_row(j)
            if row["control_number"]:
                cols = ",".join(row)
                ph = ",".join("?" * len(row))
                con.execute(
                    f"INSERT OR REPLACE INTO historic ({cols}) VALUES ({ph})",
                    list(row.values()),
                )
        total += len(rows)
        page += 1
        con.commit()
        meta = (body.get("paging") or {}).get("metadata") or {}
        if page == 1:
            print(f"  {start}..{end}: {meta.get('totalCount', '?')} announcements total")
        print(f"    page {page:>4}  +{len(rows):>5}  (running {total})")
        nxt = (body.get("paging") or {}).get("next")
        if not nxt or not rows:
            break
        # follow the server-provided next link verbatim
        url = nxt if nxt.startswith("http") else HOST + nxt
        params = None
        time.sleep(SLEEP)
    return total


def month_chunks(start, end):
    """Yield (start, end) quarter-ish chunks so no single query is huge."""
    a = dt.date.fromisoformat(start)
    z = dt.date.fromisoformat(end)
    while a <= z:
        b = min(dt.date(a.year + (a.month + 2) // 12, (a.month + 2) % 12 + 1, 1)
                - dt.timedelta(days=1), z)
        yield a.isoformat(), b.isoformat()
        a = b + dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="monitor.db")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (open date >=)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (open date <=)")
    args = ap.parse_args()

    hdrs = headers()
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    grand = 0
    for a, b in month_chunks(args.start, args.end):
        try:
            grand += pull_range(hdrs, con, a, b)
        except requests.RequestException as e:
            print(f"  {a}..{b} FAILED: {e} — continuing with next chunk")
        time.sleep(SLEEP)

    con.close()
    print(f"done: {grand} historic announcements for {args.start}..{args.end}")


if __name__ == "__main__":
    main()