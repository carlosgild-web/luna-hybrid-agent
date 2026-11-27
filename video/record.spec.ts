/**
 * The README video take (decision 0043, ported from hl-bead-studio). Drives the running app
 * inside the 1920x1080 stage and shows each storyboard row when its captured event arrives.
 *
 *   npm run video:smoke      ~10 s: the stage layout at 1920x1080 (any running app)
 *   npm run video:demo-dry   the whole take on the scripted model (e2e/showcase/harness.py,
 *                            BASE_URL=http://127.0.0.1:8020): a dry run
 *   npm run video:ollama     the take on Ollama (start.cmd video, port 8010)
 *
 * Capture: an init script wraps the app's fetch. The chat request and every SSE event of
 * POST /api/agent/chat go to Node with their time, and are handed to the app only when Node
 * returns: that is how a subtitle stays on screen while the app shows that step. The app is
 * not changed and the events are not changed; only their delivery to the browser is paced.
 * After each turn the take reads the stored trace (GET /api/agent/traces/{id}) and, for a
 * turn that searched the documents, asks the retrieval inspector (POST /api/retrieve/inspect,
 * read only) how that search ranked its passages.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { resolve as resolveLine, ROOT } from './lines.mjs'
import { cut, hasLocalPath, PATH_RE } from './payload.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const STAGE = { width: 1920, height: 1080 }
const TYPE_DELAY_MS = 40
// A model turn on a laptop CPU: two or three model calls of 2-5 min each (OLLAMA_TIMEOUT_S=900).
const TURN_TIMEOUT_MS = 30 * 60_000
// Generic rows (tool calls the storyboard did not foresee) shown at most; the others are
// listed in take.json as skipped, so the video stays near its length.
const MAX_GENERIC = Number(process.env.VIDEO_MAX_GENERIC ?? 2)
const PYTHON =
  process.env.VIDEO_PYTHON ||
  join(ROOT, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

type PayloadSpec = { from: string; turn?: string; tool?: string; pick?: string; limit?: number; fields?: string[] }
type Storyboard = {
  video: string
  provider: string
  /** The models the runs may use, id -> banner name; null: any (the dry run). */
  models?: Record<string, string> | null
  banner: string
  providers?: Record<string, { banner: string; models: Record<string, string> | null }>
  mode?: 'flexible'
  hold?: number
  sentences: Record<string, string>
  guard_query: string
  rows: Row[]
}
type Row = {
  id: string
  scene: number
  subtitle: string
  turn?: string
  on: { step?: string; event?: string; name?: string; calls?: string }
  payload?: PayloadSpec
  callouts?: string[][]
  slide?: string
  layer?: string
}
type Captured = { type: string; data: any; t_ms: number; turn: string | null; index: number }

const BOARD_FILE = 'storyboard-ollama.json'
const loadBoard = (): Storyboard => JSON.parse(readFileSync(join(HERE, BOARD_FILE), 'utf8'))
const CHAT_TYPES = new Set(['request', 'session', 'llm', 'tool', 'answer', 'done', 'error'])

// ---------------------------------------------------------------- capture (all frames)

const CAPTURE = String.raw`(() => {
  if (location.pathname.startsWith('/__video')) return
  // The portfolio banner starts folded, and its "Demo guide" pill stays out of the video.
  try { localStorage.setItem('hl-demo-guide', 'pill') } catch {}
  const hide = () => {
    for (const b of document.querySelectorAll('button')) {
      const text = b.textContent.trim()
      if ((text === 'Demo guide' || text === 'Guía de la demo') && b.style.display !== 'none') b.style.display = 'none'
    }
  }
  new MutationObserver(hide).observe(document, { childList: true, subtree: true })
  const send = (event) => window.__videoEvent(event)
  const parse = (block) => {
    let type = 'message'; const data = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) type = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (!data.length) return null
    try {
      const parsed = JSON.parse(data.join('\n'))
      // A step is a model call or a tool call: two kinds of event for the storyboard.
      return { type: type === 'step' ? parsed.kind : type, data: parsed }
    } catch { return null }
  }
  const original = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url
    const method = (init.method || 'GET').toUpperCase()
    if (method === 'POST' && url.startsWith('/api/agent/chat')) {
      await send({ type: 'request', data: JSON.parse(init.body) })
      const response = await original(input, init)
      if (!response.ok || !response.body) return response
      const reader = response.body.getReader()
      const decoder = new TextDecoder(), encoder = new TextEncoder()
      const body = new ReadableStream({
        async start(controller) {
          let buffer = ''
          for (;;) {
            const { value, done } = await reader.read()
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, '\n')
            let end
            while ((end = buffer.indexOf('\n\n')) !== -1) {
              const block = buffer.slice(0, end)
              buffer = buffer.slice(end + 2)
              const event = parse(block)
              if (event) await send(event)
              controller.enqueue(encoder.encode(block + '\n\n'))
            }
            if (done) break
          }
          const rest = parse(buffer.trim())
          if (rest) { await send(rest); controller.enqueue(encoder.encode(buffer.trim() + '\n\n')) }
          controller.close()
        },
      })
      return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
    }
    return original(input, init)
  }
})()`

