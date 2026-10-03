import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  // These UI checks must also work without the optional local model service.
  await page.route('**/api/stylegan/health', (route) => route.fulfill({
    json: { status: 'error', detail: 'offline UI fixture' },
  }))
  await page.goto('/')
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
})

test('remembers both portrait ON and OFF across refresh', async ({ page }) => {
  const toggle = page.getByTestId('portrait-toggle')
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await toggle.click()
  await page.reload()
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('central-portrait')).toBeVisible()
  await toggle.click()
  await page.reload()
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByTestId('central-portrait')).toHaveCount(0)
})

test('still toggles when browser storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      get() { throw new DOMException('Storage blocked', 'SecurityError') },
    })
  })
  await page.reload()
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
  await page.getByTestId('portrait-toggle').click()
  await expect(page.getByTestId('central-portrait')).toBeVisible()
})

test('reports an unavailable video instead of downloading a blank PNG', async ({ page }) => {
  await page.route('**/*.mp4', (route) => route.abort())
  await page.reload()
  await page.getByRole('button', { name: /VISUAL 비주얼/ }).click()
  const downloads: string[] = []
  page.on('download', (download) => downloads.push(download.suggestedFilename()))
  await page.getByRole('button', { name: '화면 PNG 저장' }).click()
  await expect(page.getByRole('status')).toContainText('영상이 나타나면 다시 저장')
  await expect(page.getByRole('button', { name: '화면 PNG 저장' })).toBeEnabled()
  expect(downloads).toEqual([])
})

test.describe('mobile PNG', () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
  test('saves at display density after scrolling to the controls', async ({ page }, testInfo) => {
    const video = page.getByTestId('latent-loop-video')
    await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.readyState)).toBeGreaterThanOrEqual(2)
    const box = await page.getByTestId('portrait-stage').boundingBox()
    const pending = page.waitForEvent('download')
    await page.getByRole('button', { name: '화면 PNG 저장' }).click()
    const download = await pending
    const output = testInfo.outputPath('mobile-2x.png')
    await download.saveAs(output)
    const png = await readFile(output)
    expect(png.readUInt32BE(16)).toBe(Math.round(box!.width * 2))
    expect(png.readUInt32BE(20)).toBe(Math.round(box!.height * 2))
    expect(png.length).toBeGreaterThan(10_000)
    await testInfo.attach('mobile-2x', { path: output, contentType: 'image/png' })
  })
})

for (const mode of ['place', 'object', 'situation'] as const) {
  test(`downloads the displayed ${mode} frame, with and without portrait`, async ({ page }, testInfo) => {
    if (mode !== 'place') await page.locator('.mode-button').filter({ hasText: mode.toUpperCase() }).click()
    const video = page.getByTestId('latent-loop-video')
    await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.readyState)).toBeGreaterThanOrEqual(2)
    await video.evaluate((el: HTMLVideoElement) => el.pause())
    const stage = page.getByTestId('portrait-stage')
    const box = await stage.boundingBox()
    expect(box).not.toBeNull()
    const saved: Buffer[] = []
    for (const portrait of ['off', 'on']) {
      if (portrait === 'on') {
        await page.getByTestId('portrait-toggle').click()
        await page.getByTestId('central-portrait').evaluate((el: HTMLImageElement) => el.decode())
      }
      const pending = page.waitForEvent('download')
      await page.getByRole('button', { name: '화면 PNG 저장' }).click()
      const download = await pending
      expect(download.suggestedFilename()).toMatch(new RegExp(`^virtueme-${mode}-portrait-${portrait}-.*\\.png$`))
      const output = testInfo.outputPath(`${mode}-${portrait}.png`)
      await download.saveAs(output)
      expect(await download.failure()).toBeNull()
      const png = await readFile(output)
      saved.push(png)
      expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
      expect(png.readUInt32BE(16)).toBe(Math.round(box!.width))
      expect(png.readUInt32BE(20)).toBe(Math.round(box!.height))
      expect(png.length).toBeGreaterThan(10_000)
      await testInfo.attach(`${mode}-${portrait}`, { path: output, contentType: 'image/png' })

      // Top-right is free of overlays. Compare exported pixels against the
      // actual decoded video, including the displayed object-fit: cover crop.
      const difference = await video.evaluate(async (el: HTMLVideoElement, bytes: number[]) => {
        const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }))
        const actual = document.createElement('canvas')
        actual.width = bitmap.width
        actual.height = bitmap.height
        const ctx = actual.getContext('2d')!
        ctx.drawImage(bitmap, 0, 0)
        bitmap.close()
        const x = Math.floor(actual.width * 0.72)
        const y = Math.floor(actual.height * 0.18)
        const pixels = ctx.getImageData(x, y, 24, 24).data
        const scale = Math.max(actual.width / el.videoWidth, actual.height / el.videoHeight)
        const w = el.videoWidth * scale
        const h = el.videoHeight * scale
        ctx.drawImage(el, (actual.width - w) / 2, (actual.height - h) / 2, w, h)
        const expected = ctx.getImageData(x, y, 24, 24).data
        return pixels.reduce((sum, value, index) => sum + Math.abs(value - expected[index]), 0) / pixels.length
      }, Array.from(png))
      expect(difference).toBeLessThan(2)
    }
    expect(saved[0].equals(saved[1])).toBe(false)
  })
}
