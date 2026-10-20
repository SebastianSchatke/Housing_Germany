-- Methodology registry, run log and issues for HOUSING_DEV.MARKET_INSIGHTS_AGENTS.
-- Idempotent: safe to re-run.
USE SCHEMA HOUSING_DEV.MARKET_INSIGHTS_AGENTS;

CREATE TABLE IF NOT EXISTS METHODOLOGY_REGISTRY (
    METHODOLOGY_ID   VARCHAR NOT NULL,
    VERSION          NUMBER  NOT NULL,
    STATUS           VARCHAR NOT NULL,          -- draft | approved | deprecated
    TITLE            VARCHAR,
    SPEC             VARIANT NOT NULL,          -- full parsed YAML
    SPEC_PATH        VARCHAR NOT NULL,          -- path in the Housing_Germany repo
    COMMIT_SHA       VARCHAR NOT NULL,          -- repo commit the spec was loaded from
    LOADED_AT        TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_METHODOLOGY PRIMARY KEY (METHODOLOGY_ID, VERSION)
)
COMMENT = 'Versioned methodology specs synced from methodologies/ in the Housing_Germany repo';

CREATE TABLE IF NOT EXISTS RUN_LOG (
    RUN_ID           VARCHAR NOT NULL,
    REQUEST_HASH     VARCHAR NOT NULL,          -- sha256 of methodology id, version, canonical params, data snapshot
    METHODOLOGY_ID   VARCHAR NOT NULL,
    VERSION          NUMBER  NOT NULL,
    COMMIT_SHA       VARCHAR,
    PARAMS           VARIANT,
    DATA_SNAPSHOT    VARCHAR,                   -- latest source date the run saw
    STATUS           VARCHAR NOT NULL,          -- running | passed | failed | rerun_passed | flagged | cached
    CHECK_RESULTS    VARIANT,
    RESULT           VARIANT,                   -- small results inline; large results go to RUN_RESULTS
    ATTEMPT          NUMBER DEFAULT 1,
    STARTED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    FINISHED_AT      TIMESTAMP_LTZ,
    RUNTIME_SECONDS  FLOAT,
    QUERY_TAG        VARCHAR,                   -- joins to ACCOUNT_USAGE.QUERY_HISTORY for cost
    RUN_BY           VARCHAR DEFAULT CURRENT_USER(),
    CONSTRAINT PK_RUN PRIMARY KEY (RUN_ID)
)
COMMENT = 'One row per methodology execution. Same REQUEST_HASH means a reproducible, cacheable request';

CREATE TABLE IF NOT EXISTS RUN_RESULTS (
    RUN_ID           VARCHAR NOT NULL,
    PLACE_ID         VARCHAR,
    ITEM_TYPE        VARCHAR,
    TRANSACTION_TYPE VARCHAR,
    PERIOD_DATE      DATE,
    METRIC           VARCHAR NOT NULL,
    VALUE            FLOAT
)
COMMENT = 'Long-format results per run (time series and per-place metrics)';

CREATE TABLE IF NOT EXISTS ISSUES (
    ISSUE_ID         VARCHAR DEFAULT UUID_STRING(),
    CREATED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    SEVERITY         VARCHAR NOT NULL,          -- info | warning | error
    KIND             VARCHAR NOT NULL,          -- check_failed | gap | drift | cost
    METHODOLOGY_ID   VARCHAR,
    VERSION          NUMBER,
    RUN_ID           VARCHAR,
    MESSAGE          VARCHAR NOT NULL,
    DETAILS          VARIANT,
    STATUS           VARCHAR DEFAULT 'open'     -- open | acknowledged | resolved
)
COMMENT = 'Flags from admin checks, gap reports, drift and cost monitoring';

CREATE OR REPLACE VIEW APPROVED_METHODOLOGIES AS
SELECT METHODOLOGY_ID, VERSION, TITLE, SPEC, COMMIT_SHA, LOADED_AT
FROM METHODOLOGY_REGISTRY
WHERE STATUS = 'approved'
QUALIFY ROW_NUMBER() OVER (PARTITION BY METHODOLOGY_ID ORDER BY VERSION DESC) = 1;
