import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/stylegan/health', (route) => route.fulfill({
    json: { status: 'error', detail: 'offline UI fixture' },
  }))
})

async function openVisual(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
}

test('selects webzine, novel, visual, and the analysis inside one page', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.experience-nav button')).toHaveCount(4)
  await expect(page.locator('main')).toHaveAttribute('data-experience-mode', 'webzine')
  // core 서버가 켜져 있으면 목록과 만들기 버튼, 꺼져 있으면 켜는 방법이 보인다
  await expect(page.getByTestId('webzine-mode').locator('.webzine, .webzine-empty').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /TALK/ })).toHaveCount(0)

  await page.getByRole('button', { name: /NOVEL 노벨/ }).click()
  await expect(page.getByTestId('novel-mode')).toBeVisible()
  await expect(page.frameLocator('iframe[title="Storyseed 노벨 모드"]').getByRole('heading', { name: 'Storyseed' })).toBeVisible()

  await page.getByRole('button', { name: /DATA 분석/ }).click()
  await expect(page.locator('main')).toHaveAttribute('data-experience-mode', 'insight')
  // core 서버가 켜져 있으면 차트, 꺼져 있으면 안내가 보인다
  await expect(page.getByTestId('insight-mode').locator('.insight, .insight-empty').first()).toBeVisible()

  await openVisual(page)
  await expect(page.locator('main')).toHaveAttribute('data-experience-mode', 'visual')
  await expect(page.getByTestId('visual-mode')).toBeVisible()
})

test('shares one narrative object across the header and the visual mode', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('narrative-chip')).toHaveCount(0)
  await page.evaluate(() => {
    window.postMessage({
      type: 'virtueme:narrative',
      narrative: {
        schema: 'virtueme.narrative/1', narrative_id: 'e2e0001', keyword: '러닝', evidence_grade: '강한 근거',
        grade_basis: { strong_records: 9, strong_days: 6, active_days: 80 }, data_scope: { records: 700, date_range: null },
        signals: { stability: 0.6, novelty: 0.3, conflict: 0.2, uncertainty: 0.4, possibility: 0.5, activity: 0.7, confidence: 0.8 },
      },
    }, window.location.origin)
  })
  await expect(page.locator('.live-status')).toContainText('러닝 · 강한 근거 · #e2e0001')
  await openVisual(page)
  await expect(page.getByTestId('narrative-chip')).toContainText('#e2e0001')
})

test('retains Storyseed interactions and state while switching modes', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /NOVEL 노벨/ }).click()
  const novel = page.frameLocator('iframe[title="Storyseed 노벨 모드"]')
  await novel.getByPlaceholder(/아무 단어나 적어보세요/).fill('혼자')
  await novel.getByRole('button', { name: 'Start' }).click()
  await expect(novel.locator('.stitle')).toHaveText('키가 필요합니다')
  await expect(novel.getByText('OpenRouter API 키가 있어야 동작합니다')).toBeVisible()

  await page.getByRole('button', { name: /WEBZINE 웹진/ }).click()
  await expect(page.getByTestId('webzine-mode')).toBeVisible()

  await page.getByRole('button', { name: /NOVEL 노벨/ }).click()
  await expect(novel.getByPlaceholder(/아무 단어나 적어보세요/)).toHaveValue('혼자')
  await expect(novel.locator('.stitle')).toHaveText('키가 필요합니다')
})

test('keeps all four experience choices available on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const choices = page.locator('.experience-nav button')
  await expect(choices).toHaveCount(4)
  await expect(choices.first()).toBeInViewport()
  await expect(choices.last()).toBeInViewport()
  await expect(page.getByTestId('webzine-mode')).toBeVisible()
  await page.getByRole('button', { name: /NOVEL 노벨/ }).click()
  await expect(page.getByTestId('novel-mode')).toBeVisible()
})

