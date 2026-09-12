import { useEffect, useMemo, useRef, useState } from 'react'
import type { AudioDrive } from './MusicPanel'
import { demoPersonalFeatures, type PersonalFeatures } from './personalData'
import { buildStyleGanRequest, generateStyleGanFrame, getStyleGanHealth, type StyleGanHealth } from './styleGanClient'
import type { VisualStateId } from './visualStates'
import type { VisualMode } from './visualModes'

const stateIndex: Record<VisualStateId, number> = {
  stability: 0,
  novelty: 1,
  conflict: 2,
  uncertainty: 3,
  'possible-self': 4,
}

const isStaticPublicBuild = import.meta.env.BASE_URL !== '/'

export type ModelLayerStatus = StyleGanHealth & { active: boolean }

export function StyleGanLayer({
  state,
  intensity,
  personalFeatures,
  mode,
  enabled,
  onStatus,
  audioDrive,
}: {
  state: VisualStateId
  intensity: number
  personalFeatures: PersonalFeatures | null
  mode: VisualMode
  enabled: boolean
  onStatus: (status: ModelLayerStatus) => void
  /** 기록의 음악을 재생하는 동안의 음량·박동. 잠재 루프의 재생 속도와 밝기를 흔든다 */
  audioDrive?: AudioDrive | null
}) {
  const [health, setHealth] = useState<StyleGanHealth>({ status: 'loading' })
  const [currentFrame, setCurrentFrame] = useState<string | null>(null)
  const [videoReady, setVideoReady] = useState(false)
  const variation = useRef(0)
  const videoRef = useRef<HTMLVideoElement>(null)
  const reactive = Boolean(audioDrive?.playing)

  // 음량이 클수록 잠재 루프를 빨리 돌린다 (0.55~1.85배). 바뀐 폭이 작으면 건드리지 않아 끊김을 막는다
  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const rate = reactive ? Math.min(1.85, 0.55 + 1.3 * (audioDrive?.level ?? 0)) : 1
    if (Math.abs(el.playbackRate - rate) > 0.05) el.playbackRate = rate
  }, [reactive, audioDrive?.level])
  const features = useMemo(
    () => personalFeatures ?? demoPersonalFeatures(stateIndex[state] * 1.7, stateIndex[state]),
    [personalFeatures, state],
  )
  const shouldUseLiveModel = enabled && !isStaticPublicBuild && mode.id === 'place'

  useEffect(() => setVideoReady(false), [mode.id])

  useEffect(() => {
    if (!shouldUseLiveModel) {
      onStatus({ status: 'ready', backend: 'pre-rendered-video', model: mode.model, active: false })
      return
    }
    let cancelled = false
    getStyleGanHealth()
      .then((next) => {
        if (cancelled) return
        setHealth(next)
        onStatus({ ...next, active: next.status === 'ready' })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const next: StyleGanHealth = { status: 'error', detail: error instanceof Error ? error.message : 'offline' }
        setHealth(next)
        onStatus({ ...next, active: false })
      })
    return () => { cancelled = true }
  }, [mode.model, onStatus, shouldUseLiveModel])

  useEffect(() => {
    if (!shouldUseLiveModel || health.status !== 'ready') return
    let cancelled = false
    const generate = async () => {
      try {
        const blob = await generateStyleGanFrame(buildStyleGanRequest(features, state, intensity, variation.current++))
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        setCurrentFrame((oldCurrent) => {
          if (oldCurrent) URL.revokeObjectURL(oldCurrent)
          return url
        })
        onStatus({ ...health, active: true })
      } catch (error) {
        if (!cancelled) onStatus({ status: 'error', active: false, detail: error instanceof Error ? error.message : 'generation failed' })
      }
    }
    void generate()
    return () => { cancelled = true }
  }, [features, health, intensity, onStatus, shouldUseLiveModel, state])

  useEffect(() => () => {
    if (currentFrame) URL.revokeObjectURL(currentFrame)
  }, [currentFrame])

  return (
    <div
      className={`stylegan-field ${videoReady || currentFrame ? 'has-frame' : ''}`}
      data-testid="stylegan-layer"
      data-renderer={mode.renderer}
      data-model={health.model ?? mode.model}
      data-device={health.device ?? 'unavailable'}
      data-precision={health.precision ?? 'fp32'}
      data-render-mode={health.render_mode ?? 'latent-structure'}
      data-class-count={mode.classes.length}
      data-display-mode="latent-loop-video"
      data-active={Boolean(enabled && (videoReady || currentFrame))}
      data-effects={reactive ? 'audio-reactive' : 'none'}
      style={{ '--audio-pulse': reactive ? (audioDrive?.pulse ?? 0).toFixed(3) : 0 } as React.CSSProperties}
      aria-hidden="true"
    >
      {enabled && (
        <video
          key={mode.id}
          ref={videoRef}
          className="latent-loop-video"
          src={`${import.meta.env.BASE_URL}${mode.video}`}
          poster={mode.id === 'place' ? currentFrame ?? undefined : undefined}
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          onCanPlay={() => setVideoReady(true)}
          onError={() => setVideoReady(false)}
          data-testid="latent-loop-video"
          data-source="stylegan-latent-loop"
          data-mode={mode.id}
          data-effects="none"
          data-frame-count="144"
        />
      )}
    </div>
  )
}
