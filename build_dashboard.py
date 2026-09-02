#!/usr/bin/env python3
"""
build_dashboard.py
Queries monitor.db and writes index.html — a self-contained,
shareable dashboard for the federal recruiting-scale analysis.

Usage (from the pipeline folder):
    python3 build_dashboard.py
    open index.html

Hygiene rules applied to all aggregates:
  * QA/test records excluded (title contains 'test', dept AM/OM)
  * total_openings >= 9999 treated as open-continuous placeholder
    (openings sums are NOT shown anywhere; announcement counts only)
"""
import json
import sqlite3
import sys
from datetime import date

DB = "monitor.db"
OUT = "index.html"

DRP_SERIES = {
    "1811": "Criminal Investigation",
    "1340": "Meteorology",
    "0962": "Contact Representative",
    "1862": "Consumer Safety Inspection",
}

HYGIENE = ("(lower(title) NOT LIKE '%test%' "
           " OR department_code NOT IN ('AM','OM'))")


def q(con, sql, params=()):
    return con.execute(sql, params).fetchall()


def main():
    con = sqlite3.connect(DB)
    try:
        con.execute("SELECT 1 FROM historic LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("No 'historic' table here — run from the pipeline folder.")

    data = {}

    # ---- quarterly throughput, government-wide ----
    rows = q(con, f"""
        SELECT substr(open_date,1,4) || '-Q' ||
               ((CAST(substr(open_date,6,2) AS INTEGER)+2)/3) AS qtr,
               COUNT(*)
        FROM historic
        WHERE open_date >= '2024-01-01' AND {HYGIENE}
        GROUP BY qtr ORDER BY qtr""")
    data["quarterly"] = [{"q": r[0], "n": r[1]} for r in rows]

    # ---- monthly volume + excepted share (freeze curve) ----
    rows = q(con, f"""
        SELECT substr(open_date,1,7) AS m, COUNT(*),
               SUM(CASE WHEN service_type LIKE 'Excepted%' THEN 1 ELSE 0 END)
        FROM historic
        WHERE open_date >= '2024-01-01' AND {HYGIENE}
        GROUP BY m ORDER BY m""")
    data["monthly"] = [{"m": r[0], "n": r[1], "exc": r[2]} for r in rows]

    # ---- Pathways (early-career) share by quarter ----
    rows = q(con, f"""
        SELECT substr(open_date,1,4) || '-Q' ||
               ((CAST(substr(open_date,6,2) AS INTEGER)+2)/3) AS qtr,
               COUNT(*),
               SUM(CASE WHEN lower(hiring_paths) LIKE '%student%'
                          OR lower(hiring_paths) LIKE '%graduate%'
                        THEN 1 ELSE 0 END)
        FROM historic
        WHERE open_date >= '2024-01-01' AND {HYGIENE}
        GROUP BY qtr ORDER BY qtr""")
    data["pathways"] = [{"q": r[0], "n": r[1], "ec": r[2]} for r in rows]

    # ---- entry-grade share by quarter (GS floor <= 9) ----
    rows = q(con, f"""
        SELECT substr(open_date,1,4) || '-Q' ||
               ((CAST(substr(open_date,6,2) AS INTEGER)+2)/3) AS qtr,
               COUNT(*),
               SUM(CASE WHEN CAST(grade_low AS INTEGER) <= 9
                        THEN 1 ELSE 0 END)
        FROM historic
        WHERE pay_plan='GS' AND grade_low GLOB '[0-9]*'
          AND open_date >= '2024-01-01' AND {HYGIENE}
        GROUP BY qtr ORDER BY qtr""")
    data["entry"] = [{"q": r[0], "n": r[1], "eg": r[2]} for r in rows]

    # ---- Jan–Aug verdict table ----
    rows = q(con, f"""
        SELECT substr(open_date,1,4) AS yr, COUNT(*),
               SUM(CASE WHEN lower(hiring_paths) LIKE '%student%'
                          OR lower(hiring_paths) LIKE '%graduate%'
                        THEN 1 ELSE 0 END),
               SUM(CASE WHEN pay_plan='GS' AND grade_low GLOB '[0-9]*'
                         AND CAST(grade_low AS INTEGER) <= 9
                        THEN 1 ELSE 0 END)
        FROM historic
        WHERE substr(open_date,6,5) BETWEEN '01-01' AND '08-31'
          AND open_date >= '2024-01-01' AND {HYGIENE}
        GROUP BY yr ORDER BY yr""")
    data["verdict"] = [{"yr": r[0], "n": r[1], "ec": r[2], "eg": r[3]}
                       for r in rows]

    # ---- post-cutoff recruiting in Partnership-flagged series ----
    ph = ",".join("?" * len(DRP_SERIES))
    rows = q(con, f"""
        SELECT substr(series,1,4) AS s4, department_code, COUNT(*)
        FROM historic
        WHERE open_date >= '2026-06-01'
          AND substr(series,1,4) IN ({ph}) AND {HYGIENE}
        GROUP BY s4, department_code
        HAVING COUNT(*) >= 3
        ORDER BY COUNT(*) DESC LIMIT 15""", tuple(DRP_SERIES))
    data["postcutoff"] = [
        {"series": r[0], "label": DRP_SERIES[r[0]], "dept": r[1] or "?",
         "n": r[2]} for r in rows]
    row = q(con, f"""
        SELECT COUNT(*) FROM historic
        WHERE open_date >= '2026-06-01'
          AND substr(series,1,4) IN ({ph}) AND {HYGIENE}""",
        tuple(DRP_SERIES))
    data["postcutoff_total"] = row[0][0]

    # ---- grade drift 2024 vs 2026, full distribution ----
    rows = q(con, f"""
        WITH g AS (
          SELECT substr(series,1,4) AS s4, substr(open_date,1,4) AS yr,
                 CAST(grade_low AS REAL) AS glo
          FROM historic
          WHERE pay_plan='GS' AND grade_low GLOB '[0-9]*' AND {HYGIENE}
        )
        SELECT s4,
               COUNT(CASE WHEN yr='2024' THEN 1 END) AS n24,
               COUNT(CASE WHEN yr='2026' THEN 1 END) AS n26,
               ROUND(AVG(CASE WHEN yr='2024' THEN glo END),2),
               ROUND(AVG(CASE WHEN yr='2026' THEN glo END),2)
        FROM g GROUP BY s4
        HAVING n24 >= 50 AND n26 >= 50""")
    drift = [{"s": r[0], "n24": r[1], "n26": r[2], "g24": r[3], "g26": r[4],
              "d": round(r[4] - r[3], 2)} for r in rows]
    drift.sort(key=lambda x: x["d"])
    data["drift_top"] = drift[:12]
    if drift:
        ds = sorted(x["d"] for x in drift)
        data["drift_median"] = ds[len(ds) // 2]
        data["drift_down_share"] = round(
            100.0 * sum(1 for x in drift if x["d"] < 0) / len(drift), 1)
    else:
        data["drift_median"] = None
        data["drift_down_share"] = None

    # ---- agency breakdown: Jan–Aug 2024 vs 2026, DoD grouped ----
    rows = q(con, f"""
        SELECT CASE WHEN department_code IN ('AR','NV','AF','DD')
                    THEN 'DOD' ELSE department_code END AS dept,
               SUM(CASE WHEN substr(open_date,1,4)='2024' THEN 1 ELSE 0 END),
               SUM(CASE WHEN substr(open_date,1,4)='2026' THEN 1 ELSE 0 END)
        FROM historic
        WHERE substr(open_date,6,5) BETWEEN '01-01' AND '08-31'
          AND open_date >= '2024-01-01'
          AND department_code IS NOT NULL AND department_code != ''
          AND {HYGIENE}
        GROUP BY dept
        HAVING SUM(CASE WHEN substr(open_date,1,4)='2024' THEN 1 ELSE 0 END)
               >= 200
        ORDER BY 3 DESC LIMIT 18""")
    data["agencies"] = [
        {"dept": r[0], "n24": r[1], "n26": r[2],
         "pct": round(100.0 * (r[2] - r[1]) / r[1], 1) if r[1] else None}
        for r in rows]

    # ---- coverage footer ----
    row = q(con, "SELECT MIN(open_date), MAX(open_date), COUNT(*) "
                 "FROM historic")[0]
    data["coverage"] = {"min": row[0], "max": row[1], "n": row[2],
                        "built": date.today().isoformat()}

    con.close()

    html = TEMPLATE.replace("__DATA__", json.dumps(data))
    with open(OUT, "w") as f:
        f.write(html)
    print(f"wrote {OUT}  (open it with:  open {OUT})")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Federal Recruiting Scale — USAJOBS Announcement Data</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1"></script>
<style>
:root{
  --bg:#f4f2ee; --card:#ffffff; --ink:#1c1b1a; --muted:#6e6a64;
  --accent:#0f4c81; --accent2:#b3552d; --pos:#2e7d32; --neg:#b3552d;
  --radius:10px; --gap:16px;
  --serif:Charter,'Bitstream Charter',Cambria,Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);
     font-size:15px;line-height:1.5;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
header{margin-bottom:24px;border-bottom:3px solid var(--ink);padding-bottom:16px}
header h1{font-family:var(--serif);font-size:30px;font-weight:700;
          letter-spacing:-.01em;margin-bottom:6px}
header p{color:var(--muted);max-width:70ch}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
      gap:var(--gap);margin-bottom:var(--gap)}
.kpi{background:var(--card);border-radius:var(--radius);padding:18px 20px;
     box-shadow:0 1px 3px rgba(0,0,0,.07)}
.kpi .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
            color:var(--muted);margin-bottom:6px}
