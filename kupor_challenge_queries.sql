-- ============================================================
-- kupor_challenge_queries.sql
-- Queries against monitor.db (postings + historic) to test the
-- three claims in Kupor's "Last Year's Workforce Reductions
-- Were Real" post, using OPM's own USAJOBS distribution data.
--
-- Usage:  sqlite3 monitor.db < kupor_challenge_queries.sql
--         (or run sections individually)
--
-- IMPORTANT before publishing anything:
--   * Angles 2 and 3 need the completed HistoricJoa backfill
--     from 2024-11-01 (your late-2024 re-run) so the 2024
--     baseline isn't truncated.
--   * Postings != hires. Announcements get cancelled, cover
--     multiple vacancies, or fill via other authorities. Frame
--     results as recruiting activity / intent, not headcount.
-- ============================================================

-- ------------------------------------------------------------
-- SETUP 0a: DRP-heavy series named in the Partnership analysis.
-- Verify each code against the Partnership's appendix before
-- publishing; these four are the ones named in press coverage.
-- Add more from their data as you confirm them.
-- ------------------------------------------------------------
DROP TABLE IF EXISTS drp_series;
CREATE TABLE drp_series (series4 TEXT PRIMARY KEY, label TEXT);
INSERT INTO drp_series VALUES
  ('1811', 'Criminal Investigation (FBI flagged, -3.1 grades)'),
  ('1340', 'Meteorology (public-safety flagged)'),
  ('0962', 'Contact Representative (IRS lost 4,566)'),
  ('1862', 'Consumer Safety Inspection (155 out / 155 in)');

-- ------------------------------------------------------------
-- SETUP 0b: unified view over historic + live postings.
--   * DoD grouped across its four sub-codes (AR/NV/AF/DD) so it
--     joins cleanly to drp_separations.csv once that's updated.
--   * series in postings is comma-joined; first code = primary
--     series (codes are 4 chars). historic carries one code.
--   * dedupe on control_number: a closed announcement may sit
--     in both tables once HistoricJoa catches up.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_ann;
CREATE VIEW v_ann AS
SELECT control_number,
       title,
       department_code,
       CASE WHEN department_code IN ('AR','NV','AF','DD')
            THEN 'DOD' ELSE department_code END AS dept_group,
       substr(series, 1, 4)                     AS series4,
       pay_plan, grade_low, grade_high,
       hiring_paths, open_date, close_date,
       total_openings,
       'historic' AS src
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
-- ANGLE 1: "14.7% is a floor, not a ceiling."
-- The Partnership's match stops at June 2026. Every announcement
-- opened in a DRP-heavy series AFTER that cutoff is recruiting
-- activity their 20,557 cannot contain — and that Kupor's "85%
-- real and lasting" arithmetic silently excludes.
-- ============================================================
.print ''
.print '=== ANGLE 1: post-cutoff recruiting in DRP-heavy series ==='
SELECT ds.series4,
       ds.label,
       a.dept_group,
       COUNT(*)                                   AS announcements,
       -- conservative openings floor: numeric counts as-is,
       -- 'MANY'/'FEW'/NULL counted as 1 vacancy minimum
       SUM(CASE WHEN a.total_openings GLOB '[0-9]*'
                THEN CAST(a.total_openings AS INTEGER)
                ELSE 1 END)                       AS min_openings
FROM v_ann a
JOIN drp_series ds ON ds.series4 = a.series4
WHERE a.open_date >= '2026-06-01'
GROUP BY ds.series4, a.dept_group
ORDER BY announcements DESC;

-- Same thing government-wide (all series), monthly, so you can
-- show the recruiting curve did not stop at their cutoff:
.print ''
.print '=== ANGLE 1b: monthly announcement volume, 2026 ==='
SELECT substr(open_date, 1, 7) AS month,
       COUNT(*)                AS announcements
FROM v_ann
WHERE open_date >= '2026-01-01'
GROUP BY month
ORDER BY month;