test('offers an off-by-default portrait toggle', async ({ page }) => {
  await page.goto('/')
  await openVisual(page)
  const toggle = page.getByTestId('portrait-toggle')

  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByTestId('central-portrait')).toHaveCount(0)
  await expect(page.getByTestId('portrait-anchor')).toHaveCount(0)
  await expect(page.getByTestId('portrait-stage')).toHaveAttribute('data-example-count', '1')
  await expect(page.locator('.mode-button')).toHaveCount(3)
  await expect(page.locator('.state-nav h1')).toContainText('장소의 잠재 장면')

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('central-portrait')).toBeVisible()
  await expect(page.getByTestId('central-portrait')).toHaveAttribute('src', '/assets/central-portrait.png')
})

test('switches between place, object, and situation latent-loop videos', async ({ page }) => {
  await page.goto('/')
  await openVisual(page)
  await expect(page.getByText('LUCID SONIC DREAMS · REAL STYLEGAN LATENT ROUTE')).toBeVisible()
  await expect(page.locator('.control-panel')).toHaveCount(0)
  await expect(page.locator('.workspace')).toHaveClass(/single-example/)
  await expect(page.getByTestId('stylegan-layer')).toHaveAttribute('data-renderer', 'stylegan2-ada')
  await expect(page.getByTestId('stylegan-layer')).toHaveAttribute('data-render-mode', 'latent-structure')
  await expect(page.getByTestId('stylegan-layer')).toHaveAttribute('data-display-mode', 'latent-loop-video')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-source', 'stylegan-latent-loop')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-effects', 'none')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-frame-count', '144')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('loop', '')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-mode', 'place')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('src', '/assets/latent-landscape-loop.mp4')
  await expect(page.locator('.state-caption p')).toHaveText('BUILDING FIXED')

  await page.getByRole('button', { name: /사물/ }).click()
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-mode', 'object')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('src', '/assets/latent-object-loop.mp4')
  await expect(page.locator('.state-caption p')).toHaveText('MULTI-CLASS OBJECTS')

  await page.getByRole('button', { name: /상황/ }).click()
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('data-mode', 'situation')
  await expect(page.getByTestId('latent-loop-video')).toHaveAttribute('src', '/assets/latent-situation-loop.mp4')
  await expect(page.locator('.state-caption p')).toHaveText('MULTI-CLASS SCENES')
  await expect(page.locator('.latent-depth-surface')).toHaveCount(0)
  await expect(page.locator('.model-frame-field')).toHaveCount(0)
  await expect(page.locator('.stylegan-shards')).toHaveCount(0)
  await expect(page.locator('.stylegan-frame.refracted')).toHaveCount(0)
})

test('keeps the selected mode stable when personal data events arrive', async ({ page }) => {
  await page.goto('/')
  await openVisual(page)
  await page.getByRole('button', { name: /사물/ }).click()
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent('virtueme:personal-data', {
      detail: { stability: 0.1, novelty: 0.2, conflict: 0.96, uncertainty: 0.3, possibility: 0.4, activity: 0.72, confidence: 0.91 },
    }))
  })
  await expect(page.locator('.mode-button')).toHaveCount(3)
  await expect(page.locator('.mode-button.active')).toContainText('OBJECT')
})

test('uses a continuous latent flow without waveform, orbit, or voxel graphics', async ({ page }) => {
  await page.goto('/')
  await openVisual(page)
  await expect(page.locator('.orbit')).toHaveCount(0)
  await expect(page.locator('.stage-grid')).toHaveCount(0)
  await expect(page.locator('.portrait-ghost')).toHaveCount(0)
  await expect(page.locator('.model-frame-field')).toHaveCount(0)
  await expect(page.getByTestId('stylegan-layer')).toHaveAttribute('data-renderer', 'stylegan2-ada')
  await expect(page.getByTestId('central-portrait')).toHaveCount(0)
})

test('explains how to get record music before a keyword is planted', async ({ page }) => {
  await page.goto('/')
  await openVisual(page)
  await expect(page.locator('.music-panel')).toContainText('노벨 모드에서 키워드를 심으면')
  await expect(page.getByTestId('stylegan-layer')).toHaveAttribute('data-effects', 'none')
})
