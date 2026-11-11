"""Seed fictional memories through the product API; record key totals without secrets."""

import ast
import json
import urllib.request
from pathlib import Path

from harness.onboarding import load_config

root = Path("/Users/nateoswalt/relay/m6cp")
c = load_config(home=root / "scratch/local")
request = urllib.request.Request(
    "https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + c.openrouter_api_key}
)
data = json.load(urllib.request.urlopen(request, timeout=30))["data"]
(root / "logs/phase2-spend-start.json").write_text(
    json.dumps(
        {
            "original_packet_usage": 82.769384839,
            **{
                name: data.get(name)
                for name in ("usage", "limit", "limit_remaining", "usage_daily")
            },
        }
    )
)
tree = ast.parse((root / "garden/verification/m6lp/lp09/receipts/seed.py").read_text())
facts = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "FACTS" for t in node.targets)
)
made = []
for label, body in facts:
    request = urllib.request.Request(
        "http://127.0.0.1:8013/v1/memories",
        method="POST",
        data=json.dumps(
            {
                "principal_id": c.principal_id,
                "machine_id": c.machine_id,
                "label": label,
                "body": body,
                "kind": "fact",
                "editor": "m6cp-seed",
                "force": True,
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + c.spine_token},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        made.append({"status": response.status, "label": label})
(root / "logs/phase2-seed.json").write_text(json.dumps(made, indent=2))
print("Seeded", len(made), "fictional memories; verification identity preserved.")
