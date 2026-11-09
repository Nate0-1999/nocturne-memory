"""Keep the original packet baseline across scratch-home restarts."""
import json
import sys
import urllib.request
from pathlib import Path
from harness.onboarding import load_config

root=Path('/Users/nateoswalt/relay/m6cp')
c=load_config(home=root/'scratch/local')
request=urllib.request.Request('https://openrouter.ai/api/v1/key',
    headers={'Authorization':'Bearer '+c.openrouter_api_key})
data=json.load(urllib.request.urlopen(request,timeout=30))['data']
baseline=82.769384839
result={name:data.get(name) for name in ('usage','usage_daily','limit','limit_remaining')}
result.update(original_packet_usage=baseline,packet_growth_including_peers=data['usage']-baseline)
(root/sys.argv[1]).write_text(json.dumps(result,indent=2))
print(json.dumps(result))
