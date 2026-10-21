-- Governance objects for HOUSING_DEV.MARKET_INSIGHTS_AGENTS (deployed 2026-10-21).

-- Methodology matcher: searchable text per registry entry.
CREATE OR REPLACE VIEW HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_DOCS AS
SELECT METHODOLOGY_ID, VERSION, STATUS, TITLE, COMMIT_SHA,
       'Methodology ' || METHODOLOGY_ID || ' v' || VERSION || ' (' || STATUS || '): ' || COALESCE(TITLE,'') ||
       '\nExample questions: ' || ARRAY_TO_STRING(SPEC:question_patterns::ARRAY, '; ') ||
       '\nSources: ' || ARRAY_TO_STRING(SPEC:sources::ARRAY, ', ') ||
       '\nInputs: ' || TO_VARCHAR(SPEC:inputs) ||
       '\nFixed choices: ' || COALESCE(TO_VARCHAR(SPEC:fixed_choices), '{}') ||
       '\nOpen questions: ' || COALESCE(ARRAY_TO_STRING(SPEC:open_questions::ARRAY, ' | '), 'none') AS DOC
FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_REGISTRY;

CREATE OR REPLACE CORTEX SEARCH SERVICE HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_SEARCH
  ON DOC ATTRIBUTES METHODOLOGY_ID, STATUS
  WAREHOUSE = WH_HOUSING_DEV TARGET_LAG = '1 day'
  COMMENT = 'Methodology matcher: find existing methodologies (approved or draft) that fit a request before proposing a new one'
AS SELECT DOC, METHODOLOGY_ID, VERSION, STATUS, TITLE, COMMIT_SHA FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_DOCS;

-- Cost: ACCOUNT_USAGE is not granted to SNF_R_HOUSING_DATA_ANALYST, so credits are estimated
-- from logged runtimes (X-Small = 1 credit/hour).
CREATE OR REPLACE VIEW HOUSING_DEV.MARKET_INSIGHTS_AGENTS.COST_SUMMARY
COMMENT = 'Per-methodology runs and ESTIMATED credits (runtime x 1 credit/h for X-Small).'
AS
SELECT METHODOLOGY_ID,
       IFF(REQUEST_HASH LIKE 'refresh:%', 'refresh', 'request') AS RUN_KIND,
       DATE_TRUNC('week', STARTED_AT) AS WEEK,
       COUNT(*) AS RUNS,
       COUNT_IF(STATUS IN ('flagged')) AS FLAGGED,
       ROUND(AVG(RUNTIME_SECONDS), 1) AS AVG_RUNTIME_S,
       ROUND(MAX(RUNTIME_SECONDS), 1) AS MAX_RUNTIME_S,
       ROUND(SUM(RUNTIME_SECONDS) / 3600, 4) AS EST_CREDITS
FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.RUN_LOG
GROUP BY 1, 2, 3;

-- Weekly sweep: slow runs, stale published data, old untriaged gaps -> ISSUES.
CREATE OR REPLACE TASK HOUSING_DEV.MARKET_INSIGHTS_AGENTS.TASK_WEEKLY_GOVERNANCE
  WAREHOUSE = WH_HOUSING_DEV
  SCHEDULE = 'USING CRON 0 8 * * 1 Europe/Berlin'
  COMMENT = 'Weekly: flag slow methodology runs, stale published tables and untriaged gap requests'
AS
INSERT INTO HOUSING_DEV.MARKET_INSIGHTS_AGENTS.ISSUES (SEVERITY, KIND, METHODOLOGY_ID, MESSAGE, DETAILS)
SELECT 'warning', 'cost', METHODOLOGY_ID,
       METHODOLOGY_ID || ' ' || RUN_KIND || ' runs averaged ' || AVG_RUNTIME_S || 's last week',
       OBJECT_CONSTRUCT('runs', RUNS, 'avg_runtime_s', AVG_RUNTIME_S, 'est_credits', EST_CREDITS)
FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.COST_SUMMARY
WHERE WEEK = DATE_TRUNC('week', DATEADD('week', -1, CURRENT_DATE()))
  AND ((RUN_KIND = 'request' AND AVG_RUNTIME_S > 30) OR (RUN_KIND = 'refresh' AND AVG_RUNTIME_S > 1800))
UNION ALL
SELECT 'error', 'drift', 'index_growth', 'Published MARKET_SUMMARY index date is more than 70 days old',
       OBJECT_CONSTRUCT('latest_date', MAX(LATEST_DATE))
FROM HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS.MARKET_SUMMARY
HAVING MAX(LATEST_DATE) < DATEADD('day', -70, CURRENT_DATE())
UNION ALL
SELECT 'info', 'gap', NULL, COUNT(*) || ' gap requests open for more than 14 days',
       OBJECT_CONSTRUCT('oldest', MIN(CREATED_AT))
FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.ISSUES
WHERE KIND = 'gap' AND STATUS = 'open' AND CREATED_AT < DATEADD('day', -14, CURRENT_TIMESTAMP())
HAVING COUNT(*) > 0;

ALTER TASK HOUSING_DEV.MARKET_INSIGHTS_AGENTS.TASK_WEEKLY_GOVERNANCE RESUME;
