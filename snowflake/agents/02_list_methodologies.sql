-- Registry lookup used by the HOUSING_ANALYST agent (list_methodologies tool).
CREATE OR REPLACE PROCEDURE HOUSING_DEV.MARKET_INSIGHTS_AGENTS.LIST_METHODOLOGIES()
RETURNS VARIANT
LANGUAGE SQL
COMMENT = 'Methodologies in the registry with status, inputs and fixed choices, for the orchestrator agent'
EXECUTE AS CALLER
AS
$$
DECLARE
  res VARIANT;
BEGIN
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(
           'id', METHODOLOGY_ID, 'version', VERSION, 'status', STATUS, 'title', TITLE,
           'question_patterns', SPEC:question_patterns, 'inputs', SPEC:inputs,
           'fixed_choices', SPEC:fixed_choices, 'open_questions', SPEC:open_questions))
           WITHIN GROUP (ORDER BY METHODOLOGY_ID, VERSION)
    INTO :res
  FROM HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_REGISTRY
  WHERE STATUS <> 'deprecated';
  RETURN res;
END;
$$;
