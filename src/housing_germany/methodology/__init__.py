"""
Methodology execution (validate, cache, compute, check, log).

    from housing_germany.methodology import run
    run(session, "index_growth", {"geography": "AD02DE1", "window": "5y"})
"""
from housing_germany.methodology.runner import RequestError, report_gap, run
