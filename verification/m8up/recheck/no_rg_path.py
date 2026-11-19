"""Preserve the normal tools while excluding rg from the test/app PATH."""
import json,os,shutil
from pathlib import Path
root=Path('/Users/nateoswalt/relay/m8up')
bin=root/'scratch/no-rg-bin'
bin.mkdir(exist_ok=True)
for directory in (root/'harness/.venv/bin',Path('/usr/bin'),Path('/bin'),Path('/usr/sbin'),Path('/sbin'),Path('/opt/homebrew/bin')):
 for source in directory.iterdir():
  try:
   if source.name!='rg' and source.is_file() and os.access(source,os.X_OK) and not (bin/source.name).exists():
    (bin/source.name).symlink_to(source)
  except OSError:pass
assert shutil.which('rg',path=str(bin)) is None
print(json.dumps({'PATH':str(bin),'rg':None,'suite_cwd':str(root/'harness'),
 'suite_command':".venv/bin/pytest -m 'not contract' -q",'method':'Only this symlink directory on PATH; every rg basename excluded; ChatGPT Resources directory omitted.'},indent=2))
