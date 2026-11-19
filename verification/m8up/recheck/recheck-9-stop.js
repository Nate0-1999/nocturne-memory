await frame('conversation').getByTestId('stop').click();
await waitUntil(async()=>await frame('conversation').getByTestId('composer').isEnabled(),45000);
await frame('conversation').getByTestId('composer').fill('');
await shot('recheck-9-interrupted'); return {thread:state.threadId,answer:await lastAnswer(1500)};
