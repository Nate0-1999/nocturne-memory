await openApp('Sheet'); await sleep(1500)
await page.getByRole('tab', {name:'Work',exact:true}).click()
const log = []
for (const [index, projectName, color, person, day] of [
  [1,'Orion','copper','Mira','Friday'],[2,'Selene','graphite','Luis','Saturday'],[3,'Vega','indigo','Anika','Monday']
]) {
  await newThread(`/Users/nateoswalt/relay/m6cp/work/final-project-${index}`)
  await send(`This is a fictional release plan. ${person} owns ${projectName}, the notebook is ${color}, and shipping is ${day}. The launch checklist remains open. A kettle is boiling right now; that detail is temporary. Summarize in two sentences. Do not use tools.`)
  await settled(120000)
  for (const cmd of ['/compact keep 0', '/compact instructions Extract the user-stated facts, including temporary details, as separate facts. {messages}', '/compact']) {
    await send(cmd, 'none'); await settled(120000)
  }
  let queue = await api(`/v1/approval-queue?thread_id=${state.threadId}`)
  log.push({index, thread:state.threadId, initialQueue:queue})
  await send('/compact plan on','none'); await settled(120000)
  await send('/compact policy performance 0.0001','none'); await settled(120000)
  await send('We need to preserve the release decisions while the conversation fills. What is the next step?', 'none')
  await settled(120000)
  const questions = await lastAnswer(2000)
  if (!questions.includes('Before I compact this plan:')) throw Error(`No plan questions: ${questions}`)
  await shot(`run${index}/FL-084-questions`)
  await send(`Keep ${person} as release owner for ${projectName}, the ${color} notebook and ${day} shipping. The launch checklist is still open. Discard the kettle detail. Give one concise next step. Do not use tools.`, 'none')
  await settled(120000)
  const answer = await lastAnswer(2000)
  await shot(`run${index}/FL-084-completed`)
  const compactions = await api(`/v1/threads/${state.threadId}/compactions`)
  log.at(-1).questions=questions; log.at(-1).answer=answer; log.at(-1).compactions=compactions
  log.at(-1).finalQueue=await api(`/v1/approval-queue?thread_id=${state.threadId}`)
  await send('/compact policy performance 0.8','none'); await settled(120000)
}
return log
