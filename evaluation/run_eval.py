"""Evaluate the agent on evaluation/test_queries.csv.

    uv run python evaluation/run_eval.py                      # all cases
    uv run python evaluation/run_eval.py --category negative  # one category
    uv run python evaluation/run_eval.py --ids NEG-02 EDGE-01 # selected cases
    uv run python evaluation/run_eval.py --no-judge           # forbidden by exact text only
    uv run python evaluation/run_eval.py --judge-model light  # cheaper judge

Results: evaluation/results/<timestamp>/ (results.csv, summary.json, review.md).
Logic lives in travel_agent.evaluation.
"""

from travel_agent.evaluation import main

if __name__ == "__main__":
    main()
