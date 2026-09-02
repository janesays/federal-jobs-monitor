#!/usr/bin/env python3
"""
snapshot.py — pull current USAJOBS announcements into SQLite.

Run on a schedule (daily is plenty; hourly is overkill for trend work).
Each run stamps rows with the snapshot date, so first-seen / last-seen
dates per announcement fall out naturally — that's what powers weekly
new-posting counts and time-to-close.

Setup:
    export USAJOBS_API_KEY="your-key"
    export USAJOBS_EMAIL="you@example.com"     # goes in User-Agent, per OPM docs
    python snapshot.py [--db monitor.db]

The Search API caps any one query at 10,000 results, so we slice the
pull by department (top-level Organization code), which keeps every
slice comfortably under the cap.
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time

import requests

HOST = "https://data.usajobs.gov"
PAGE_SIZE = 500          # API max per page
SLEEP = 1.2              # be polite; stays far under rate limits
TIMEOUT = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    control_number   TEXT,            -- MatchedObjectId (announcement id)
    position_id      TEXT,
    title            TEXT,
    department_code  TEXT,            -- top-level org code (e.g. VA, DD, HS)
    department_name  TEXT,
    agency_name      TEXT,            -- OrganizationName (subelement)
    series           TEXT,            -- occupational series code(s), comma-joined
    grade_low        TEXT,
    grade_high       TEXT,
    pay_plan         TEXT,
    hiring_paths     TEXT,            -- comma-joined (public, fed-internal-search, vet, ...)
    salary_min       REAL,
    salary_max       REAL,
    location_count   INTEGER,
    remote           INTEGER,
    open_date        TEXT,            -- PublicationStartDate
    close_date       TEXT,            -- ApplicationCloseDate
    first_seen       TEXT,            -- snapshot date first observed
    last_seen        TEXT,            -- snapshot date last observed
    PRIMARY KEY (control_number)
);
CREATE TABLE IF NOT EXISTS snapshots (
    run_at   TEXT,
    dept     TEXT,
    fetched  INTEGER,
    PRIMARY KEY (run_at, dept)
);
CREATE INDEX IF NOT EXISTS ix_postings_dept  ON postings(department_code);
CREATE INDEX IF NOT EXISTS ix_postings_open  ON postings(open_date);
CREATE INDEX IF NOT EXISTS ix_postings_seen  ON postings(first_seen);
"""


def headers():
    key = os.environ.get("USAJOBS_API_KEY")
    email = os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        sys.exit("Set USAJOBS_API_KEY and USAJOBS_EMAIL environment variables first.")
    return {"Authorization-Key": key, "User-Agent": email, "Host": "data.usajobs.gov"}


def get_department_codes(hdrs):
    """Top-level department codes from the agencysubelements codelist.
    Parent departments have short codes (2–4 chars) and no parent of their own."""
    r = requests.get(f"{HOST}/api/codelist/agencysubelements", headers=hdrs, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json()["CodeList"][0]["ValidValue"]
    depts = {}
    for it in items:
        code = it.get("Code", "")
        parent = it.get("ParentCode") or ""
        if code and (parent == "" or parent == code) and len(code) <= 4:
            depts[code] = it.get("Value", code)
    return depts  # {code: name}


def parse_item(item, today):
    d = item.get("MatchedObjectDescriptor", {})
    ua = (d.get("UserArea") or {}).get("Details") or {}
    series = ",".join(c.get("Code", "") for c in d.get("JobCategory", []))
    grades = d.get("JobGrade", [])
    rem = d.get("PositionRemuneration", [{}])
    paths = ",".join(ua.get("HiringPath", []) or [])
    org = d.get("OrganizationCodes", "") or ""   # e.g. "VA/VATA" dept/subelement
    dept_code = org.split("/")[0] if org else ""
    return {
        "control_number": str(item.get("MatchedObjectId", "")),
        "position_id": d.get("PositionID", ""),
        "title": d.get("PositionTitle", ""),
        "department_code": dept_code,
        "department_name": d.get("DepartmentName", ""),
        "agency_name": d.get("OrganizationName", ""),
        "series": series,
        "grade_low": d.get("UserArea", {}).get("Details", {}).get("LowGrade", "") or (grades[0].get("Code", "") if grades else ""),
        "grade_high": ua.get("HighGrade", ""),
        "pay_plan": (rem[0] or {}).get("RateIntervalCode", ""),
        "hiring_paths": paths,
        "salary_min": float((rem[0] or {}).get("MinimumRange") or 0),
        "salary_max": float((rem[0] or {}).get("MaximumRange") or 0),
        "location_count": len(d.get("PositionLocation", []) or []),
        "remote": 1 if str(ua.get("RemoteIndicator", "")).lower() == "true" else 0,
        "open_date": (d.get("PublicationStartDate") or "")[:10],
        "close_date": (d.get("ApplicationCloseDate") or "")[:10],
        "first_seen": today,
        "last_seen": today,
    }


def upsert(con, row):
    cols = ",".join(row)
    ph = ",".join("?" * len(row))
    con.execute(
        f"INSERT INTO postings ({cols}) VALUES ({ph}) "
        "ON CONFLICT(control_number) DO UPDATE SET last_seen=excluded.last_seen",
        list(row.values()),
    )


def pull_department(hdrs, con, dept_code, today):
    fetched, page = 0, 1
    while True:
        params = {"Organization": dept_code, "ResultsPerPage": PAGE_SIZE, "Page": page}
        r = requests.get(f"{HOST}/api/Search", headers=hdrs, params=params, timeout=TIMEOUT)
        if r.status_code == 429:          # rate limited: back off and retry once
            time.sleep(30)
            continue
        r.raise_for_status()
        sr = r.json().get("SearchResult", {})
        items = sr.get("SearchResultItems", [])
        for item in items:
            row = parse_item(item, today)
            row["department_code"] = dept_code   # stamp from the loop
            if row["control_number"]:
                upsert(con, row)
                fetched += 1
        total_pages = int(sr.get("UserArea", {}).get("NumberOfPages", "1") or 1)
        if page >= total_pages or not items:
            break
        page += 1
        time.sleep(SLEEP)
    return fetched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="monitor.db")
    ap.add_argument("--depts", nargs="*", help="limit to specific department codes (e.g. VA DD HS)")
    args = ap.parse_args()

    hdrs = headers()
    today = dt.date.today().isoformat()
    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)

    depts = get_department_codes(hdrs)
    if args.depts:
        depts = {c: depts.get(c, c) for c in args.depts}
    print(f"[{today}] snapshotting {len(depts)} departments -> {args.db}")

    for code, name in sorted(depts.items()):
        try:
            n = pull_department(hdrs, con, code, today)
        except requests.RequestException as e:
            print(f"  {code:<5} FAILED: {e}")
            continue
        con.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (today, code, n))
        con.commit()
        print(f"  {code:<5} {name[:44]:<46} {n:>6} announcements")
        time.sleep(SLEEP)

    con.commit()
    con.close()
    print("done.")


if __name__ == "__main__":
    main()