// ---------------------------------------------------------------- the conductor

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// A subtitle stays at least this long per character, whatever the storyboard's hold factor
// (hl-bead-studio F13).
const READ_MS_PER_CHAR = 1000 / 17

function holdMs(row: Row, factor = 1) {
  const chars = (row.subtitle || '').length
  const paced = (Math.min(Math.max(1400 + chars * 48, 2800), 8500) + (row.payload ? 1800 : 0)) * factor
  return Math.round(Math.max(paced, chars * READ_MS_PER_CHAR))
}

/**
 * Flexible mode (a real model): each row is shown at most once, on the first event that
 * matches it; an unforeseen tool call or a failed tool gets a generic row built from the
 * event; a row that never matched is reported, not failed.
 */
class Conductor {
  rows: Row[]
  shownIds = new Set<string>()
  generic = 0
  skippedGeneric: string[] = []
  t0 = 0
  turn: string | null = null
  events: Captured[] = []
  shown: Array<{ id: string; t_ms: number; hold_ms: number; subtitle: string; payload?: unknown; source?: unknown }> = []
  waits: Array<{ from_ms: number; to_ms: number; what: string }> = []
  traces: Record<string, any> = {}
  inspects: Record<string, any> = {}
  failure: Error | null = null
  readonly failed: Promise<never>
  private reject: (err: Error) => void = () => {}
  private chain: Promise<void> = Promise.resolve()
  private turnDone: Promise<void> = Promise.resolve()
  private finishTurn: () => void = () => {}
  private pendingTrace: Promise<void> = Promise.resolve()

  constructor(
    private page: Page,
    private api: APIRequestContext,
    private outDir: string,
    readonly board: Storyboard,
  ) {
    this.failed = new Promise<never>((_, reject) => (this.reject = reject))
    this.failed.catch(() => {}) // observed through until()
    let turn: string | undefined
    this.rows = board.rows.map((row) => {
      const typed = row.on.step?.startsWith('typed:') ? row.on.step.slice(6) : undefined
      turn = typed ?? turn
      return { ...row, turn: row.turn ?? (row.on.event && CHAT_TYPES.has(row.on.event) ? turn : undefined) }
    })
  }

  setFailure(err: Error) {
    if (!this.failure) {
      this.failure = err
      this.reject(err)
    }
  }

  private fail(message: string): never {
    this.setFailure(new Error(message))
    throw this.failure
  }

