/**
 * How a storyboard row's payload is cut from what the take captured (decision 0043). The
 * recorder uses it to show the payload, and cues.mjs to check it against the saved capture:
 * one function, so the two can't drift.
 *
 * A payload spec: { from, turn?, tool?, pick?, limit?, fields? }
 * - from: "event" | "trace" | "inspect" | "introspect:guard"
 * - tool: for "trace", the first tool step with that name (its `result`, the text the model
 *   saw, is parsed when it is JSON)
 * - pick: a dotted path; limit: the first N items of an array; fields: keep only these keys
 *   of each object (or of the object itself)
 */

export function pick(value, path) {
  if (!path) return value
  for (const part of path.split('.')) {
    if (value == null) throw new Error(`payload path "${path}" not found`)
    value = value[/^\d+$/.test(part) ? Number(part) : part]
  }
  if (value === undefined) throw new Error(`payload path "${path}" not found`)
  return value
}

/** A trace's tool step, with its result parsed when the model saw JSON. */
export function toolStep(trace, name) {
  const step = (trace?.steps ?? []).find((s) => s.kind === 'tool' && s.name === name)
  if (!step) throw new Error(`no ${name} step in trace ${trace?.id}`)
  let result = step.result
  try {
    result = JSON.parse(step.result)
  } catch {
    // plain text: shown as it is
  }
  return { ...step, result }
}

function only(value, fields) {
  if (!fields) return value
  const keep = (o) => Object.fromEntries(fields.filter((f) => o && f in o).map((f) => [f, o[f]]))
  return Array.isArray(value) ? value.map(keep) : keep(value)
}

/** The payload `spec` cut from `source` (an event's data, a trace, an inspector answer). */
export function cut(source, spec) {
  let value = spec.tool ? toolStep(source, spec.tool) : source
  value = pick(value, spec.pick)
  if (spec.limit && Array.isArray(value)) value = value.slice(0, spec.limit)
  return only(value, spec.fields)
}

// A Windows or home path on screen fails the take (acceptance check: no C:\MyData in frames).
// Checked on string values, not JSON text: there a newline after "directory:" reads "y:\n".
export const PATH_RE = /(?<![A-Za-z])[A-Za-z]:\\[^\s\\]|\\Users\\|\/home\/|MyData/
export const hasLocalPath = (value) =>
  typeof value === 'string'
    ? PATH_RE.test(value)
    : value !== null && typeof value === 'object'
      ? Object.values(value).some(hasLocalPath)
      : false
