"""Observe the real extractor without changing its prompt, model or retry allowance."""
import asyncio
import hashlib
import json
import os
import sys
import urllib.request
from importlib.metadata import version
from pathlib import Path
from harness.agent import HarnessAgent, EXTRACTION_INSTRUCTION
from harness.config import HarnessSettings
from harness.onboarding import load_config
from harness.pydantic_harness_adapter import COMPACTION_SUMMARY_PROMPT

ROOT=Path('/Users/nateoswalt/relay/m8up')
OUT=ROOT/'harness/verification/m8up/recheck'
fixture=json.loads((OUT/'fixture.json').read_text())
transcript=(OUT/'attempt-5-transcript.txt').read_text()
assert COMPACTION_SUMMARY_PROMPT==fixture['summary_prompt']
assert EXTRACTION_INSTRUCTION==fixture['extraction_instruction']
assert hashlib.sha256(transcript.encode()).hexdigest()==fixture['transcript_sha256']
config=load_config(home=ROOT/'walk/home')
os.environ.update(config.process_environment())
key=config.process_environment()['OPENROUTER_API_KEY']
def meter():
    req=urllib.request.Request('https://openrouter.ai/api/v1/key',headers={'Authorization':'Bearer '+key})
    return json.load(urllib.request.urlopen(req))['data']['usage']
def payment(exc):
    seen=set()
    while exc and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc,'status_code',None)==402 or getattr(getattr(exc,'response',None),'status_code',None)==402:return True
        exc=exc.__cause__
    return False
async def run():
    side,index=sys.argv[1],int(sys.argv[2])
    start_path=OUT/'budget.json'
    current=meter()
    if not start_path.exists():start_path.write_text(json.dumps({'key_start':current,'prior_packet_spend':0.06945352}))
    budget=json.loads(start_path.read_text())
    spent=current-budget['key_start']+budget['prior_packet_spend']
    assert spent<2, 'Packet spend wall reached'
    settings=HarnessSettings(chat_model='openrouter:openai/gpt-4.1-mini',
        model_policy_chat='pinned:openrouter:openai/gpt-4.1-mini',
        model_policy_subagent='pinned:openrouter:openai/gpt-4.1-mini',
        model_policy_judge='pinned:openrouter:openai/gpt-4.1-mini')
    agent=HarnessAgent(settings)
    model=agent.model_for('openrouter:openai/gpt-4.1-mini')
    original=model.request
    outputs=[]
    requests=[]
    async def observed(*args,**kwargs):
        messages=args[0] if args else kwargs['messages']
        requests.append([{'type':type(m).__name__,'instructions':getattr(m,'instructions',None),
          'parts':[{'type':type(p).__name__,'content':str(getattr(p,'content',''))} for p in m.parts]} for m in messages])
        reply=await original(*args,**kwargs)
        outputs.append(reply.text)
        return reply
    model.request=observed
    result={'side':side,'run':index,'versions':{n:version(n) for n in ('nocturne-harness','pydantic-ai','pydantic-ai-harness')},
      'transcript_sha256':fixture['transcript_sha256'],'instruction_sha256':hashlib.sha256(EXTRACTION_INSTRUCTION.encode()).hexdigest(),
      'summary_sha256':hashlib.sha256(COMPACTION_SUMMARY_PROMPT.encode()).hexdigest()}
    try:
        await agent.extract_thread(transcript,model=model,model_settings=fixture['model_settings'],summary_prompt=fixture['summary_prompt'])
        result['completed']=True
    except Exception as exc:
        result.update(completed=False,error=type(exc).__name__,detail=str(exc),payment_required=payment(exc))
    drafts=[]
    for output in outputs:
        try:
            parsed=json.loads(output.strip().removeprefix('```json').removesuffix('```').strip())
            labels=[c.get('label') for c in parsed.get('candidates',[])]
            drafts.append({'overlong_labels':[label for label in labels if isinstance(label,str) and len(label)>64]})
        except Exception:
            drafts.append({'unparsed':True})
    bad=bool(drafts and drafts[0].get('overlong_labels'))
    result.update(first_overlong=bad,retry_recovered=bad and result['completed'],drafts=drafts,outputs=outputs,requests=requests,
        packet_spend_after=meter()-budget['key_start']+budget['prior_packet_spend'])
    (OUT/f'{side}-{index:02}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('side','run','completed','first_overlong','retry_recovered','packet_spend_after')}),flush=True)
    if result.get('payment_required') or result['packet_spend_after']>=2:sys.exit(2)
asyncio.run(run())
