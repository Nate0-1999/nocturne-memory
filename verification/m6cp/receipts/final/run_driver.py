"""Run a reviewable Playwright step and keep its exact result."""

import sys
import urllib.request
from pathlib import Path

code = Path(sys.argv[1]).read_text()
request = urllib.request.Request(
    "http://127.0.0.1:9657/run",
    method="POST",
    data=code.encode(),
    headers={"Content-Type": "text/plain"},
)
with urllib.request.urlopen(request, timeout=1800) as response:
    result = response.read()
Path(sys.argv[2]).write_bytes(result)
print(result.decode()[:500])
