/* 공유 카드 HTML 을 PNG 로 (virtueme_core/share.py 가 부른다)
     node share_render.mjs <card.html> <출력 폴더> '[["#선택자","파일.png"], ...]' [card|book] [배율]
   card: 카드 HTML 이 글자 크기를 맞춘 뒤 window.__cardDone 을 켠다. book: 책 HTML(book.html)이 쪽을 다 앉힌 뒤 __bookDone.
   playwright 는 virme/ 에 설치돼 있다. 결과 요약(JSON, 요소마다 화면에 보이는 글자 포함)을 표준 출력으로 낸다. */
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const virme = new URL('../../package.json', import.meta.url)
const { chromium } = createRequire(virme)('playwright')

const [html, outDir, shotsArg, kind = 'card', scaleArg = '1'] = process.argv.slice(2)
const shots = JSON.parse(shotsArg)
const browser = await chromium.launch()
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 2200 }, deviceScaleFactor: Number(scaleArg) || 1 })
  await page.goto(pathToFileURL(path.resolve(html)).href, { waitUntil: 'load' })
  await page.evaluate(() => document.fonts.ready)
  const flag = kind === 'book' ? '__bookDone' : '__cardDone'
  await page.waitForFunction((f) => window[f] === true, flag, { timeout: 120000 })
  await page.evaluate(() => document.fonts.ready)
  const report = (await page.evaluate(() => window.__cardReport || {})) || {}
  report.fonts = await page.evaluate(() =>
    Array.from(document.fonts).filter((f) => f.status === 'loaded').map((f) => `${f.family} ${f.weight}`))
  report.shots = []
  report.text = {}
  for (const [sel, name] of shots) {
    const el = await page.$(sel)
    if (!el) continue
    await el.screenshot({ path: path.join(outDir, name) })
    report.shots.push(name)
    report.text[name] = await el.evaluate((n) => n.innerText)
  }
  console.log(JSON.stringify(report))
} finally {
  await browser.close()
}
