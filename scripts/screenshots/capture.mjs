// Playwright capture runner for marketing screenshots (adapted from
// LakeEriePartners/stream scripts/screenshots).
//
//   node scripts/screenshots/capture.mjs                # capture all
//   node scripts/screenshots/capture.mjs feed-phone     # one shot
//   TRAILCAM_APP=http://localhost:8100 node ...         # default
//   SHOTS_HEADLESS=0 node ...                           # show browser
//
// PNGs land in /tmp/trailcam-shots/png/<id>.png so they never pollute the
// worktree. Auth is the dev-only /auth/screenshot-login route (needs the
// backend running with TRAILCAM_ENV=dev TRAILCAM_SCREENSHOT_LOGIN=1).

import fs from 'node:fs/promises'
import path from 'node:path'

import { chromium } from '@playwright/test'

const APP = process.env.TRAILCAM_APP || 'http://localhost:8100'
const OUT_ROOT = process.env.SHOTS_OUT || '/tmp/trailcam-shots/png'
const HEADLESS = process.env.SHOTS_HEADLESS !== '0'

const DESKTOP = { width: 1440, height: 900 }
const PHONE = { width: 393, height: 852 }

/** Wait until every visible photo tile <img> has actually painted pixels. */
async function tilesLoaded(page) {
  await page.waitForSelector('.tile-img')
  await page.waitForFunction(() =>
    [...document.querySelectorAll('img.tile-img')].every(
      (i) => i.complete && i.naturalWidth > 0
    )
  )
}

const SHOTS = [
  {
    id: 'feed-desktop',
    route: '/',
    viewport: DESKTOP,
    setup: tilesLoaded,
  },
  {
    id: 'feed-phone',
    route: '/',
    viewport: PHONE,
    setup: tilesLoaded,
  },
  {
    id: 'photo-detail',
    route: '/',
    viewport: DESKTOP,
    setup: async (page) => {
      await tilesLoaded(page)
      await page.locator('.tile').first().click()
      await page.waitForSelector('.detail-overlay img')
      await page.waitForFunction(() => {
        const img = document.querySelector('.detail-overlay img')
        return img && img.complete && img.naturalWidth > 0
      })
    },
  },
  {
    id: 'photo-detail-phone',
    route: '/',
    viewport: PHONE,
    setup: async (page) => {
      await tilesLoaded(page)
      await page.locator('.tile').first().click()
      await page.waitForSelector('.detail-overlay img')
      await page.waitForFunction(() => {
        const img = document.querySelector('.detail-overlay img')
        return img && img.complete && img.naturalWidth > 0
      })
    },
  },
  {
    id: 'nodes',
    route: '/nodes',
    viewport: DESKTOP,
    setup: async (page) => {
      await page.waitForSelector('.node-card')
    },
  },
  {
    id: 'mesh',
    route: '/nodes',
    viewport: DESKTOP,
    setup: async (page) => {
      // live mesh traffic panel with one row expanded — a chunked transfer if
      // one is in flight (fresh seeds are thumbnail-only), else an announce
      await page.waitForSelector('.mesh-row')
      await page.locator('.mesh-feed').scrollIntoViewIfNeeded()
      const chunk = page.locator('.mesh-has-packet .mesh-line', { hasText: 'full-res chunk' })
      const row = (await chunk.count())
        ? chunk.first()
        : page.locator('.mesh-has-packet .mesh-line').first()
      await row.click()
      await page.waitForSelector('.mesh-packet')
    },
  },
  {
    id: 'node-detail',
    route: '/nodes',
    viewport: DESKTOP,
    setup: async (page) => {
      await page.waitForSelector('.node-card')
      await page.getByRole('button', { name: /Food Plot/ }).click()
      // three charts: battery / radio / temp
      await page.waitForFunction(
        () => document.querySelectorAll('.node-detail-panel .chart-svg').length >= 3
      )
    },
  },
  {
    id: 'node-detail-gateway',
    route: '/nodes',
    viewport: DESKTOP,
    setup: async (page) => {
      await page.waitForSelector('.node-card')
      await page.getByRole('button', { name: /Cabin Gateway/ }).click()
      // gateways chart WiFi signal only — battery/temp are hidden (mains power)
      await page.waitForFunction(
        () => document.querySelectorAll('.node-detail-panel .chart-svg').length === 1
      )
    },
  },
]

const filter = process.argv.slice(2)
const selected = filter.length ? SHOTS.filter((s) => filter.includes(s.id)) : SHOTS
if (!selected.length) {
  console.error(
    `no shots matched ${JSON.stringify(filter)}. known: ${SHOTS.map((s) => s.id).join(', ')}`
  )
  process.exit(2)
}

async function captureOne(browser, shot) {
  const context = await browser.newContext({
    viewport: shot.viewport,
    deviceScaleFactor: 2,
    baseURL: APP,
  })
  try {
    const login = await context.request.get('/auth/screenshot-login')
    if (!login.ok()) {
      throw new Error(
        `screenshot-login returned ${login.status()} — backend up with TRAILCAM_ENV=dev TRAILCAM_SCREENSHOT_LOGIN=1?`
      )
    }
    const page = await context.newPage()
    page.on('pageerror', (err) => console.error(`  pageerror[${shot.id}]:`, err.message))
    await page.goto(shot.route, { waitUntil: 'load', timeout: 30000 })
    if (shot.setup) await shot.setup(page)
    // settle: image decode + font paint
    await page.waitForTimeout(400)
    const outPath = path.join(OUT_ROOT, `${shot.id}.png`)
    await fs.mkdir(path.dirname(outPath), { recursive: true })
    await page.screenshot({ path: outPath })
    console.log(`  ✓ ${shot.id} → ${outPath}`)
  } finally {
    await context.close()
  }
}

const browser = await chromium.launch({ headless: HEADLESS })
console.log(`capturing ${selected.length} shot(s) against ${APP} → ${OUT_ROOT}`)
let failed = 0
for (const shot of selected) {
  try {
    await captureOne(browser, shot)
  } catch (err) {
    failed += 1
    console.error(`  ✗ ${shot.id}: ${err.message}`)
  }
}
await browser.close()
if (failed) process.exit(1)