  /** `p`, or the take's first failure, or a timeout. */
  async until<T>(p: Promise<T>, ms = TURN_TIMEOUT_MS, what = 'the app'): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${what}: nothing after ${ms / 1000} s`)), ms)
    })
    try {
      return await Promise.race([p, this.failed, timeout])
    } finally {
      clearTimeout(timer)
    }
  }

  check() {
    if (this.failure) throw this.failure
  }

  now() {
    return Date.now() - this.t0
  }

  beginTurn(key: string) {
    this.turn = key
    this.turnDone = new Promise((r) => (this.finishTurn = r))
  }

  /** The turn's done event has been handled, its trace saved, and its rows shown. */
  async waitTurn() {
    await this.turnDone
    await this.pendingTrace
    await this.chain
    this.check()
  }

  async idle() {
    await this.chain
    this.check()
  }

  private matches(row: Row, ev: Captured) {
    if (!row.on.event || row.on.event !== ev.type) return false
    if (row.turn && row.turn !== ev.turn) return false
    const d = ev.data ?? {}
    if (row.on.name && d.name !== row.on.name) return false
    if (row.on.calls && !(d.tool_calls ?? []).some((c: any) => c.name === row.on.calls)) return false
    return true
  }

  private payloadFor(row: Row, ev?: Captured) {
    const spec = row.payload
    if (!spec) return undefined
    let value: any
    let source: any
    let label: string
    if (spec.from === 'event') {
      if (!ev) this.fail(`row ${row.id}: payload from an event, but no event triggered it`)
      value = cut(ev.data, spec)
      source = { spec, index: ev.index }
      label = ev.type === 'llm' ? 'SSE step: model call' : ev.type === 'tool' ? 'SSE step: tool call' : `SSE event: ${ev.type}`
    } else if (spec.from === 'trace') {
      const trace = this.traces[spec.turn!] ?? this.fail(`row ${row.id}: no trace for turn "${spec.turn}"`)
      value = cut(trace, spec)
      source = { spec, turn: spec.turn }
      label = `GET /api/agent/traces/${trace.id}`
    } else if (spec.from === 'inspect') {
      const inspect = this.inspects[spec.turn!] ?? this.fail(`row ${row.id}: no retrieval inspection for turn "${spec.turn}"`)
      value = cut(inspect, spec)
      source = { spec, turn: spec.turn }
      label = 'POST /api/retrieve/inspect'
    } else if (spec.from === 'introspect:guard') {
      const query = this.board.guard_query
      value = JSON.parse(execFileSync(PYTHON, [join(HERE, 'introspect.py'), 'guard', query], { encoding: 'utf8' }))
      source = { spec, query }
      label = 'video/introspect.py guard (sql_guard.check_select)'
    } else {
      this.fail(`row ${row.id}: unknown payload source "${spec.from}"`)
    }
    if (hasLocalPath(value)) this.fail(`row ${row.id}: the payload shows a local path`)
    const note = [spec.tool, spec.pick].filter(Boolean).join('.') || 'captured'
    return { label: label!, note, value, source }
  }

  private async show(row: Row, ev?: Captured) {
    this.shownIds.add(row.id)
    const callouts = (row.callouts ?? []).map(([file, symbol]) => resolveLine(file, symbol))
    const payload = this.payloadFor(row, ev)
    const slideData = row.slide === 'end' ? this.endCard() : undefined
    const cue = { id: row.id, subtitle: row.subtitle, layer: row.layer, callouts, payload, slide: row.slide, slideData }
    await this.page.evaluate((c) => (window as any).stage.show(c), cue)
    const hold = holdMs(row, this.board.hold)
    this.shown.push({ id: row.id, t_ms: this.now(), hold_ms: hold, subtitle: row.subtitle, payload: payload?.value, source: payload?.source })
    await sleep(hold)
  }

  private endCard() {
    return {
      repo: process.env.VIDEO_REPO_URL || 'github.com/carlosgild-web/luna-hybrid-agent',
      author: process.env.VIDEO_AUTHOR ?? 'Carlos Velazquez',
      linkedin: process.env.VIDEO_LINKEDIN ?? 'linkedin.com/in/carlos-v-90b99149',
    }
  }

  /** Called by the page (exposeBinding) for every captured event; returning releases it. */
  async onEvent(raw: { type: string; data: any }) {
    const ev: Captured = { ...raw, t_ms: this.now(), turn: CHAT_TYPES.has(raw.type) ? this.turn : null, index: this.events.length }
    this.events.push(ev)
    try {
      await this.chain
      if (this.failure) return
      const row = this.rows.find((r) => !this.shownIds.has(r.id) && this.matches(r, ev))
      let rows = row ? [row] : []
      if (!rows.length) {
        const generic = this.genericRow(ev)
        if (generic && this.generic > MAX_GENERIC) this.skippedGeneric.push(`${ev.type} ${ev.data?.name ?? ''}`.trim())
        else if (generic) rows = [generic]
      }
      if (ev.type === 'done') this.onDone(ev)
      if (ev.type === 'error') this.finishTurn()
      this.chain = (async () => {
        await sleep(250) // the app renders the event first
        for (const r of rows) await this.show(r, ev)
      })().catch((err) => this.setFailure(err))
    } catch (err) {
      this.setFailure(err as Error)
    }
  }

  /** A row for what the storyboard did not foresee: the event says what happened. */
  private genericRow(ev: Captured): Row | null {
    const d = ev.data ?? {}
    const id = () => `g${++this.generic}`
    if (ev.type === 'tool') {
      const known = this.rows.some((r) => r.turn === ev.turn && r.on.event === 'tool' && r.on.name === d.name)
      if (known && d.ok !== false) return null
      if (d.ok === false)
        return { id: id(), scene: 0, subtitle: `${d.name} refused these arguments. The model sees why, and may try again.`, layer: 'Registry', on: { event: 'tool' }, payload: { from: 'event', pick: 'arguments' } }
      return { id: id(), scene: 0, subtitle: `The model also called ${d.name}. The registry checked the arguments; the tool did the work.`, layer: 'Tools', on: { event: 'tool' }, payload: { from: 'event', pick: 'arguments' } }
    }
    if (ev.type === 'error') {
      return { id: id(), scene: 0, subtitle: `The turn ended with an error: ${String(d.detail ?? '').slice(0, 70)}`, layer: 'Agent', on: { event: 'error' }, payload: { from: 'event' } }
    }
    return null
  }

  /** The run's stored trace and, if it searched the documents, how that search ranked. */
  private onDone(ev: Captured) {
    const turn = this.turn!
    this.pendingTrace = (async () => {
      const response = await this.api.get(`/api/agent/traces/${ev.data.trace_id}`)
      expect(response.ok(), `GET /api/agent/traces/${ev.data.trace_id}`).toBe(true)
      const trace = await response.json()
      this.traces[turn] = trace
      writeFileSync(join(this.outDir, `trace-${turn}.json`), JSON.stringify(trace, null, 2))
      const search = trace.steps.find((s: any) => s.kind === 'tool' && s.name === 'search_documents' && s.ok)
      if (search) {
        const inspect = await this.inspect(search.arguments)
        this.inspects[turn] = inspect
        this.events.push({ type: 'inspect', data: { turn }, t_ms: this.now(), turn: null, index: this.events.length })
        writeFileSync(join(this.outDir, `inspect-${turn}.json`), JSON.stringify(inspect, null, 2))
      }
      this.finishTurn()
    })().catch((err) => {
      this.setFailure(err)
      this.finishTurn()
    })
  }

  /** The retrieval inspector on the search's own query and shelf (read only, same code). */
  private async inspect(args: { query: string; shelf?: string | null }) {
    let body: Record<string, unknown> = { question: args.query, scope: 'portal', include_prompt: false }
    if (args.shelf) {
      const shelves = await this.api.get('/api/collections').then((r) => r.json())
      const key = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
      const shelf = shelves.find((c: any) => key(c.name) === key(args.shelf!))
      if (shelf) body = { ...body, scope: 'shelf', collection_id: shelf.id }
    }
    const response = await this.api.post('/api/retrieve/inspect', { data: body })
    expect(response.ok(), 'POST /api/retrieve/inspect').toBe(true)
    return response.json()
  }

  /** The spec reached `name`: show its row, if the storyboard has one. */
  async step(name: string) {
    await this.idle()
    const row = this.rows.find((r) => r.on.step === name && !this.shownIds.has(r.id))
    if (row) await this.show(row)
  }

  /** Rows that never matched: reported in take.json, not failed. */
  missing() {
    return this.rows.filter((r) => !this.shownIds.has(r.id)).map((r) => r.id)
  }
}

// ---------------------------------------------------------------- shared setup

async function guard(api: APIRequestContext, dry: boolean) {
  const hint = dry
    ? 'Start the scripted backend: cd e2e/showcase && ../../backend/.venv/Scripts/python harness.py, and set BASE_URL=http://127.0.0.1:8020.'
    : 'Start the app with `start.cmd video` (port 8010), then run this again.'
  let demo: any
  try {
    demo = await api.get('/api/demo', { timeout: 5_000 }).then((r) => r.json())
  } catch {
    throw new Error(`The app is not running at ${test.info().project.use.baseURL}. ${hint}`)
  }
  if (!demo.documents) throw new Error(`The app has no documents: the demo company isn't loaded. ${hint}`)
}

