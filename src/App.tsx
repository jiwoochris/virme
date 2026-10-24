import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { captureStage, downloadPng } from './captureStage'
import { experienceModes, type ExperienceModeId } from './experienceModes'
import { InsightPanel } from './InsightPanel'
import { WebzinePanel } from './WebzinePanel'
import { MusicPanel, type AudioDrive } from './MusicPanel'
import { isNarrativeMessage, narrativeLabel, signalsToFeatures, type NarrativeObject } from './narrative'
import { normalizePersonalData, type NormalizedPersonalData, type PersonalFeatures } from './personalData'
import { StyleGanLayer, type ModelLayerStatus } from './StyleGanLayer'
import { portraitAsset, visualStates } from './visualStates'
import { visualModes, type VisualModeId } from './visualModes'
import './styles.css'

const PORTRAIT_STORAGE_KEY = 'virtueme:portrait-visible'

export default function App() {
  const state = visualStates[0]
  const [experienceMode, setExperienceMode] = useState<ExperienceModeId>('webzine')
  const [modeId, setModeId] = useState<VisualModeId>('place')
  const mode = visualModes.find((candidate) => candidate.id === modeId) ?? visualModes[0]
  const [modelStatus, setModelStatus] = useState<ModelLayerStatus>({ status: 'loading', active: false })
  const [portraitVisible, setPortraitVisible] = useState(() => {
    try { return localStorage.getItem(PORTRAIT_STORAGE_KEY) === 'true' }
    catch { return false }
  })
  const stageRef = useRef<HTMLDivElement>(null)
  const captureInProgress = useRef(false)
  const [saving, setSaving] = useState(false)
  const [captureMessage, setCaptureMessage] = useState('')
  useEffect(() => {
    try { localStorage.setItem(PORTRAIT_STORAGE_KEY, String(portraitVisible)) }
    catch { /* The toggle remains usable when storage is blocked. */ }
  }, [portraitVisible])

  const saveScreenshot = async () => {
    if (!stageRef.current || captureInProgress.current) return
    captureInProgress.current = true
    setSaving(true)
    setCaptureMessage('PNG를 준비하고 있습니다…')
    const filename = `virtueme-${modeId}-portrait-${portraitVisible ? 'on' : 'off'}-${new Date().toISOString().replace(/[:.]/g, '-')}.png`
    try {
      const blob = await captureStage(stageRef.current)
      downloadPng(blob, filename)
      setCaptureMessage('PNG 다운로드를 시작했습니다.')
    } catch (error) {
      setCaptureMessage(error instanceof Error ? error.message : '화면 저장에 실패했습니다. 다시 시도해 주세요.')
    } finally {
      captureInProgress.current = false
      setSaving(false)
    }
  }
  // 노벨 모드(iframe)가 core 서버에서 받은 서사 오브젝트. 시각화·비주얼 모드가 같은 값을 쓴다
  const [narrative, setNarrative] = useState<NarrativeObject | null>(null)
  const [features, setFeatures] = useState<PersonalFeatures | null>(null)
  const previousSignals = useRef<NormalizedPersonalData | undefined>(undefined)
  const [audioDrive, setAudioDrive] = useState<AudioDrive | null>(null)
  const [musicVideo, setMusicVideo] = useState<string | null>(null)
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || !isNarrativeMessage(event.data)) return
      const next = event.data.narrative
      setNarrative(next)
      setFeatures(signalsToFeatures(next.signals, previousSignals.current))
      previousSignals.current = normalizePersonalData(next.signals)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])
  const handleModelStatus = useCallback((status: ModelLayerStatus) => setModelStatus(status), [])
  const backendLabel = mode.id === 'place' && modelStatus.active
    ? `${modelStatus.model ?? 'STYLEGAN2'} LATENT STRUCTURE · ${(modelStatus.device ?? 'MAC').toUpperCase()}`
    : mode.id === 'place' && modelStatus.status === 'loading'
      ? 'STYLEGAN2 · MAC LOADING'
      : `${mode.model} · ${mode.subject}`

  const portraitStyle = useMemo(
    () =>
      ({
        '--portrait-opacity': state.portrait.opacity,
        '--portrait-scale': state.portrait.scale,
        '--portrait-contrast': state.portrait.contrast,
        '--portrait-saturate': state.portrait.saturate,
        '--state-color': mode.color,
        '--state-accent': mode.accent,
        '--dissolution': state.controls.dissolution,
        '--shear': state.controls.shear,
      }) as React.CSSProperties,
    [mode, state],
  )

  return (
    <main
      className={`app experience-${experienceMode}`}
      style={{ '--state-color': mode.color, '--state-accent': mode.accent } as React.CSSProperties}
      data-experience-mode={experienceMode}
    >
      <header className="header">
        <a className="wordmark" href="#stage" aria-label="버추어미 잠재공간 MVP">
          <span className="mark" aria-hidden="true"><i /><i /><i /></span>
          <span>VIRTUEME</span>
        </a>
        <nav className="experience-nav" aria-label="버추어미 경험 모드">
          {experienceModes.map((candidate) => (
            <button
              type="button"
              className={candidate.id === experienceMode ? 'active' : ''}
              aria-current={candidate.id === experienceMode ? 'page' : undefined}
              onClick={() => setExperienceMode(candidate.id)}
              disabled={saving}
              key={candidate.id}
            >
              <span>{candidate.index}</span>
              <strong>{candidate.label}</strong>
              <small>{candidate.koLabel}</small>
            </button>
          ))}
        </nav>
        <div className="live-status">
          <i /> {narrative ? narrativeLabel(narrative) : experienceMode === 'visual' ? `PORTRAIT ${portraitVisible ? 'ON' : 'OFF'}` : `${experienceMode.toUpperCase()} MODE`}
        </div>
      </header>

      <section className="webzine-panel" data-testid="webzine-mode" hidden={experienceMode !== 'webzine'}>
        <WebzinePanel active={experienceMode === 'webzine'} />
      </section>

      <section className="storyseed-panel" data-testid="novel-mode" hidden={experienceMode !== 'novel'}>
        <iframe
          src={`${import.meta.env.BASE_URL}storyseed-novel.html`}
          title="Storyseed 노벨 모드"
          sandbox="allow-scripts allow-same-origin allow-forms"
        />
      </section>


      <section className="insight-panel" data-testid="insight-mode" hidden={experienceMode !== 'insight'}>
        <InsightPanel narrative={narrative} active={experienceMode === 'insight'} />
      </section>

      <section className="visual-panel" data-testid="visual-mode" hidden={experienceMode !== 'visual'}>
      <section className="workspace single-example" id="stage">
        <nav className="state-nav" aria-label="잠재 비주얼 모드">
          <div className="eyebrow">THREE LATENT MODES</div>
          <h1>{mode.koLabel}의<br />{' '}잠재 장면</h1>
          <p className="intro">{mode.description}</p>
          <div className="reference-note">LUCID SONIC DREAMS · REAL STYLEGAN LATENT ROUTE</div>
          <div className="state-list mode-list">
            {visualModes.map((candidate) => (
              <button
                className={`state-button mode-button ${candidate.id === mode.id ? 'active' : ''}`}
                type="button"
                aria-current={candidate.id === mode.id ? 'true' : undefined}
                onClick={() => setModeId(candidate.id)}
                disabled={saving}
                key={candidate.id}
              >
                <span>{candidate.index}</span>
                <strong>{candidate.label}</strong>
                <small>{candidate.koLabel}</small>
              </button>
            ))}
          </div>
          <button
            className={`portrait-toggle ${portraitVisible ? 'active' : ''}`}
            type="button"
            aria-pressed={portraitVisible}
            data-testid="portrait-toggle"
            onClick={() => setPortraitVisible((visible) => !visible)}
            disabled={saving}
          >
            <span>PORTRAIT</span>
            <strong>{portraitVisible ? 'ON' : 'OFF'}</strong>
          </button>
          <button
            className="capture-button"
            type="button"
            onClick={saveScreenshot}
            disabled={saving}
            aria-label="화면 PNG 저장"
            aria-busy={saving}
          >
            {saving ? '저장 중…' : '화면 PNG 저장'}
          </button>
          <p className="capture-message" role="status">{captureMessage || '현재 비주얼과 초상 설정을 함께 저장합니다.'}</p>
          <MusicPanel narrative={narrative} onDrive={setAudioDrive} onVideo={setMusicVideo} />
          <div className="single-example-meta">
            <span>LOOP</span><strong>12 SEC</strong>
            <span>MODEL</span><strong>{mode.model}</strong>
            <span>RANGE</span><strong>{mode.subject}</strong>
          </div>
        </nav>

        <div
          ref={stageRef}
          className={`portrait-stage mode-${mode.id} route-stylegan`}
          style={portraitStyle}
          data-testid="portrait-stage"
          data-example-count="1"
        >
          <StyleGanLayer
            state={state.id}
            intensity={0.86}
            personalFeatures={features}
            mode={mode}
            enabled={experienceMode === 'visual'}
            onStatus={handleModelStatus}
            audioDrive={audioDrive}
          />
          {musicVideo && experienceMode === 'visual' && (
            <div className="music-video-layer">
              <video src={musicVideo} controls autoPlay playsInline data-testid="music-video" />
              <button type="button" onClick={() => setMusicVideo(null)}>영상 닫기</button>
            </div>
          )}
          {portraitVisible && (
            <figure className="portrait-anchor" data-testid="portrait-anchor">
              <div className="portrait-aura" aria-hidden="true" />
              <img
                src={portraitAsset}
                alt="잠재 풍경 앞에 표시된 익명의 합성 인물 초상"
                data-testid="central-portrait"
              />
              <figcaption>
                <span>SUBJECT 01</span>
                <strong>PORTRAIT ON</strong>
              </figcaption>
            </figure>
          )}
          <div className="state-caption">
            <span>{mode.index} / 03</span>
            <div>
              <p>{mode.subject}</p>
              <strong>{mode.koLabel} 모드</strong>
            </div>
          </div>
          <p className="state-description">{mode.description} 초상은 선택적으로 겹쳐 볼 수 있습니다.</p>
          <div className="model-chip">{backendLabel}</div>
          {narrative && (
            <div className="narrative-chip" data-testid="narrative-chip">
              <span>DATA</span> {narrativeLabel(narrative)}
            </div>
          )}
        </div>
      </section>

      <footer className="transport single-transport">
        <div className="play-button loop-label"><span aria-hidden="true">▶</span>LATENT LOOP</div>
        <div className="timeline" aria-label="12초 잠재 루프">
          <button className="active" aria-label="단일 잠재 장면" disabled><i /></button>
        </div>
        <div className="clock">LOOP DURATION <strong>12 SEC</strong></div>
      </footer>
      </section>
    </main>
  )
}
