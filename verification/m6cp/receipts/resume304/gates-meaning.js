const CFG={"run": "joint", "tag": "G1", "project": "/Users/nateoswalt/relay/m6cp/work/orchard-ledger", "rule": "meaning", "prompts": ["Plan this week's harvest: how many crates go to cold storage and when does the ledger close?", "Which apples should go to cider instead of the market, and when does the press run?", "Write a short note for the pickers about shift times and the tags on field bins.", "What protects the trees on a frost night, and who gets woken up?", "Summarize how fruit moves from the field through the shed to the market truck.", "Draft an outline for the weekly report of crates picked per grove row."]};
// M6LP walk helpers, prepended to each part (the driver evaluates one async body).
const layer = async (name) => {
  await page.locator('button[role="tab"]').filter({ hasText: new RegExp(`^${name}$`, 'i') }).first().click({ force: true })
  await sleep(2500)
}
const consoleFrame = () => frame('injection_console')
const num = (lines, label) => { const i = lines.indexOf(label); const v = Number(lines[i + 1]); return Number.isFinite(v) ? v : 0 }
async function readCards() {
  const gate = frame('gate'), cards = gate.getByTestId('memory-card'), n = await cards.count(), read = []
  for (let k = 0; k < n; k++) {
    const card = cards.nth(k)
    await card.locator('h4').hover(); await sleep(450)
    const lines = (await gate.locator('.feature-radar figcaption').first().innerText()).split('\n').map(s => s.trim())
    read.push({ id: await card.getAttribute('data-memory-id'), label: await card.locator('h4').innerText(),
      tone: await card.getAttribute('data-tone'), sem: num(lines, 'SEMANTIC'), kw: num(lines, 'KEYWORD') })
  }
  await page.mouse.move(4, 600); await sleep(300)
  return read
}
async function setControl(name, value) {
  const input = consoleFrame().locator(`.control-bank > label:has(> span:text-is("${name}")) input`)
  await input.fill(String(value)); await sleep(300)
}
async function deep() {
  await consoleFrame().getByRole('button', { name: 'Run DEEP simulation' }).click()
  await waitUntil(async () => (await consoleFrame().locator('[aria-label="Deep simulation receipt"]').count()) > 0, 120_000, 300)
  await sleep(800)
}
// The driver's settled() reads the whole transcript for /Sending/i, which a packet answer itself
// says ("sending the packet later"); this waits for a new answer whose text holds still for 4 s.
async function answered(timeoutMs = 600_000) {
  const c = frame('conversation'), answers = c.locator('article[data-role="assistant"]')
  let last = null, since = Date.now()
  await waitUntil(async () => {
    const gate = frame('gate').getByTestId('memory-gate-continue')
    if (await gate.isVisible().catch(() => false)) await gate.click().catch(() => {})
    if (await answers.count() <= state.answersBefore || await c.locator('.run-state').count() || !(await c.getByTestId('composer').isEnabled())) return false
    const text = await answers.last().innerText()
    if (text !== last) { last = text; since = Date.now(); return false }
    return Date.now() - since > 4000
  }, timeoutMs, 500)
}

// Gates for one project: each prompt opens a new thread; its first-turn memory check is read card
// by card (the hover's Semantic and Keyword values) and decided by the project's rule, then continued.
// meaning: keep/add a card whose Semantic is at least the gate's median, remove the rest.
// keywords: keep/add a card with a Keyword match, remove the rest. Near misses failing the rule are left alone.
await openApp('Sheet'); await sleep(3000); await layer('Work')
const log = []
for (const [i, prompt] of CFG.prompts.entries()) {
  await newThread(CFG.project)
  await send(prompt, 'leave')
  const gate = frame('gate')
  await gate.getByTestId('memory-gate').waitFor({ state: 'visible', timeout: 90_000 }); await sleep(1200)
  const source = await gate.getByTestId('memory-gate-source').innerText()
  if (/local copy/i.test(source)) {
    // M4BW: the Palace did not answer in time, so the check came from the local copy; that is not
    // a Palace gate for the learner. Stop this run and ask again in a fresh thread.
    await shot(`runs/${CFG.run}/${CFG.tag}-${CFG.rule}-${i + 1}-local-copy`)
    await gate.getByTestId('memory-gate-stop').click(); await sleep(3000)
    log.push({ prompt, thread: state.threadId, localCopy: source })
    return {failed:true,index:i,thread:state.threadId,prompt,localCopy:source,log}
  }
  const cards = await readCards()
  const sems = cards.map(c => c.sem).sort((a, b) => a - b)
  const median = sems.length ? sems[Math.floor((sems.length - 1) / 2)] : 0
  const decided = cards.map(c => ({ ...c, pass: CFG.rule === 'meaning' ? c.sem >= median : c.kw > 0 }))
  for (const c of decided) {
    if (c.tone === 'injected' && !c.pass) { await gate.locator(`[data-testid="memory-remove"][data-memory-id="${c.id}"]`).click(); await sleep(250); c.action = 'removed' }
    else if (c.tone === 'near-miss' && c.pass) { await gate.locator(`[data-testid="near-miss-toggle"][data-memory-id="${c.id}"]`).click(); await sleep(250); c.action = 'added' }
    else c.action = c.tone === 'injected' ? 'kept' : 'left'
  }
  await shot(`runs/${CFG.run}/${CFG.tag}-${CFG.rule}-${i + 1}`)
  await gate.getByTestId('memory-gate-continue').click()
  await answered()
  const answer = await lastAnswer(8000)
  if (/timed out|stopped answering|did not answer|ReadTimeout|request failed/i.test(answer)) {
    await shot(`gates-${CFG.rule}-${i+1}-provider-timeout`)
    return {failed:true,index:i,thread:state.threadId,prompt,answer,log}
  }
  log.push({ prompt, thread: state.threadId, median, cards: decided.map(({ label, tone, sem, kw, action }) => ({ label, tone, sem, kw, action })), answer: (await lastAnswer(160)) })
}
return log