async function openStage(browser: import('@playwright/test').Browser, baseURL: string, outDir: string, onEvent?: (e: any) => Promise<void>) {
  const context = await browser.newContext({ baseURL, viewport: STAGE, recordVideo: { dir: join(outDir, 'raw'), size: STAGE } })
  await context.route('**/__video/*', (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop()!
    if (!['stage.html', 'stage.js'].includes(name)) return route.fulfill({ status: 404 })
    return route.fulfill({ path: join(HERE, name), contentType: name.endsWith('.js') ? 'text/javascript' : 'text/html' })
  })
  await context.addInitScript(CAPTURE)
  await context.exposeBinding('__videoEvent', async (_source, event) => (onEvent ? onEvent(event) : undefined))
  const t0 = Date.now()
  const page = await context.newPage()
  await page.goto('/__video/stage.html')
  const app = page.frameLocator('#app')
  return { context, page, app, t0 }
}

async function loadApp(page: Page, path: string) {
  await page.evaluate((p) => (window as any).stage.loadApp(p), path)
}

/**
 * The page paints: an animation frame within 5 s. On the 7.2 GB laptop Edge stalled under
 * memory pressure while the model ran (findings F9, F11); the video keeps time by repeating
 * the last frame. Wait up to 3 min for it to paint again and log the stall as a wait, which
 * compose.mjs cuts to 1 s with its real length in the label. Longer than that fails the take.
 */
