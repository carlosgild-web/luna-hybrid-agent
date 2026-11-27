import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { defineConfig } from '@playwright/test'

/**
 * The README GIF and screenshots (decision 0042): `npm run showcase` and `npm run showcase:gif`.
 * One backend, harness.py: the real app and the built web app on a scripted model. Edge, the
 * browser already on Windows, so nothing is downloaded. Not part of CI.
 */
const PORT = Number(process.env.E2E_PORT ?? 8020)
const venvPython = join(
  '..',
  '..',
  'backend',
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
)
const python = process.env.E2E_PYTHON ?? (existsSync(venvPython) ? venvPython : 'python')

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: 'msedge',
    locale: 'en-US',
    viewport: { width: 1280, height: 800 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `"${python}" harness.py`,
    env: { E2E_PORT: String(PORT) },
    url: `http://127.0.0.1:${PORT}/api/demo`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
})
