// The stage's own behaviour: no app logic, no data of its own. record.spec.ts calls
// window.stage.* with cues built from the take's capture (decision 0043, ported from
// hl-bead-studio).
;(() => {
  const $ = (id) => document.getElementById(id)
  const LAYERS = ['Browser', 'API', 'Agent', 'Ollama', 'Registry', 'Tools', 'SQLite', 'Search', 'Check']
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

  function pipeline(active) {
    $('pipeline').innerHTML = LAYERS.map(
      (l, i) => `${i ? '<i>›</i>' : ''}<span class="${l === active ? 'on' : ''}">${esc(l)}</span>`,
    ).join('')
  }

  /** The app frame at `path` (same origin), resolved once it has loaded. */
  function loadApp(path = '/intranet') {
    return new Promise((resolve) => {
      const app = $('app')
      app.addEventListener('load', () => resolve(), { once: true })
      app.src = path
    })
  }

  function init({ banner, provider }) {
    $('banner').textContent = banner
    $('provider').textContent = provider
    pipeline(null)
  }

  /** One storyboard row: subtitle, layer, callouts (file:line + excerpt), payload JSON. */
  function show(cue) {
    $('subs').textContent = cue.subtitle || ''
    $('row-id').textContent = cue.id ? `row ${cue.id}` : ''
    if (cue.layer) pipeline(cue.layer)
    $('callouts').innerHTML = (cue.callouts || [])
      .slice(0, cue.payload ? 2 : 3)
      .map((c) => `<div class="callout"><div class="where">${esc(c.file)}:${c.line} · ${esc(c.symbol)}</div><pre>${esc(c.excerpt)}</pre></div>`)
      .join('')
    const box = $('payload')
    if (cue.payload) {
      box.classList.remove('empty')
      $('payload-label').textContent = cue.payload.label
      $('payload-note').textContent = cue.payload.note || ''
      $('payload-json').textContent = JSON.stringify(cue.payload.value, null, 2)
    } else {
      box.classList.add('empty')
    }
    if (cue.slide) slide(cue.slide, cue.slideData)
    else hideSlide()
  }

  const SLIDES = {
    hook: () => `
      <h1>Luna</h1>
      <p class="lead">A local agent for a small company. For each question she decides where the answer lives: the documents, the business database, an exchange rate, or both. Everything runs on this laptop, on Ollama.</p>
      <div class="boxes">
        <div class="box"><b>Documents</b><span>Hybrid search (vectors + SQLite FTS5, fused with RRF), cited passages.</span></div>
        <div class="box hot"><b>Business data</b><span>Typed tools over a read-only SQLite database. Numbers come from here.</span></div>
        <div class="box"><b>FX rate</b><span>The one external API, off by default; the stored rate otherwise.</span></div>
        <div class="box"><b>Charts</b><span>A chart spec stored in the trace, drawn by the browser.</span></div>
      </div>`,
    end: (d) => `
      <h1>Numbers come from tools, never from the model's memory.</h1>
      <p class="lead" style="margin-bottom:34px">Every answer can be rebuilt from its trace.</p>
      <p class="lead">${esc(d.repo || '')}</p>
      ${d.author ? `<p class="lead" style="margin-top:34px">${esc(d.author)}</p>` : ''}
      ${d.linkedin ? `<p class="lead">${esc(d.linkedin)}</p>` : ''}`,
  }

  function slide(name, data = {}) {
    $('slide-inner').innerHTML = SLIDES[name](data)
    $('slide').classList.add('on')
  }

  function hideSlide() {
    $('slide').classList.remove('on')
  }

  /** The app frame's text, without what the recorder masks: for the no-paths check. */
  function appText() {
    const doc = $('app').contentDocument
    if (!doc) return ''
    const clone = doc.body.cloneNode(true)
    clone.querySelectorAll('[data-video-masked]').forEach((n) => n.remove())
    return clone.innerText || clone.textContent || ''
  }

  window.stage = { init, loadApp, show, slide, hideSlide, appText }
})()
