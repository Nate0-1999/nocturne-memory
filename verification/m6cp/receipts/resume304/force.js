const CFG={"run": "joint", "tag": "A", "control": "Minimum match", "value": 0.3};
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
    if (await answers.count() <= state.answersBefore || !(await c.getByTestId('composer').isEnabled())) return false
    const text = await answers.last().innerText()
    if (text !== last) { last = text; since = Date.now(); return false }
    return Date.now() - since > 4000
  }, timeoutMs, 500)
}

// A manual adjustment from the console: set one control, run DEEP, Force values.
await openApp('Sheet'); await sleep(3000); await layer('Injection')
const con = consoleFrame()
const before = await con.locator('.console-active').innerText()
await setControl(CFG.control, CFG.value)
await deep()
const receipt = await con.locator('[aria-label="Deep simulation receipt"]').innerText()
await con.getByRole('button', { name: 'Force values' }).click()
await waitUntil(async () => (await con.locator('.console-active').innerText()) !== before, 60_000, 300)
await sleep(1500)
await shot(`runs/${CFG.run}/${CFG.tag}-forced`)
return { before, after: await con.locator('.console-active').innerText(), receipt: receipt.slice(0, 300) }
