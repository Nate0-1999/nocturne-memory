await openApp('Sheet'); await sleep(1500)
await page.getByRole('tab', {name:'Work',exact:true}).click()
await newThread('/Users/nateoswalt/relay/m6cp/work/final-project-3')
const firstPrompt = 'This is a fictional release plan. Anika owns Vega, the notebook is indigo, and shipping is Monday. The launch checklist remains open. A kettle is boiling right now; that detail is temporary. Summarize in two sentences. Do not use tools.'
await send(firstPrompt); await settled(120000)
const firstAnswer = await lastAnswer(8000)
if (/timed out|stopped answering|did not answer|ReadTimeout|request failed/i.test(firstAnswer)) {
  await shot('fixture-3-first-provider-timeout')
  return {index:3,thread:state.threadId,failed:true,prompt:firstPrompt,answer:firstAnswer}
}
const index = 3
const [projectName,color,person,day] = [['Orion','copper','Mira','Friday'],['Selene','graphite','Luis','Saturday'],['Vega','indigo','Anika','Monday']][index-1]
const log = []
async function step(prompt) {
  const started = new Date().toISOString()
  await send(prompt,'none'); await settled(120000)
  const answer = await lastAnswer(8000)
  const failed = /timed out|stopped answering|did not answer|ReadTimeout|request failed/i.test(answer)
  log.push({prompt,started,completed:new Date().toISOString(),answer,failed})
  if (failed) await shot(`fixture-${index}-provider-timeout`)
  return !failed
}
for (const prompt of ['/compact keep 0','/compact instructions Extract the user-stated facts, including temporary details, as separate facts. {messages}','/compact','/compact plan on','/compact policy performance 0.0001','We need to preserve the release decisions while the conversation fills. What is the next step?',`Keep ${person} as release owner for ${projectName}, the ${color} notebook and ${day} shipping. The launch checklist is still open. Discard the kettle detail. Give one concise next step. Do not use tools.`,'/compact policy performance 0.8']) {
  if (!await step(prompt)) return {index,thread:state.threadId,failed:true,log}
}
await shot(`fixture-${index}-complete`)
return {index,thread:state.threadId,failed:false,log,compactions:await api(`/v1/threads/${state.threadId}/compactions`),queue:await api(`/v1/approval-queue?thread_id=${state.threadId}`)}
