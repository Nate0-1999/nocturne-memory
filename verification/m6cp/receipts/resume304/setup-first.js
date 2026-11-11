await openApp('Sheet'); await sleep(1500)
await page.getByRole('tab', {name:'Work',exact:true}).click()
const thread = await newThread('/Users/nateoswalt/relay/m6cp/work/final-project-1')
const prompt = 'This is a fictional release plan. Mira owns Orion, the notebook is copper, and shipping is Friday. The launch checklist remains open. A kettle is boiling right now; that detail is temporary. Summarize in two sentences. Do not use tools.'
const started = new Date().toISOString()
await send(prompt)
await settled(120000)
const answer = await lastAnswer(8000)
const failed = /timed out|stopped answering|did not answer|ReadTimeout|request failed/i.test(answer)
const screenshot = await shot(failed ? 'setup-first-provider-timeout' : 'setup-first-complete')
return {thread, prompt, started, completed:new Date().toISOString(), answer, failed, screenshot}