async function waitPainting(page: Page, conductor: Conductor, where: string) {
  const frame = () =>
    page.evaluate(
      () => new Promise<boolean>((r) => { const t = setTimeout(() => r(false), 5000); requestAnimationFrame(() => { clearTimeout(t); r(true) }) }),
    )
  if (await frame()) return
  const from = conductor.now() - 5000
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    if (await frame()) {
      conductor.waits.push({ from_ms: from, to_ms: conductor.now(), what: 'the browser catching up (low memory)' })
      console.warn(`${where}: the browser stalled for ${Math.round((conductor.now() - from) / 1000)} s`)
      return
    }
  }
  throw new Error(`${where}: the browser stopped painting for over 3 min (out of memory?)`)
}

async function assertNoPaths(page: Page, where: string) {
  const text: string = await page.evaluate(() => (window as any).stage.appText())
  const hit = text.match(PATH_RE)
  if (hit) throw new Error(`${where}: a local path is visible in the app ("${hit[0]}")`)
}

/** What cues.mjs and compose.mjs read: the capture, the rows shown, the waits, the video. */
async function saveTake(conductor: Conductor, page: Page, context: import('@playwright/test').BrowserContext, outDir: string, extra: Record<string, unknown>) {
  writeFileSync(join(outDir, 'events.json'), JSON.stringify(conductor.events, null, 2))
  writeFileSync(join(outDir, 'shown.json'), JSON.stringify(conductor.shown, null, 2))
  writeFileSync(join(outDir, 'waits.json'), JSON.stringify(conductor.waits, null, 2))
  const take = {
    storyboard: BOARD_FILE,
    mode: conductor.board.mode ?? 'flexible',
    provider: conductor.board.provider,
    models: Object.values(conductor.traces).map((t: any) => t.model),
    models_allowed: conductor.board.models ? Object.keys(conductor.board.models) : null,
    inspected: Object.keys(conductor.inspects),
    missing: conductor.missing(),
    generic_skipped: conductor.skippedGeneric,
    failure: conductor.failure?.message ?? null,
    // Two passes (decision 0043): the folder of pass 1's runs that the app replayed.
    replay_of: process.env.VIDEO_REPLAY_DIR ?? null,
    ...extra,
  }
  writeFileSync(join(outDir, 'take.json'), JSON.stringify(take, null, 2))
  const video = page.video()
  await context.close()
  if (video) {
    await video.saveAs(join(outDir, 'take.webm.part'))
    renameSync(join(outDir, 'take.webm.part'), join(outDir, 'take.webm'))
  }
  rmSync(join(outDir, 'raw'), { recursive: true, force: true })
}

// ---------------------------------------------------------------- the takes

