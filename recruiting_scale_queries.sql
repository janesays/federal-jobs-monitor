-- ============================================================
-- recruiting_scale_queries.sql
-- Government-wide test of Kupor's recruiting claims:
--   (a) ~300,000 employees left in 2025 (his own figure)
--   (b) he says early-career recruitment is a priority
-- Question: does OPM's own public recruiting channel (USAJOBS)
-- show recruiting activity at replacement scale, and is the
-- early-career share actually rising?
--
-- RUN AFTER the full backfill re-run + snapshot + cross-fill.
-- Requires the patched pipeline (hiring_paths / series now
-- populate) and assumes v_ann exists — run
-- kupor_challenge_queries.sql first, or this file standalone
-- will recreate it below.
--
-- Framing discipline for anything published:
--   * postings != hires; announcements are recruiting intent
--   * multi-vacancy announcements mean openings floors are
--     conservative; 'MANY'/'FEW' counted as 1
--   * excepted-service and direct-hire activity that bypasses
--     USAJOBS is invisible here — which is itself a finding
--     when the flagship early-career program (Tech Force)
--     doesn't appear in the merit channel. Verify how Tech
--     Force roles are actually posted before asserting that.
-- ============================================================

DROP VIEW IF EXISTS v_ann;
CREATE VIEW v_ann AS
SELECT control_number, title, department_code,
       CASE WHEN department_code IN ('AR','NV','AF','DD')
            THEN 'DOD' ELSE department_code END AS dept_group,
       substr(series, 1, 4) AS series4,
       pay_plan, grade_low, grade_high,
       hiring_paths, open_date, close_date,
       total_openings, 'historic' AS src
FROM historic
UNION ALL
SELECT control_number, title, department_code,
       CASE WHEN department_code IN ('AR','NV','AF','DD')
            THEN 'DOD' ELSE department_code END,
       substr(series, 1, 4),
       pay_plan, grade_low, grade_high,
       hiring_paths, open_date, close_date,
       NULL, 'live'
FROM postings
WHERE control_number NOT IN (SELECT control_number FROM historic);


-- ============================================================
-- SCALE 1: total recruiting throughput by quarter, 2024–2026.
-- The replacement-scale yardstick: ~300,000 departed in 2025
-- (Kupor's number). min_openings is a conservative floor on
-- advertised vacancies. If quarterly openings never approach
-- replacement scale, "selective hiring" means structural
-- shrinkage of the pipeline — his real policy, stated or not.
-- ============================================================
.print ''
.print '=== SCALE 1: quarterly recruiting throughput, govt-wide ==='
SELECT substr(open_date, 1, 4) || '-Q' ||
       ((CAST(substr(open_date, 6, 2) AS INTEGER) + 2) / 3) AS quarter,
       COUNT(*) AS announcements,
       SUM(CASE WHEN total_openings GLOB '[0-9]*'
                THEN CAST(total_openings AS INTEGER)
                ELSE 1 END) AS min_openings
FROM v_ann
WHERE open_date >= '2024-01-01'
GROUP BY quarter
ORDER BY quarter;


-- ============================================================
-- SCALE 2: early-career share via hiring paths (Pathways).
-- 'Students' and 'Recent graduates' paths are the official
-- early-career recruiting channels. If Kupor's early-career
-- push is real, this share should be RISING vs the 2024
-- baseline — not just holding steady in a shrunken total.
-- ============================================================
.print ''
.print '=== SCALE 2: early-career (Pathways) share by quarter ==='
SELECT substr(open_date, 1, 4) || '-Q' ||
       ((CAST(substr(open_date, 6, 2) AS INTEGER) + 2) / 3) AS quarter,
       COUNT(*) AS total_ann,
       SUM(CASE WHEN lower(hiring_paths) LIKE '%student%'
                  OR lower(hiring_paths) LIKE '%graduate%'
                THEN 1 ELSE 0 END) AS early_career_ann,
       ROUND(100.0 * SUM(CASE WHEN lower(hiring_paths) LIKE '%student%'
                                OR lower(hiring_paths) LIKE '%graduate%'
                              THEN 1 ELSE 0 END) / COUNT(*), 2)
         AS early_career_pct
FROM v_ann
WHERE open_date >= '2024-01-01'
GROUP BY quarter
ORDER BY quarter;


-- ============================================================
-- SCALE 3: entry-grade recruiting (GS-5/7/9 announcement
-- floor). Second, independent early-career proxy: jobs a new
-- graduate can actually be hired into. Absolute counts matter
-- here, not just share — a rising share of a collapsed total
-- is not a recruiting push.
-- ============================================================
.print ''
.print '=== SCALE 3: entry-grade (GS-5/7/9 floor) announcements ==='
SELECT substr(open_date, 1, 4) || '-Q' ||
       ((CAST(substr(open_date, 6, 2) AS INTEGER) + 2) / 3) AS quarter,
       SUM(CASE WHEN CAST(grade_low AS INTEGER) <= 9
                THEN 1 ELSE 0 END) AS entry_grade_ann,
       COUNT(*) AS all_gs_ann,
       ROUND(100.0 * SUM(CASE WHEN CAST(grade_low AS INTEGER) <= 9
                              THEN 1 ELSE 0 END) / COUNT(*), 2)
         AS entry_grade_pct
FROM v_ann
WHERE pay_plan = 'GS'
  AND grade_low GLOB '[0-9]*'
  AND open_date >= '2024-01-01'
GROUP BY quarter
ORDER BY quarter;


-- ============================================================
-- SCALE 4: the year-over-year verdict table. Same-period
-- comparison (Jan 1 – Aug 31 of 2024 / 2025 / 2026) so
-- seasonality can't be blamed. One row per year: total
-- recruiting, early-career recruiting, entry-grade recruiting.
-- This is the table that answers "is he recruiting at the
-- scale the departures require" in one screenshot.
-- ============================================================
.print ''
.print '=== SCALE 4: Jan-Aug year-over-year verdict table ==='
SELECT substr(open_date, 1, 4) AS yr,
       COUNT(*) AS announcements,
       SUM(CASE WHEN total_openings GLOB '[0-9]*'
                THEN CAST(total_openings AS INTEGER)
                ELSE 1 END) AS min_openings,
       SUM(CASE WHEN lower(hiring_paths) LIKE '%student%'
                  OR lower(hiring_paths) LIKE '%graduate%'
                THEN 1 ELSE 0 END) AS early_career_ann,
       SUM(CASE WHEN pay_plan = 'GS'
                 AND grade_low GLOB '[0-9]*'
                 AND CAST(grade_low AS INTEGER) <= 9
                THEN 1 ELSE 0 END) AS entry_grade_ann
FROM v_ann
WHERE substr(open_date, 6, 5) BETWEEN '01-01' AND '08-31'
  AND open_date >= '2024-01-01'
GROUP BY yr
ORDER BY yr;
