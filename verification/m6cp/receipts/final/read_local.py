"""Read-only local proof: feedback, proposed policies and model receipts."""

import asyncio
import json
import sys
from pathlib import Path

import asyncpg
from harness.onboarding import load_config


async def main():
    root = Path("/Users/nateoswalt/relay/m6cp")
    c = load_config(home=root / "scratch/local")
    db = await asyncpg.connect(c.database_url.replace("postgresql+asyncpg:", "postgresql:"))
    queries = {
        "scorers": "SELECT version, active, params FROM scorer_config ORDER BY version",
        "creation_counts": (
            "SELECT source, outcome, count(*) FROM creation_outcome GROUP BY 1,2 ORDER BY 1,2"
        ),
        "feedback_counts": "SELECT outcome, count(*) FROM injection_event GROUP BY 1 ORDER BY 1",
        "generations": (
            "SELECT run_uid, loop, verdict, challenger_version, challenger_params, "
            "backtest_scores, corpus_size, corpus_stratification "
            "FROM optimization_run ORDER BY run_uid"
        ),
        "spend": (
            "SELECT product_type, purpose, ref, sum(cost_usd) AS usd "
            "FROM spend_event GROUP BY 1,2,3 ORDER BY 1,2,3"
        ),
        "model_spend": (
            "SELECT model, product_type, count(*) AS lines, sum(cost_usd) AS usd, "
            "count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced "
            "FROM spend_event GROUP BY 1,2 ORDER BY 1,2"
        ),
    }
    result = {name: [dict(row) for row in await db.fetch(sql)] for name, sql in queries.items()}
    await db.close()
    (root / sys.argv[1]).write_text(json.dumps(result, default=str, indent=2))
    print({name: result[name] for name in ("creation_counts", "feedback_counts")})


asyncio.run(main())
