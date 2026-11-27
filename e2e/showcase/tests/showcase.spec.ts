import { spawnSync } from 'node:child_process'
import { rmSync } from 'node:fs'
import { join } from 'node:path'

import { expect, type Page, test } from '@playwright/test'

/**
 * The README's GIF and screenshots (Phase 9, decision 0042), on harness.py: the real app
 * and tools on the agent suite's scripted model. `npm run showcase` writes the screenshots
 * to docs/showcase/img/; `npm run showcase:gif` writes docs/showcase/demo.gif (needs ffmpeg
 * on the PATH; without it the WebM is kept). Not part of CI.
 */

const DOCS = join('..', '..', 'docs', 'showcase')
const IMG = join(DOCS, 'img')
const SIZE = { width: 1280, height: 800 }

const HYBRID = "Do CH-2004's Q3 sales match what the Q3 meeting notes say?"

/** The portfolio banner stays out of the README images. */
async function openPortal(page: Page, path = '/intranet') {
  await page.goto(path)
  const hide = page.getByRole('button', { name: /Hide the guide|Ocultar la guía/ })
  if (await hide.isVisible().catch(() => false)) await hide.click()
  // The "Demo guide" pill that replaces it, too.
  await page.addStyleTag({ content: '[aria-label="Demo guide"],[aria-label="Guía de la demo"]{display:none!important}' })
}

async function ask(page: Page, question: string, typeDelay = 0) {
  const box = page.getByPlaceholder(/Type your question|Escribe tu pregunta/)
  await box.click()
  if (typeDelay) await box.pressSequentially(question, { delay: typeDelay })
  else await box.fill(question)
  await page.waitForTimeout(typeDelay ? 400 : 0)
  await box.press('Enter')
}

/** Waits for the saved answer (the footer with the inspector link appears with it). */
async function answered(page: Page) {
  await expect(page.getByText(/Inspect the run|Ver el recorrido/).last()).toBeVisible()
  await page.waitForTimeout(600)
}

/**
 * The cited passage is the whole meeting-notes chunk: scroll its panel to the line that
 * holds the number and mark that line (a highlight for the reader; the text is unchanged).
 */
async function markLine(page: Page, start: string) {
  const passage = page.getByText(start, { exact: false }).last()
  await passage.evaluate((el, start) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent ?? ''
      const at = text.indexOf(start)
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
  }, start)
  await page.waitForTimeout(500)
}

/** The inspector: the open step's result (the rows the model saw) at the top of the view. */
async function showResult(page: Page) {
  await page
    .getByText(/Result, as the model saw it/i)
    .evaluate((el) => el.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  await page.waitForTimeout(800)
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: join(IMG, `${name}.png`) })
}

/**
 * WebM → GIF from `startS` to `endS` seconds, with the portfolio's README spec: 10 fps, 960 px
 * wide, a 96-colour palette of its own.
 */
function toGif(webm: string, gif: string, startS: number, endS: number) {
  const filters =
    'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];' +
    '[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle'
  const run = spawnSync(
    'ffmpeg',
    ['-y', '-loglevel', 'error', '-ss', startS.toFixed(2), '-to', endS.toFixed(2), '-i', webm, '-vf', filters, '-loop', '0', gif],
    { timeout: 120_000, encoding: 'utf8' },
  )
  if (run.error || run.status !== 0) {
    console.warn(`showcase: no GIF (${run.error?.message ?? run.stderr.trim()}); the WebM stays at ${webm}`)
    return false
  }
  return true
}

/**
 * The README GIF: the portal → Luna full screen → the hybrid question → the steps → the cited
 * answer → the passage behind [1] → the agent inspector with the sales_report rows open.
 */
test('recorded demo for the README GIF @gif', async ({ browser, baseURL }) => {
  test.setTimeout(180_000)
  const context = await browser.newContext({ baseURL, viewport: SIZE, recordVideo: { dir: join(DOCS, '.gif-video'), size: SIZE } })
  const page = await context.newPage()
  const t0 = Date.now()
  await openPortal(page)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.waitForTimeout(800)
  const startS = (Date.now() - t0) / 1000 - 0.2
  await page.waitForTimeout(1200)

  // Luna, full screen.
  await page.getByRole('link', { name: 'Ask Luna', exact: true }).first().click()
  await expect(page.getByPlaceholder('Type your question…')).toBeVisible()
  await page.waitForTimeout(800)

  // The hybrid question: the steps arrive, then the answer with its citation.
  await ask(page, HYBRID, 45)
  await answered(page)
  await page.waitForTimeout(2200)

  // The passage behind [1], scrolled to the line that holds the number.
  await page.getByRole('button', { name: '1', exact: true }).first().click()
  await markLine(page, 'Best-selling SKU: CH-2004')
  await page.waitForTimeout(3000)

  // The agent inspector: the run rebuilt from its trace, the sales_report rows open.
  const href = (await page.getByRole('link', { name: /Inspect the run/ }).last().getAttribute('href'))!
  await page.goto(href)
  await expect(page.getByRole('heading', { name: /Agent run #/ })).toBeVisible()
  await expect(page.getByText('All systems OK')).toBeVisible()
  await page.waitForTimeout(2800)
  const step = page.getByRole('button', { name: /sales_report.*Reading the sales records/ })
  await step.hover()
  await page.waitForTimeout(500)
  await step.click()
  await page.waitForTimeout(900)
  await showResult(page)
  await page.waitForTimeout(4500)
  const endS = (Date.now() - t0) / 1000

  const video = page.video()!
  await context.close()
  const webm = join(DOCS, 'demo.webm')
  await video.saveAs(webm)
  rmSync(join(DOCS, '.gif-video'), { recursive: true, force: true })
  if (toGif(webm, join(DOCS, 'demo.gif'), Math.max(0, startS), endS)) rmSync(webm)
})

test('the hybrid answer and its run in the agent inspector', async ({ page }) => {
  await openPortal(page, '/intranet/luna')
  await ask(page, HYBRID)
  await answered(page)
  await page.getByRole('button', { name: '1', exact: true }).first().click()
  await markLine(page, 'Best-selling SKU: CH-2004')
  await shot(page, '50-hybrid-answer')

  const href = (await page.getByRole('link', { name: /Inspect the run/ }).last().getAttribute('href'))!
  await page.goto(href)
  await page.getByRole('button', { name: /sales_report.*Reading the sales records/ }).click()
  await showResult(page)
  await page.waitForTimeout(2500)
  await shot(page, '51-agent-inspector')
})

test('a chart and the data behind it', async ({ page }) => {
  await openPortal(page, '/intranet/luna')
  await ask(page, 'Show me a bar chart of Q3 2026 revenue by month.')
  await answered(page)
  await page.getByRole('button', { name: /Show the data/ }).first().click()
  await page.waitForTimeout(1200)
  await shot(page, '52-chart-data')
})

test('an exchange rate with external calls off', async ({ page }) => {
  await openPortal(page, '/intranet/luna')
  await ask(page, "What is today's USD to MXN exchange rate?")
  await answered(page)
  await shot(page, '53-fx-rate')
})

test('a request to change data, refused', async ({ page }) => {
  await openPortal(page, '/intranet/luna')
  await ask(page, 'Delete the orders table.')
  await answered(page)
  await shot(page, '54-refusal')
})

test('Luna in Spanish', async ({ page }) => {
  await openPortal(page, '/intranet/luna')
  await page.getByRole('button', { name: /^es$/i }).click()
  await ask(page, '¿Cuántas piezas de CH-2004 hay en inventario?')
  await answered(page)
  await shot(page, '55-spanish')
})