-- ============================================================
-- ANGLE 2: intent timing. Announcements OPENED during the DRP
-- admin-leave window (Feb–Sep 2025), vs. the same months of
-- 2024. A posting is a deliberate recruiting decision made
-- while DRP participants were still on paid leave.
-- *** Requires completed 2024 backfill for a clean baseline ***
-- ============================================================
.print ''
.print '=== ANGLE 2: DRP-window posting vs 2024 baseline ==='
WITH w AS (
  SELECT dept_group, series4,
         SUM(CASE WHEN open_date BETWEEN '2024-02-01' AND '2024-09-30'
                  THEN 1 ELSE 0 END) AS base_2024,
         SUM(CASE WHEN open_date BETWEEN '2025-02-01' AND '2025-09-30'
                  THEN 1 ELSE 0 END) AS drp_window_2025
  FROM v_ann
  GROUP BY dept_group, series4
)
SELECT w.dept_group, w.series4, ds.label,
       w.base_2024, w.drp_window_2025,
       CASE WHEN w.base_2024 > 0
            THEN ROUND(1.0 * w.drp_window_2025 / w.base_2024, 2)
            ELSE NULL END AS ratio_vs_2024
FROM w
LEFT JOIN drp_series ds ON ds.series4 = w.series4
WHERE w.drp_window_2025 > 0
  AND (ds.series4 IS NOT NULL OR w.drp_window_2025 >= 25)
ORDER BY (ds.series4 IS NOT NULL) DESC, w.drp_window_2025 DESC;


-- ============================================================
-- ANGLE 3: grade drift from the posting side. Partnership found
-- replacements arriving ~1.4 GS grades lower; Kupor didn't
-- dispute it. Test whether ADVERTISED grades in the same series
-- dropped between 2024 and 2026 — independent corroboration
-- from OPM's own recruiting channel.
-- *** Requires completed 2024 backfill ***
-- ============================================================
.print ''
.print '=== ANGLE 3: advertised GS grade drift, 2024 vs 2026 ==='
WITH g AS (
  SELECT series4,
         substr(open_date, 1, 4) AS yr,
         CAST(grade_low  AS REAL) AS glo,
         CAST(grade_high AS REAL) AS ghi
  FROM v_ann
  WHERE pay_plan = 'GS'
    AND grade_low GLOB '[0-9]*'
)
SELECT series4,
       COUNT(CASE WHEN yr = '2024' THEN 1 END)              AS n_2024,
       COUNT(CASE WHEN yr = '2026' THEN 1 END)              AS n_2026,
       ROUND(AVG(CASE WHEN yr = '2024' THEN glo END), 2)    AS lo_2024,
       ROUND(AVG(CASE WHEN yr = '2026' THEN glo END), 2)    AS lo_2026,
       ROUND(AVG(CASE WHEN yr = '2026' THEN glo END)
           - AVG(CASE WHEN yr = '2024' THEN glo END), 2)    AS drift_low,
       ROUND(AVG(CASE WHEN yr = '2026' THEN ghi END)
           - AVG(CASE WHEN yr = '2024' THEN ghi END), 2)    AS drift_high
FROM g
GROUP BY series4
HAVING n_2024 >= 20 AND n_2026 >= 20        -- small-cell guard
ORDER BY drift_low ASC
LIMIT 30;


-- ============================================================
-- BONUS: freeze-period volume check. If posting volume in
-- "frozen" months barely dipped (or exception categories
-- ballooned), "selective hiring" starts to look like ordinary
-- hiring. Runs clean once the 2024-11 backfill lands.
-- ============================================================
.print ''
.print '=== BONUS: monthly volume through the freeze window ==='
SELECT substr(open_date, 1, 7) AS month,
       COUNT(*)                AS announcements,
       SUM(CASE WHEN service_type LIKE 'Excepted%' THEN 1 ELSE 0 END)
                               AS excepted_service
FROM historic
WHERE open_date BETWEEN '2024-11-01' AND '2025-12-31'
GROUP BY month
ORDER BY month;
