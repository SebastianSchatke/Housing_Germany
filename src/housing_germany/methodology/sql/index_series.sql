-- Build SQL for INDEX_SERIES, recovered verbatim from TASK_REFRESH_INDEX_SERIES on 2026-10-20.
-- {target} is replaced by the pipeline (built as <TABLE>_NEXT, checked, then swapped).
CREATE OR REPLACE TABLE {target} AS
SELECT PLACE_ID, PLACE_TYPE, TRANSACTION_TYPE, ITEM_TYPE, INDEX_DATE, INDEX_VALUE, VOLUME
FROM (
    SELECT
        i.PLACE_ID,
        i.PLACE_TYPE,
        i.TRANSACTION_TYPE,
        i.ITEM_TYPE,
        d.VALUE::DATE AS INDEX_DATE,
        v.VALUE::FLOAT AS INDEX_VALUE,
        vol.VALUE::FLOAT AS VOLUME,
        ROW_NUMBER() OVER (PARTITION BY i.PLACE_ID, i.PLACE_TYPE, i.TRANSACTION_TYPE, i.ITEM_TYPE, d.VALUE::DATE
                           ORDER BY i.RECORD_TS DESC) AS RN
    FROM SILVER_PROD.MKKN_INDICE.INDICE_DE i,
        LATERAL FLATTEN(input => i.DATES) d,
        LATERAL FLATTEN(input => i."VALUES") v,
        LATERAL FLATTEN(input => i.VOLUMES) vol
    WHERE d.INDEX = v.INDEX
      AND d.INDEX = vol.INDEX
      AND i.METRIC = 'METRIC.SQM_PRICE'
      AND i.PLACE_TYPE IN ('AD02', 'AD04', 'AD05', 'AD06', 'AD08')
)
WHERE RN = 1
