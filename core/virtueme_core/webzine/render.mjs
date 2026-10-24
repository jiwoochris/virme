/* 웹진 HTML 을 쪽별 PNG 와 A4 PDF 로 (virtueme_core/webzine/issue.py 가 부른다)
     node render.mjs <issue.html> <출력 폴더> '[["#pg-cover","pg-cover.png"], ...]'
   playwright 는 virme/ 에 설치돼 있다. 결과 요약(JSON)을 표준 출력으로 낸다. */
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const virme = new URL('../../../package.json', import.meta.url)
const { chromium } = createRequire(virme)('playwright')

const [html, outDir, shotsArg] = process.argv.slice(2)
const shots = JSON.parse(shotsArg || '[]')
const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 1400 }, deviceScaleFactor: 2 })
  await page.goto(pathToFileURL(path.resolve(html)).href, { waitUntil: 'load' })
  await page.waitForFunction(() => window.__wzDone === true, null, { timeout: 60000 })
  const report = { shots: [] }
  report.fonts = await page.evaluate(() =>
    Array.from(document.fonts).filter((f) => f.status === 'loaded').map((f) => f.family))
  for (const [sel, name] of shots) {
    const el = await page.$(sel)
    if (!el) continue
    await el.screenshot({ path: path.join(outDir, name) })
    report.shots.push(name)
  }
  report.overflow = await page.evaluate(() => Array.from(document.querySelectorAll('.page')).map((p) => ({
    id: p.id, height: Math.round(p.scrollHeight), mm: Math.round(p.scrollHeight / 3.7795) })))
  await page.emulateMedia({ media: 'print' })
  await page.pdf({ path: path.join(outDir, 'issue.pdf'), width: '210mm', height: '297mm', printBackground: true,
    margin: { top: 0, bottom: 0, left: 0, right: 0 } })
  console.log(JSON.stringify(report))
} finally {
  await browser.close()
}
