import { toCanvas } from 'html-to-image'

/** Freeze the visible video frame, then composite the stage's DOM overlays. */
export async function captureStage(stage: HTMLElement): Promise<Blob> {
  const video = stage.querySelector('video')
  if (!video || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth) {
    throw new Error('화면을 불러오는 중입니다. 영상이 나타나면 다시 저장해 주세요.')
  }

  const { width, height } = stage.getBoundingClientRect()
  if (!width || !height) throw new Error('비주얼 화면을 연 뒤 다시 저장해 주세요.')
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * pixelRatio)
  canvas.height = Math.round(height * pixelRatio)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('이 브라우저에서 화면을 저장할 수 없습니다.')

  // Match .latent-loop-video's centered object-fit: cover. Drawing the video
  // separately avoids DOM capture libraries stretching it to the stage ratio.
  const scale = Math.max(canvas.width / video.videoWidth, canvas.height / video.videoHeight)
  const frameWidth = video.videoWidth * scale
  const frameHeight = video.videoHeight * scale
  context.drawImage(video, (canvas.width - frameWidth) / 2, (canvas.height - frameHeight) / 2, frameWidth, frameHeight)

  await Promise.all(Array.from(stage.querySelectorAll('img'), (image) => image.decode()))
  const overlays = await toCanvas(stage, {
    pixelRatio,
    canvasWidth: width,
    canvasHeight: height,
    backgroundColor: 'transparent',
    style: { background: 'transparent' },
    filter: (node) => !node.classList?.contains('stylegan-field'),
    preferredFontFormat: 'woff2',
  })
  context.drawImage(overlays, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('PNG 변환에 실패했습니다. 다시 시도해 주세요.'))
    }, 'image/png')
  })
}

export function downloadPng(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Give the browser time to consume the download before releasing the URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
