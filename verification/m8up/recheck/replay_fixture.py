"""Recover attempt 5's conversation from its recorded provider parts and tool results."""
import hashlib
import json
from pathlib import Path
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from harness.agent_runtime import _strip_all_proposed_response_blocks
from harness.pydantic_harness_adapter import COMPACTION_SUMMARY_PROMPT, located_transcript, own_history
from harness.agent import EXTRACTION_INSTRUCTION

root = Path('/Users/nateoswalt/relay/m8up')
source = Path(__file__).parent/'attempt-5-source-journal.jsonl'
messages = {}
for line in source.read_text().splitlines():
    row = json.loads(line)
    if row.get('message'):
        messages[row['message']['message_id']] = row['message']
history = []
values = list(messages.values())
for user, assistant in zip(values[::2], values[1::2]):
    prompt = user['content']
    if prompt.startswith(('/remember', '/model', '/compact')) or assistant.get('partial') is not False:
        continue
    history = list(_strip_all_proposed_response_blocks(history))
    history.append(ModelRequest([UserPromptPart(prompt)], metadata={'location': user.get('location')}))
    response, returns, previous = [], [], -1
    def flush_response():
        global response
        if response:
            history.append(ModelResponse(response, metadata={'location': assistant.get('location')}))
            response = []
    def flush_returns():
        global returns
        if returns:
            history.append(ModelRequest(returns))
            returns = []
    for event in assistant.get('events', []):
        part = event.get('part', {})
        if event['event_kind'] == 'part_end':
            flush_returns()
            index = event['index']
            if index <= previous:
                flush_response()
            previous = index
            if part['part_kind'] == 'text':
                response.append(TextPart(part['content']))
            elif part['part_kind'] == 'tool-call':
                response.append(ToolCallPart(part['tool_name'], part['args'], part['tool_call_id']))
        elif event['event_kind'] == 'function_tool_call':
            flush_response()
            previous = -1
        elif event['event_kind'] == 'function_tool_result':
            returns.append(ToolReturnPart(part['tool_name'], part['content'], part['tool_call_id']))
    flush_returns()
    flush_response()
transcript, locations = located_transcript(own_history(history))
assert 'probe.html' in transcript and 'M8UP-BROWSER-OK' in transcript
out = root/'harness/verification/m8up/recheck'
(out/'attempt-5-transcript.txt').write_text(transcript)
(out/'fixture.json').write_text(json.dumps({
    'source_journal': source.name,
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'transcript_sha256': hashlib.sha256(transcript.encode()).hexdigest(),
    'reconstruction': 'Recorded completed chat prompts, provider part_end parts and function_tool_result parts; omit /remember, /model and /compact as the runtime does. Strip prior proposed-response blocks at the start of each ordinary chat turn, preserving the final browser response exactly for manual compaction.',
    'messages': len(history), 'locations': locations,
    'summary_prompt': COMPACTION_SUMMARY_PROMPT,
    'extraction_instruction': EXTRACTION_INSTRUCTION,
    'model_settings': {'extra_body': {'session_id':'eb7eec95-cf59-4cb6-9446-8c12ea5aa744:epoch:1'}, 'openrouter_usage':{'include':True}, 'openrouter_cache_instructions':True, 'openrouter_cache_messages':True, 'openrouter_cache_tool_definitions':True, 'temperature':0},
}, indent=2)+'\n')
print('Reconstructed', len(history), 'messages;',len(transcript),'characters')
