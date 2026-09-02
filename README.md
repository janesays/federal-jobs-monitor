# Federal Hiring Monitor — live data pipeline

## One-time setup
    pip install requests
    export USAJOBS_API_KEY="<your key>"      # never hardcode it
    export USAJOBS_EMAIL="you@example.com"   # required User-Agent header

## Build history (once, then occasionally)
    python backfill.py --start 2024-08-01 --end 2026-08-01

## Snapshot current postings (schedule daily)
    python snapshot.py
    # cron example, 6:10am daily:
    # 10 6 * * * cd /path/to/pipeline && USAJOBS_API_KEY=... USAJOBS_EMAIL=... python snapshot.py && python build_json.py

## Aggregate for the dashboard (after each snapshot)
    python build_json.py            # writes monitor_data.json

## Fill in the DRP side
Edit drp_separations.csv with agency-level DRP separation counts from
GAO-26-108719 tables and OPM's workforce data site. dept_code must match
the OPM top-level org codes in build_json.py TRACKED.

## Serve the dashboard
Put federal_hiring_monitor.html next to monitor_data.json and run:
    python -m http.server 8080
Open http://localhost:8080/federal_hiring_monitor.html
The page loads monitor_data.json if present; otherwise it falls back to
the simulated dataset and says so in the masthead.
