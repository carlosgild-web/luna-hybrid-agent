/**
 * After a take: shown rows -> cues.json (start/end per subtitle), and the checks that make
 * the video trustworthy (decision 0043, ported from hl-bead-studio):
 *   - flexible mode (a real model): every row shown is a storyboard row or a generic one,
 *     and the rows that never matched are listed; strict mode: every row, once, in order;
 *   - every payload on screen equals the capture it names (events.json, trace-<turn>.json,
 *     inspect-<turn>.json, or a fresh run of introspect.py), cut the same way (payload.mjs);
 *   - the ranking the retrieval inspector shows is the order the take's search returned;
 *   - every run used a model the storyboard allows, and no payload shows a local path.
 *
 *   node cues.mjs out/ollama            build cues.json and check
 *   node cues.mjs out/ollama --verify   check only
 */
import { execFileSync } from 'node:child_process'
import { isDeepStrictEqual } from 'node:util'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cut, hasLocalPath, toolStep } from './payload.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const PYTHON =
  process.env.VIDEO_PYTHON ||
  join(ROOT, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const TAIL_MS = 1500 // the last subtitle stays this long after its hold

const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'))

export function buildCues(shown) {
  return shown.map((s, i) => ({
    id: s.id,
    start_ms: s.t_ms,
    end_ms: i + 1 < shown.length ? shown[i + 1].t_ms : s.t_ms + s.hold_ms + TAIL_MS,
    subtitle: s.subtitle,
  }))
}

function verify(dir, storyboard, shown, take) {
  const problems = []
  const notes = []
  const expected = storyboard.rows.map((r) => r.id)
  const got = shown.map((s) => s.id)
  if ((take.mode ?? 'flexible') === 'flexible') {
    const unknown = got.filter((id) => !expected.includes(id) && !/^g\d+$/.test(id))
    if (unknown.length) problems.push(`rows not in the storyboard: ${unknown.join(', ')}`)
    const missing = expected.filter((id) => !got.includes(id))
    if (missing.length) notes.push(`never matched (not in the video): ${missing.join(', ')}`)
    const generic = got.filter((id) => /^g\d+$/.test(id))
    if (generic.length) notes.push(`generic rows for unforeseen events: ${generic.length}`)
  } else if (!isDeepStrictEqual(got, expected)) {
    problems.push(`rows: expected ${expected.length} in storyboard order, got ${got.length}`)
  }
  if (take.failure) problems.push(`the take failed: ${take.failure}`)

  const events = readJson(join(dir, 'events.json'))
  const file = (name) => (existsSync(join(dir, name)) ? readJson(join(dir, name)) : null)
  let checked = 0
  for (const s of shown) {
    if (!s.source) continue
    const { spec } = s.source
    let truth
    try {
      if (spec.from === 'event') truth = cut(events[s.source.index]?.data, spec)
      else if (spec.from === 'trace') truth = cut(file(`trace-${s.source.turn}.json`), spec)
      else if (spec.from === 'inspect') truth = cut(file(`inspect-${s.source.turn}.json`), spec)
      else if (spec.from === 'introspect:guard')
        truth = JSON.parse(execFileSync(PYTHON, [join(HERE, 'introspect.py'), 'guard', s.source.query], { encoding: 'utf8' }))
    } catch (err) {
      problems.push(`${s.id}: ${err.message}`)
      continue
    }
    checked++
    if (!isDeepStrictEqual(s.payload, truth)) problems.push(`${s.id}: the payload on screen differs from the captured ${spec.from}`)
    if (hasLocalPath(s.payload)) problems.push(`${s.id}: the payload shows a local path`)
  }

  // The retrieval inspector's ranking for a turn's search must be the order the take's
  // search_documents returned (the chunk ids in the trace), or the panel explains another search.
  for (const turn of take.inspected ?? []) {
    const inspect = file(`inspect-${turn}.json`)
    const passages = toolStep(file(`trace-${turn}.json`), 'search_documents').meta?.passages ?? []
    const returned = passages.map((p) => p.chunk_id)
    const ranked = (inspect?.chunks ?? []).filter((c) => c.sent).map((c) => c.chunk_id).slice(0, returned.length)
    if (!returned.length || !isDeepStrictEqual(ranked, returned))
      problems.push(`${turn}: the inspector ranks ${ranked} but the search returned ${returned}`)
  }

  // Every run used a model the storyboard allows (the banner names it).
  if (take.models_allowed) {
    const odd = (take.models ?? []).filter((m) => !take.models_allowed.includes(m))
    if (odd.length) problems.push(`runs used ${odd.join(', ')}, not ${take.models_allowed.join(', ')}`)
  }
  return { problems, checked, notes }
}

const [dirArg, flag] = process.argv.slice(2)
if (!dirArg) {
  console.error('usage: node cues.mjs out/<video> [--verify]')
  process.exit(2)
}
const dir = resolve(HERE, dirArg)
const take = existsSync(join(dir, 'take.json')) ? readJson(join(dir, 'take.json')) : {}
const storyboard = readJson(join(HERE, take.storyboard ?? 'storyboard-ollama.json'))
const shown = readJson(join(dir, 'shown.json'))
if (flag !== '--verify') {
  writeFileSync(join(dir, 'cues.json'), JSON.stringify(buildCues(shown), null, 2))
  console.log(`cues.json: ${shown.length} cues`)
}
const { problems, checked, notes } = verify(dir, storyboard, shown, take)
for (const note of notes) console.log(`note: ${note}`)
if (problems.length) {
  console.error(problems.map((p) => `FAIL ${p}`).join('\n'))
  process.exit(1)
}
console.log(`ok: ${shown.length} rows shown; ${checked} payloads equal the capture; the search ranking matches; no local paths`)
