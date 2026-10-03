/* 책 HTML 을 PNG·PDF 로 (virtueme_core/book/render.py 가 부른다)
     node render.mjs <book.html> <출력 폴더>
   playwright 는 virme/ 에 설치돼 있다. 결과 요약(JSON)을 표준 출력으로 낸다. */
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const virme = new URL('../../../package.json', import.meta.url)
const { chromium } = createRequire(virme)('playwright')

const [html, outDir] = process.argv.slice(2)
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2 })
await page.goto(pathToFileURL(path.resolve(html)).href, { waitUntil: 'load' })
await page.evaluate(() => document.fonts.ready)
await page.waitForFunction(() => window.__bookDone === true, null, { timeout: 60000 })
await page.evaluate(() => document.fonts.ready)
const report = await page.evaluate(() => window.__bookReport)
report.fonts = await page.evaluate(() =>
  Array.from(document.fonts).map((f) => ({ family: f.family, weight: f.weight, status: f.status })))
for (const [sel, name] of [['#cover', 'cover.png'], ['#opener', 'opener.png'], ['#spread', 'spread.png']]) {
  const el = await page.$(sel)
  if (el) await el.screenshot({ path: path.join(outDir, name) })
}
await page.emulateMedia({ media: 'print' })
await page.pdf({ path: path.join(outDir, 'book.pdf'), width: '152mm', height: '225mm', printBackground: true,
  preferCSSPageSize: true, margin: { top: 0, bottom: 0, left: 0, right: 0 } })
await browser.close()
console.log(JSON.stringify(report))