test('smoke: the stage at 1920x1080 @smoke', async ({ browser, baseURL, request }) => {
  const outDir = join(HERE, 'out', 'smoke')
  mkdirSync(outDir, { recursive: true })
  const board = loadBoard()
  const demo = await request.get('/api/demo').then((r) => r.json())
  const { context, page } = await openStage(browser, baseURL!, outDir)
  await page.evaluate((b) => (window as any).stage.init(b), { banner: 'Smoke test: the stage layout', provider: `${demo.documents} documents` })
  await loadApp(page, '/intranet/luna')
  const row = board.rows.find((r) => r.id === '2.2')!
  const callouts = row.callouts!.map(([f, s]) => resolveLine(f, s))
  await page.evaluate((c) => (window as any).stage.show(c), {
    id: row.id, subtitle: row.subtitle, layer: row.layer, callouts,
    payload: { label: 'GET /api/demo', note: 'captured', value: demo },
  })
  const box = async (sel: string) => page.locator(sel).boundingBox()
  expect(await box('#app')).toEqual({ x: 0, y: 0, width: 1280, height: 960 })
  expect(await box('#panel')).toEqual({ x: 1280, y: 0, width: 640, height: 960 })
  expect(await box('#subs')).toEqual({ x: 0, y: 960, width: 1920, height: 120 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > 1920 || document.documentElement.scrollHeight > 1080)
  expect(overflow, 'the stage scrolls').toBe(false)
  const pipelineFits = await page.evaluate(() => {
    const p = document.getElementById('pipeline')!
    return p.scrollWidth <= p.clientWidth
  })
  expect(pipelineFits, 'the pipeline fits the panel').toBe(true)
  await sleep(4_000)
  await page.screenshot({ path: join(outDir, 'stage.png') })
  await page.evaluate(() => (window as any).stage.slide('hook'))
  await sleep(3_000)
  await page.screenshot({ path: join(outDir, 'hook.png') })
  const video = page.video()
  await context.close()
  await video?.saveAs(join(outDir, 'smoke.webm'))
  rmSync(join(outDir, 'raw'), { recursive: true, force: true })
})

/**
 * The take, on Ollama (the published video) or on the scripted model (a dry run of the same
 * steps, against e2e/showcase/harness.py). Five chat turns, then the agent inspector.
 */
for (const [provider, tag] of [['ollama', '@ollama'], ['scripted', '@demo-dry']] as const)
  test(`${provider} take ${tag}`, async ({ browser, baseURL, request }) => {
    test.setTimeout(90 * 60_000)
    const dry = provider === 'scripted'
    const base = loadBoard()
    const board: Storyboard = { ...base, provider, ...(dry ? base.providers?.scripted ?? {} : {}) }
    const outDir = join(HERE, 'out', dry ? 'demo-dry' : 'ollama')
    rmSync(outDir, { recursive: true, force: true })
    mkdirSync(outDir, { recursive: true })
    await guard(request, dry)
    const S = board.sentences

    let conductor!: Conductor
    const { context, page, app, t0 } = await openStage(browser, baseURL!, outDir, (e) => conductor.onEvent(e))
    conductor = new Conductor(page, context.request, outDir, board)
    conductor.t0 = t0
    const until = <T>(p: Promise<T>, ms?: number, what?: string) => conductor.until(p, ms, what)
    let posterMs: number | null = null

    const box = app.getByPlaceholder('Type your question…')
    page.on('crash', () => conductor.setFailure(new Error('the page crashed (out of memory?)')))
    /** One chat turn in a new conversation, so each answer is alone on screen. */
    const ask = async (key: string, fresh = true) => {
      await waitPainting(page, conductor, `before "${key}"`)
      if (fresh) {
        await app.getByRole('button', { name: 'New conversation' }).click()
        await sleep(600)
      }
      await box.click()
      conductor.beginTurn(key)
      // The row comes up as the typing starts, so the last scene's subtitle doesn't linger.
      const typedRow = conductor.step(`typed:${key}`)
      await box.pressSequentially(S[key], { delay: TYPE_DELAY_MS })
      await typedRow
      await box.press('Enter')
      await until(conductor.waitTurn(), TURN_TIMEOUT_MS, `turn "${key}"`)
      await waitPainting(page, conductor, `after "${key}"`)
      const error = [...conductor.events].reverse().find((e) => e.turn === key && e.type === 'error')
      if (error) throw new Error(`turn "${key}" ended with an error: ${error.data?.detail}`)
      const model = conductor.traces[key]?.model
      if (board.models && !(model in board.models)) throw new Error(`the run used model "${model}", not one of the storyboard's models`)
      await expect(app.getByText('Inspect the run').last()).toBeVisible()
      await sleep(900)
    }

    try {
      const models = Object.entries(board.models ?? {})
      await page.evaluate((b) => (window as any).stage.init(b), {
        banner: models.length ? board.banner.replace('{model}', models[0][1]) : board.banner,
        provider: 'everything on this laptop',
      })
      await conductor.step('hook')
      await loadApp(page, '/intranet/luna')
      await expect(box).toBeVisible()
      await page.evaluate(() => (window as any).stage.hideSlide())
      await sleep(800)

      // Scene 1: a database question
      await ask('restock', false)
      await conductor.step('rows:restock')
      await assertNoPaths(page, 'restock')

      // Scene 2: one answer, two sources, and the passage behind [1]
      await ask('hybrid')
      await conductor.step('rrf:hybrid')
      const cite = app.getByRole('button', { name: '1', exact: true }).first()
      await cite.hover()
      await sleep(400)
      await cite.click()
      await app.getByText('Best-selling SKU: CH-2004', { exact: false }).last().evaluate((el) => {
        // Scroll the passage to the line with the number and mark it (the text is unchanged).
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = node.textContent ?? ''
          const at = text.indexOf('Best-selling SKU: CH-2004')
          if (at < 0) continue
          const end = text.indexOf('\n', at)
          const range = document.createRange()
          range.setStart(node, at)
          range.setEnd(node, end < 0 ? text.length : end)
          const mark = document.createElement('mark')
          mark.style.cssText = 'background: rgba(250, 204, 21, .45); color: inherit; border-radius: 3px; padding: 0 2px'
          range.surroundContents(mark)
          mark.scrollIntoView({ block: 'center' })
          return
        }
      }).catch(() => {}) // a real model may cite another passage: no mark then
      await sleep(600)
      posterMs = conductor.now()
      await conductor.step('cite')
      await assertNoPaths(page, 'hybrid')

      // Scene 3: a chart and the data behind it
      await ask('chart')
      const showData = app.getByRole('button', { name: /Show the data/ }).first()
      await showData.hover()
      await sleep(400)
      await showData.click()
      await sleep(600)
      await conductor.step('show_data')

      // Scene 4: the exchange rate, with external calls off
      await ask('fx')
      await conductor.step('rows:fx')

      // Scene 5: a request to change data
      await ask('hostile')
      await conductor.step('guard')
      await assertNoPaths(page, 'hostile')

      // Scene 6: the hybrid run in the agent inspector
      const traceId = conductor.traces.hybrid?.id
      if (traceId == null) throw new Error('no trace for the hybrid question')
      await loadApp(page, `/agent/${traceId}`)
      await expect(app.getByRole('heading', { name: /Agent run #/ })).toBeVisible()
      // The console's health bar warns that qwen3 wouldn't fit in memory now: true in pass 2,
      // where the model isn't loaded, and not what this scene is about. Its own close button.
      // The bar comes with the health check, a moment after the page.
      const hideWarning = app.getByTitle('Hide until the next visit')
      if (await hideWarning.waitFor({ timeout: 10_000 }).then(() => true, () => false)) {
        await sleep(800)
        await hideWarning.click()
      }
      await sleep(1200)
      await conductor.step('inspector')
      const step = app.getByRole('button', { name: /sales_report.*Reading the sales records/ }).first()
      await step.hover()
      await sleep(400)
      await step.click()
      await sleep(700)
      await app.getByText(/Result, as the model saw it/i).first().evaluate((el) => el.scrollIntoView({ block: 'start', behavior: 'smooth' }))
      await sleep(900)
      await conductor.step('inspector_rows')
      await assertNoPaths(page, 'inspector')

      await conductor.step('end')
      await sleep(1000)
      conductor.check()
      const missing = conductor.missing()
      if (missing.length) console.warn(`rows that never matched (kept out of the video): ${missing.join(', ')}`)
    } finally {
      await saveTake(conductor, page, context, outDir, { poster_ms: posterMs })
    }
  })
