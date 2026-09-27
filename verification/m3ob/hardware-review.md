# Second computer and phone review

M3OB prepares these two physical-device walks; neither is claimed as executed.
Use only fresh verification homes and the fictional `hardware-fixture.md` beside
this file. Do not use the owner's normal Nocturne home or memories.

On the first computer, install `nocturne-harness==0.1.34` from PyPI in a new
virtual environment. Select the Palace's GCP project for this shell, supply the
disposable OpenRouter key privately, and run:

```sh
python3.12 -m venv /tmp/nocturne-hardware-code
source /tmp/nocturne-hardware-code/bin/activate
python -m pip install --index-url https://pypi.org/simple nocturne-harness==0.1.34
gcloud auth login
export NOCTURNE_HOME="$(mktemp -d /tmp/nocturne-hardware.XXXXXX)"
export CLOUDSDK_CORE_PROJECT=n8-memory-palace
nocturne init --verification
nocturne up
```

Accept the discovered Palace and transcript backup. In Memory Ingest, paste the
fixture, review its two facts, and approve. Ask for the codeword, approve the
memory gate, and keep the app running. Record the verification principal from
`nocturne doctor` in another terminal using this same home.

On the second physical computer, install the same public package, select the
same GCP project, and initialize another fresh verification home. To represent
the same disposable user with a different machine, set only its principal to
the first computer's verification principal (never `local`):

```sh
export REVIEW_PRINCIPAL='nocturne-verification-PASTE-THE-FIRST-COMPUTERS-ID'
python - <<'PY'
import os
from dataclasses import replace
from harness.onboarding import load_config, _write_config
p = os.environ['REVIEW_PRINCIPAL']
assert p.startswith('nocturne-verification-') and 'PASTE' not in p
_write_config(replace(load_config(), principal_id=p))
PY
nocturne up
```

FL-001 passes when the old conversation restores and a new turn recalls
silver-heron from the same Palace. Capture both physical devices and the
restored conversation; record their distinct machine IDs. This does not test
concurrent editing of one conversation.

For FL-101, use an SSH client on a physical phone to connect to the first
computer's existing authorized SSH account. Configure a **local** forward from
phone port 8765 to destination `127.0.0.1:8765` on that computer (the equivalent
desktop command is `ssh -N -L 8765:127.0.0.1:8765 USER@FIRST-COMPUTER`). Open
`http://127.0.0.1:8765` in the phone browser. Send a short prompt, complete its
gate if present, and capture the readable conversation and response. Do not
expose the daemon publicly or change its authentication. A resized desktop
browser is not physical-phone proof.

After review, tombstone only this fixture's memories through their delete
controls, reject any pending fixture proposals, stop both disposable daemons,
close the SSH forward, and remove the two scratch homes. Palace tombstones and
scoped transcript/spend audit records are retained; this is not a hard purge.
