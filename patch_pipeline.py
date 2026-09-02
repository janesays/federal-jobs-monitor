#!/usr/bin/env python3
"""
patch_pipeline.py — fixes the two field-parsing bugs in the hiring
monitor pipeline.

  backfill.py : HistoricJoa uses lowercase keys 'jobcategories' and
                'hiringpaths', and a flat string 'payScale' — the
                parser was reading wrong/nonexistent keys, leaving
                series, pay_plan, and hiring_paths empty.
  snapshot.py : department_code came back empty from the Search API
                payload; stamp it from the pull loop instead, which
                already knows the code.

Run from the pipeline folder:
    python3 patch_pipeline.py
"""
import os
import py_compile
import re
import sys

if not (os.path.exists("backfill.py") and os.path.exists("snapshot.py")):
    sys.exit("Run this from the pipeline folder (backfill.py / snapshot.py not found here).")

# ---------- backfill.py ----------
src = open("backfill.py").read()
orig = src
changes = []

new = src.replace(
    'cats = j.get("JobCategories") or j.get("jobCategories") or []',
    'cats = j.get("jobcategories") or j.get("JobCategories") or []',
)
if new != src:
    changes.append("series key casing")
src = new

new = src.replace(
    '"pay_plan": ",".join(p.get("payPlan", "") for p in (j.get("payPlans") or [])),',
    '"pay_plan": j.get("payScale", "") or "",',
)
if new != src:
    changes.append("pay_plan -> payScale")
src = new

new = re.sub(
    r'"hiring_paths":.*\n',
    '"hiring_paths": ",".join(h.get("hiringPath", "") '
    'for h in (j.get("hiringpaths") or [])),\n',
    src,
    count=1,
)
if new != src:
    changes.append("hiring_paths key casing")
src = new

if changes:
    open("backfill.py", "w").write(src)
    print("backfill.py: patched (" + ", ".join(changes) + ")")
elif "jobcategories" in orig and '"payScale"' in orig:
    print("backfill.py: already patched")
else:
    print("backfill.py: NOTHING MATCHED - stop and tell Claude")

# ---------- snapshot.py ----------
src = open("snapshot.py").read()
if "stamp from the loop" in src:
    print("snapshot.py: already patched")
elif "row = parse_item(item, today)" in src:
    src = src.replace(
        "row = parse_item(item, today)",
        "row = parse_item(item, today)\n"
        '            row["department_code"] = dept_code'
        "   # stamp from the loop",
    )
    open("snapshot.py", "w").write(src)
    print("snapshot.py: patched (department_code stamp)")
else:
    print("snapshot.py: LINE NOT FOUND - stop and tell Claude")

# ---------- syntax check ----------
for f in ("backfill.py", "snapshot.py"):
    try:
        py_compile.compile(f, doraise=True)
        print(f"{f}: syntax OK")
    except py_compile.PyCompileError as e:
        print(f"{f}: SYNTAX ERROR after patch - tell Claude:\n{e}")
