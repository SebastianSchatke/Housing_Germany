"""
Smoke test for the deployed entry point using Streamlit's AppTest.

Runs the real streamlit_app.py against a live Snowpark session and fails on
any exception. Run: python3 test_app.py
"""
import os
import sys

from snowflake.snowpark import Session
from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

session = Session.builder.config(
    "connection_name", os.environ.get("SNOWFLAKE_CONNECTION_NAME", "aviv-prod")
).create()
session.sql("USE ROLE SNF_R_DATA_ANALYST").collect()
session.sql("USE WAREHOUSE WH_PROD_PERSONA").collect()

at = AppTest.from_file(os.path.join(HERE, "streamlit_app.py"), default_timeout=600)
# Pre-seed what streamlit_app.py would otherwise get from st.connection("snowflake").
at.session_state["conn"] = object()
at.session_state["session"] = session
at.run()

errors = list(at.exception)
print(f"metrics={len(at.metric)} markdown={len(at.markdown)} exceptions={len(errors)}")
for e in errors:
    print("EXCEPTION:", e.message)
    print("\n".join(e.stack_trace[-6:]))
session.close()
sys.exit(1 if errors else 0)