.kpi .value{font-family:var(--serif);font-size:30px;font-weight:700}
.kpi .sub{font-size:13px;margin-top:4px}
.sub.neg{color:var(--neg);font-weight:600}
.sub.pos{color:var(--pos);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:var(--gap);
      margin-bottom:var(--gap)}
.card{background:var(--card);border-radius:var(--radius);padding:20px 22px;
      box-shadow:0 1px 3px rgba(0,0,0,.07)}
.card.full{grid-column:1 / -1}
.card h3{font-family:var(--serif);font-size:17px;margin-bottom:2px}
.card .note{font-size:12.5px;color:var(--muted);margin-bottom:14px}
canvas{max-height:280px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--ink);
   font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
   color:var(--muted)}
td{padding:8px 10px;border-bottom:1px solid #e8e5df}
td.num{text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
tr:last-child td{border-bottom:none}
.down{color:var(--neg);font-weight:600}
footer{margin-top:20px;padding-top:14px;border-top:1px solid #d9d5cd;
       font-size:12.5px;color:var(--muted)}
footer b{color:var(--ink)}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
@media print{body{background:#fff}.card,.kpi{box-shadow:none;
  border:1px solid #ddd}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Is federal recruiting running at replacement scale?</h1>
  <p>Every USAJOBS announcement, January 2024 – present, from OPM's own
  Search and HistoricJoa APIs. Counts are <b>announcements</b>, not hires or
  positions: postings can be cancelled or cover many vacancies, and
  direct-hire / excepted activity that bypasses USAJOBS is not visible here.
  Test records and open-continuous placeholder counts are excluded.</p>
</header>

<section class="kpis" id="kpis"></section>

<div class="grid">
  <div class="card full">
    <h3>Monthly announcement volume, with excepted-service overlay</h3>
    <div class="note">The 2025 freeze collapse is visible in competitive
    hiring; excepted-service announcements fell far less, so their share of
    the (shrunken) total rose sharply.</div>
    <canvas id="cMonthly"></canvas>
  </div>
  <div class="card">
    <h3>Early-career (Pathways) announcements</h3>
    <div class="note">Announcements open to Students / Recent graduates —
    the merit-based early-career channel.</div>
    <canvas id="cPathways"></canvas>
  </div>
  <div class="card">
    <h3>Entry-grade share of GS announcements</h3>
    <div class="note">Share of GS announcements with a grade floor of
    GS-9 or below.</div>
    <canvas id="cEntry"></canvas>
  </div>
  <div class="card">
    <h3>Jan–Aug, year over year</h3>
    <div class="note">Same eight months each year, so seasonality can't
    explain the gap.</div>
    <table id="tVerdict"></table>
  </div>
  <div class="card">
    <h3>Posted since June 2026 in Partnership-flagged series</h3>
    <div class="note">Recruiting activity after the Partnership analysis
    cutoff, in the four series it flagged (cells of 3+ shown).</div>
    <table id="tCutoff"></table>
  </div>
  <div class="card full">
    <h3>Announcements by department, Jan–Aug 2024 vs 2026</h3>
    <div class="note">Departments with 200+ Jan–Aug 2024 announcements;
    Army/Navy/Air Force/defense-wide grouped as DOD. Same-months comparison,
    so seasonality can't explain the gaps.</div>
    <canvas id="cAgency" style="max-height:420px"></canvas>
  </div>
  <div class="card full">
    <h3>Advertised grade floors, 2024 → 2026</h3>
    <div class="note">Mean advertised minimum GS grade by occupational
    series (series with 50+ announcements in both years). Largest declines
    shown; the median and down-share cover the full distribution.</div>
    <table id="tDrift"></table>
  </div>
</div>

<footer id="foot"></footer>
</div>

<script>
const D = __DATA__;
const fmt = n => n.toLocaleString();
const INK='#1c1b1a', BLUE='#0f4c81', RUST='#b3552d', MUT='#6e6a64';

/* ---------- KPIs ---------- */
(function(){
  const v = {}; D.verdict.forEach(r => v[r.yr] = r);
  const cards = [];
  if (v['2024'] && v['2026']) {
    const pct = ((v['2026'].n - v['2024'].n) / v['2024'].n * 100).toFixed(0);
    cards.push({label:'Jan–Aug announcements, 2026', value:fmt(v['2026'].n),
      sub:`${pct}% vs 2024 (${fmt(v['2024'].n)})`, cls:pct<0?'neg':'pos'});
    const ep = ((v['2026'].ec - v['2024'].ec) / v['2024'].ec * 100).toFixed(0);
    cards.push({label:'Early-career (Pathways), 2026', value:fmt(v['2026'].ec),
      sub:`${ep}% vs 2024 (${fmt(v['2024'].ec)})`, cls:ep<0?'neg':'pos'});
  }
  if (v['2025'] && v['2024']) {
    const p5 = ((v['2025'].n - v['2024'].n) / v['2024'].n * 100).toFixed(0);
    cards.push({label:'2025 freeze-year drop', value:p5+'%',
      sub:`Jan–Aug: ${fmt(v['2025'].n)} announcements`, cls:'neg'});
  }
  cards.push({label:'Flagged-series postings since Jun 2026',
    value:fmt(D.postcutoff_total),
    sub:'after the Partnership analysis cutoff', cls:''});
  if (D.drift_median !== null) {
    cards.push({label:'Median grade-floor drift \u201924\u2192\u201926',
      value:(D.drift_median>0?'+':'')+D.drift_median,
      sub:`${D.drift_down_share}% of series advertise lower`,
      cls:D.drift_median<0?'neg':''});
  }
  document.getElementById('kpis').innerHTML = cards.map(c =>
    `<div class="kpi"><div class="label">${c.label}</div>
     <div class="value">${c.value}</div>
     <div class="sub ${c.cls}">${c.sub}</div></div>`).join('');
})();

/* ---------- monthly volume + excepted overlay ---------- */
new Chart(document.getElementById('cMonthly'), {
  data: {
    labels: D.monthly.map(r => r.m),
    datasets: [
      {type:'bar', label:'All announcements',
       data:D.monthly.map(r=>r.n), backgroundColor:BLUE+'55',
       borderColor:BLUE, borderWidth:1},
      {type:'line', label:'Excepted service',
       data:D.monthly.map(r=>r.exc), borderColor:RUST,
       backgroundColor:RUST, borderWidth:2, pointRadius:0, tension:.25}
    ]
  },
  options:{responsive:true, animation:false,
    interaction:{mode:'index', intersect:false},
    plugins:{legend:{labels:{boxWidth:14}}},
    scales:{y:{beginAtZero:true}, x:{ticks:{maxTicksLimit:16}}}}
});

/* ---------- pathways ---------- */
new Chart(document.getElementById('cPathways'), {
  type:'bar',
  data:{labels:D.pathways.map(r=>r.q),
    datasets:[{label:'Student/Recent-grad announcements',
      data:D.pathways.map(r=>r.ec), backgroundColor:RUST+'99',
      borderColor:RUST, borderWidth:1}]},
  options:{responsive:true, animation:false,
    plugins:{legend:{display:false}},
    scales:{y:{beginAtZero:true}}}
});

/* ---------- entry-grade share ---------- */
new Chart(document.getElementById('cEntry'), {
  type:'line',
  data:{labels:D.entry.map(r=>r.q),
    datasets:[{label:'GS-9-or-below floor, % of GS announcements',
      data:D.entry.map(r=>(100*r.eg/r.n).toFixed(1)),
      borderColor:BLUE, backgroundColor:BLUE+'22', fill:true,
      borderWidth:2, tension:.25}]},
  options:{responsive:true, animation:false,
    plugins:{legend:{display:false}},
    scales:{y:{ticks:{callback:v=>v+'%'}}}}
});

/* ---------- verdict table ---------- */
document.getElementById('tVerdict').innerHTML =
  `<tr><th>Year</th><th class="num">Announcements</th>
   <th class="num">Pathways</th><th class="num">Entry-grade GS</th></tr>` +
  D.verdict.map(r =>
    `<tr><td>${r.yr}</td><td class="num">${fmt(r.n)}</td>
     <td class="num">${fmt(r.ec)}</td><td class="num">${fmt(r.eg)}</td></tr>`
  ).join('');

/* ---------- post-cutoff table ---------- */
document.getElementById('tCutoff').innerHTML =
  `<tr><th>Series</th><th>Dept</th><th class="num">Announcements</th></tr>` +
  D.postcutoff.map(r =>
    `<tr><td>${r.label} (${r.series})</td><td>${r.dept}</td>
     <td class="num">${fmt(r.n)}</td></tr>`).join('');

/* ---------- drift table ---------- */
document.getElementById('tDrift').innerHTML =
  `<tr><th>Series</th><th class="num">n 2024</th><th class="num">n 2026</th>
   <th class="num">Avg floor 2024</th><th class="num">Avg floor 2026</th>
   <th class="num">Drift</th></tr>` +
  D.drift_top.map(r =>
    `<tr><td>${r.s}</td><td class="num">${fmt(r.n24)}</td>
     <td class="num">${fmt(r.n26)}</td><td class="num">${r.g24}</td>
     <td class="num">${r.g26}</td>
     <td class="num ${r.d<0?'down':''}">${r.d>0?'+':''}${r.d}</td></tr>`
  ).join('');

/* ---------- agency chart ---------- */
const DEPT_NAMES = {DOD:'Defense', VA:'Veterans Affairs', HS:'Homeland Security',
  DJ:'Justice', TR:'Treasury', AG:'Agriculture', IN:'Interior', HE:'HHS',
  CM:'Commerce', TD:'Transportation', DL:'Labor', ST:'State', NN:'NASA',
  OM:'OPM', GS:'GSA', ED:'Education', DN:'Energy', SB:'SBA', LL:'Legislative',
  JL:'Judicial', EP:'EPA', HU:'HUD', SZ:'SSA', PO:'USPS-adjacent', AM:'AmeriCorps',
  TB:'Treasury bureaus', HF:'HUD field', EE:'EEOC', EB:'ExIm', SE:'SEC'};
if (D.agencies && D.agencies.length) {
  const rows = D.agencies;
  new Chart(document.getElementById('cAgency'), {
    type:'bar',
    data:{labels:rows.map(r=>DEPT_NAMES[r.dept]||r.dept),
      datasets:[
        {label:'Jan–Aug 2024', data:rows.map(r=>r.n24),
         backgroundColor:MUT+'66', borderColor:MUT, borderWidth:1},
        {label:'Jan–Aug 2026', data:rows.map(r=>r.n26),
         backgroundColor:BLUE+'99', borderColor:BLUE, borderWidth:1}]},
    options:{indexAxis:'y', responsive:true, animation:false,
      plugins:{legend:{labels:{boxWidth:14}},
        tooltip:{callbacks:{afterBody:(items)=>{
          const r=rows[items[0].dataIndex];
          return r.pct===null?'':`Change: ${r.pct>0?'+':''}${r.pct}%`;}}}},
      scales:{x:{beginAtZero:true}}}
  });
}

/* ---------- footer ---------- */
document.getElementById('foot').innerHTML =
  `<b>Sources:</b> USAJOBS Search API + HistoricJoa archive (OPM). ` +
  `Coverage ${D.coverage.min} – ${D.coverage.max}, ` +
  `${fmt(D.coverage.n)} announcements. Built ${D.coverage.built}. ` +
  `Announcements ≠ hires; excludes activity that bypasses USAJOBS.`;
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
