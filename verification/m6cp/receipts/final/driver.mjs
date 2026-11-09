// M5VW walk driver (after M4W1B receipts/driver.mjs) (after m3w6b/receipts/driver.mjs) (after m3ex/receipts/driver.mjs): holds one headless Chrome page on the
// walk app; POST /run evaluates an async body with the helpers below in scope. Row logic is
// posted, never kept here.
import { createRequire } from 'node:module'
import http from 'node:http'
import { appendFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
const require = createRequire('/Users/nateoswalt/relay/m6cp/harness/web/package.json')
const { chromium } = require('playwright-core')
const ROOT = process.env.WALK_ROOT ?? '/Users/nateoswalt/relay/m6cp/walk'
const BASE = process.env.WALK_BASE ?? 'http://127.0.0.1:8873'
const CAP = `${ROOT}/captures`, CLIPS = `${ROOT}/clips`
for (const dir of [CAP, CLIPS, `${ROOT}/logs`]) mkdirSync(dir, { recursive: true })
const context = await chromium.launchPersistentContext(`${ROOT}/m6cp-driver-profile`, {
  channel: 'chrome', headless: true, args: ['--use-angle=metal', '--enable-gpu', '--enable-unsafe-webgpu'],
  viewport: { width: 1600, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'],
})
let page = context.pages()[0] ?? await context.newPage()
page.setDefaultTimeout(15_000)
const hook = p => {
  p.on('console', m => { if (m.type() === 'error') appendFileSync(`${ROOT}/logs/console.log`, `${new Date().toISOString()} ${m.type()} ${m.text().slice(0, 500)}\n`) })
  p.on('pageerror', e => appendFileSync(`${ROOT}/logs/console.log`, `${new Date().toISOString()} pageerror ${e.message}\n`))
}
hook(page)
context.on('page', p => hook(p))
const frame = id => page.frameLocator(`[data-testid="rack-plugin-frame-${id}"]`)
const sleep = ms => new Promise(r => setTimeout(r, ms))
async function shot(name, opts = {}) {
  const file = `${CAP}/${name}.png`
  mkdirSync(file.slice(0, file.lastIndexOf('/')), { recursive: true })
  await page.mouse.move(4, 600); await sleep(300)
  if (opts.module) await page.locator(`[data-testid="rack-plugin-frame-${opts.module}"]`).screenshot({ path: file })
  else await page.screenshot({ path: file, fullPage: opts.fullPage ?? false })
  appendFileSync(`${ROOT}/captures.jsonl`, JSON.stringify({ t: new Date().toISOString(), file: name, note: opts.note ?? '' }) + '\n')
  return file
}
let rec = null
const clip = {
  async start(name) {
    const cdp = await context.newCDPSession(page)
    const dir = `${CLIPS}/${name}-frames`; mkdirSync(dir, { recursive: true })
    rec = { name, cdp, dir, frames: [] }
    cdp.on('Page.screencastFrame', async f => {
      const n = rec.frames.length
      writeFileSync(`${dir}/${String(n).padStart(5, '0')}.jpg`, Buffer.from(f.data, 'base64'))
      rec.frames.push(f.metadata.timestamp)
      try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }) } catch {}
    })
    await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 1600, maxHeight: 1000, everyNthFrame: 1 })
    return dir
  },
  async stop(seconds = 10) {
    if (!rec) return null
    await rec.cdp.send('Page.stopScreencast'); await sleep(300)
    const { name, dir, frames } = rec; rec = null
    if (!frames.length) return 'no frames'
    const lines = frames.map((t, i) => `file '${dir}/${String(i).padStart(5, '0')}.jpg'\nduration ${Math.max(0.04, ((frames[i + 1] ?? t + 0.5) - t)).toFixed(3)}`)
    lines.push(`file '${dir}/${String(frames.length - 1).padStart(5, '0')}.jpg'`)
    writeFileSync(`${dir}/list.txt`, lines.join('\n'))
    const out = `${CLIPS}/${name}.mp4`
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', `${dir}/list.txt`, '-vf', 'scale=1280:-2,format=yuv420p', '-r', '15', '-t', String(seconds), out])
    return { out, frames: frames.length, seconds: (frames.at(-1) - frames[0]).toFixed(1) }
  },
}
// App helpers (after m3sk/receipts/walk.mjs).
const api = async (path, init) => {
  const response = await fetch(`${BASE}${path}`, init)
  const text = await response.text()
  let body; try { body = JSON.parse(text) } catch { body = text }
  if (!response.ok) throw new Error(`${response.status} ${path}: ${text.slice(0, 400)}`)
  return body
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })
async function waitUntil(predicate, timeoutMs = 30_000, every = 250) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try { if (await predicate()) return true } catch (error) { lastError = error }
    await sleep(every)
  }
  throw lastError ?? new Error('walk condition did not settle')
}
const state = { threadId: null, answersBefore: 0, threads: [] }
async function openApp(view = 'Sheet') {
  const response = await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  if (response === null || !response.ok()) throw new Error(`app did not load: ${response?.status()}`)
  if (view) await page.getByRole('button', { name: view, exact: true }).click()
}
async function newThread(project) {
  const list = frame('threads')
  const before = new Set((await api('/v1/transcripts/catalog')).threads.map(t => t.thread_id))
  const folder = list.getByPlaceholder('Choose or type a folder path')
  await list.getByTestId('new-thread').waitFor()
  for (let attempt = 0; attempt < 6 && !(await folder.isVisible()); attempt += 1) {
    await list.getByTestId('new-thread').click(); await sleep(1_500)
  }
  await folder.fill(project)
  await list.getByRole('button', { name: 'Create', exact: true }).click()
  await waitUntil(async () => (await api('/v1/transcripts/catalog')).threads.some(t => !before.has(t.thread_id)))
  state.threadId = (await api('/v1/transcripts/catalog')).threads.find(t => !before.has(t.thread_id)).thread_id
  state.threads.push(state.threadId)
  const conversation = frame('conversation')
  await conversation.getByTestId('composer').waitFor({ state: 'visible' })
  await waitUntil(async () => conversation.getByTestId('composer').isEnabled())
  await waitUntil(async () => (await conversation.locator('article[data-role="assistant"]').count()) === 0)
  return state.threadId
}
// gate: 'continue' clicks the first-turn memory check through; 'leave' leaves it open for the row.
async function send(prompt, gate = 'continue') {
  const conversation = frame('conversation')
  await waitUntil(async () => await conversation.getByTestId('composer').isEnabled() && await conversation.getByTestId('composer').inputValue() === '', 120_000)
  state.answersBefore = await conversation.locator('article[data-role="assistant"]').count()
  await conversation.getByTestId('composer').fill(prompt)
  await conversation.getByTestId('composer').press('Enter')
  if (gate !== 'continue') return
  try {
    await frame('gate').getByTestId('memory-gate').waitFor({ state: 'visible', timeout: 20_000 })
    await frame('gate').getByTestId('memory-gate-continue').click()
  } catch { /* later turns and commands open no first-turn memory check */ }
}
async function settled(timeoutMs = 900_000) {
  const conversation = frame('conversation')
  const answers = conversation.locator('article[data-role="assistant"]')
  await waitUntil(async () => {
    // A gate that opens after send()'s wait is continued here (M3CL2).
    const gate = frame('gate').getByTestId('memory-gate-continue')
    if (await gate.isVisible().catch(() => false)) await gate.click().catch(() => {})
    return await answers.count() > state.answersBefore &&
      !/Working…|Streaming|Sending/i.test(await conversation.locator('.transcript').innerText()) &&
      await conversation.getByTestId('composer').isEnabled() &&
      await conversation.getByTestId('composer').inputValue() === ''
  }, timeoutMs, 500)
}
const lastAnswer = async (n = 600) => ((await frame('conversation').locator('article[data-role="assistant"]').last().innerText()) ?? '').slice(0, n)
const archive = async id => post(`/v1/threads/${id}/archive`).catch(e => String(e))
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const names = ['page', 'context', 'frame', 'shot', 'clip', 'sleep', 'setPage', 'api', 'post', 'waitUntil', 'state', 'openApp', 'newThread', 'send', 'settled', 'lastAnswer', 'archive', 'BASE']
http.createServer(async (req, res) => {
  let body = ''; for await (const c of req) body += c
  let out
  try {
    const fn = new AsyncFunction(...names, body)
    const r = await fn(page, context, frame, shot, clip, sleep, p => { page = p; hook(p) }, api, post, waitUntil, state, openApp, newThread, send, settled, lastAnswer, archive, BASE)
    out = { ok: true, result: r }
  } catch (e) { out = { ok: false, error: String(e?.stack ?? e).slice(0, 3000) } }
  res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(out, null, 1))
}).listen(Number(process.env.DRIVER_PORT ?? 9657), '127.0.0.1', () => console.log('driver ready'))
