/* 시연 영상 녹화: 브라우저에서 노벨 → 대화 → 인사이트 → 비주얼을 실제로 돌려 영상으로 남긴다.
   이야기와 대화 답은 진짜 모델을 부르므로 한 번에 3~4분 걸리고, 그동안 만들어지는 과정이 그대로 찍힌다.

   준비: core 서버(8767)와 웹 앱(5173)을 먼저 켜고, 발표용 키워드는 `virtueme demo` 로 음악·영상을 만들어 둔다.

     export OPENROUTER_API_KEY=sk-or-v1-...
     node scripts/record-demo.mjs 러닝 "처음 뛴 날 기억나?"

   결과: generated/demo/<날짜>.webm (소리 없음. 음악은 오디오 반응 영상 파일로 따로 보여 준다).
   키는 환경 변수로만 받아 브라우저 localStorage 에 넣고, 파일에는 남기지 않는다. */
import { mkdir, readdir, rename } from 'node:fs/promises'
import { chromium } from 'playwright'

const KEY = process.env.OPENROUTER_API_KEY
const APP = process.env.DEMO_APP || 'http://127.0.0.1:5173/'
const OUT = process.env.DEMO_OUT || 'generated/demo'
const [keyword = '러닝', question = '처음 뛴 날 기억나?'] = process.argv.slice(2)
if (!KEY) {
  console.error('OPENROUTER_API_KEY 가 없습니다. 이야기·대화를 실제로 만들어야 하므로 키가 필요합니다.')
  process.exit(2)
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  await mkdir(OUT, { recursive: true })
  // 지난 녹화본이 남아 있으면 이름순 정렬로는 그것을 집어 버린다. 이번에 새로 생긴 파일만 고른다.
  const before = new Set((await readdir(OUT)).filter((f) => f.endsWith('.webm')))
  const browser = await chromium.launch()
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
    locale: 'ko-KR',
  })
  await ctx.addInitScript((k) => { try { localStorage.setItem('seedv2_or_key', k) } catch (e) {} }, KEY)
  const page = await ctx.newPage()
  const mode = (name) => page.locator('header nav button', { hasText: name }).click()

  await page.goto(APP)
  await page.waitForLoadState('networkidle')
  await wait(2500)

  // 1) 노벨: 내 기록으로 키워드를 심고 이야기가 자라는 동안 옆 패널의 근거 기록이 보인다
  const novel = page.frameLocator('iframe[title="Storyseed 노벨 모드"]')
  await novel.locator('#srcBtns button[data-s="core"]').click()
  await wait(2500)
  await novel.locator('#seed').fill(keyword)
  await wait(800)
  await novel.locator('#grow').click()
  await novel.locator('.vx, .vx-blur').first().waitFor({ timeout: 180000 })
  await wait(2500)
  await novel.locator('.vx').first().click()          // 금색 값을 누르면 인용한 기록이 뜬다
  await wait(4000)
  await novel.locator('#paper').click({ position: { x: 5, y: 5 } })
  await novel.locator('#paper').evaluate((el) => el.scrollIntoView({ block: 'end', behavior: 'smooth' }))
  await wait(3500)

  // 2) 대화: 같은 기록으로 묻고, 답 속 값이 기록과 맞는지 검사한다
  await mode('TALK')
  await wait(1500)
  const talk = page.frameLocator('iframe[title="Storyseed 채팅 모드"]')
  await talk.locator('#srcBtns button[data-s="core"]').click()
  await wait(2500)
  await talk.locator('#ipt').fill(question)
  await wait(800)
  await talk.locator('#sendBtn').click()
  await talk.locator('#msgs .vx, #msgs .vx-blur').first().waitFor({ timeout: 120000 })
  await wait(5000)

  // 3) 인사이트: 정서 곡선·주제·의미 지도·관계 그래프·간극
  await mode('INSIGHT')
  await wait(4000)
  for (let i = 0; i < 5; i++) {
    await page.mouse.wheel(0, 520)
    await wait(2200)
  }

  // 4) 비주얼: 기록의 음악을 틀면 잠재 루프가 소리에 맞춰 움직이고, 매핑 설명에 계획과 측정값이 나온다
  await mode('VISUAL')
  await wait(2500)
  const audio = page.locator('[data-testid="music-audio"]')
  if (await audio.count()) {
    await audio.evaluate((el) => { el.currentTime = 0; el.play() })
    await wait(12000)
    await page.locator('.music-link').click()
    await wait(6000)
    await audio.evaluate((el) => el.pause())
    const place = page.locator('.music-videos button', { hasText: '장소' })
    if (await place.count()) {
      await place.click()
      await wait(14000)
    }
  } else {
    await wait(8000)
  }

  await ctx.close()
  await browser.close()
  const made = (await readdir(OUT)).filter((f) => f.endsWith('.webm') && !before.has(f))
  if (made.length !== 1) throw new Error(`새로 만들어진 녹화 파일을 하나로 특정하지 못했습니다: ${made.join(', ') || '없음'}`)
  const from = `${OUT}/${made[0]}`
  const day = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10)
  const to = `${OUT}/virtueme-demo-${day}-${keyword}.webm`
  await rename(from, to)
  console.log(to)
}

await main()
