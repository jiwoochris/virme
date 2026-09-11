import { useCallback, useEffect, useRef, useState } from 'react'
import { coreFetch, useCoreBase } from './coreApi'
import { koDate, type NarrativeObject } from './narrative'

/* 기록의 음악: core 서버가 서사 오브젝트의 정서 곡선으로 음악 계획을 세우고 로컬 ACE-Step 이 만든다.
   재생하는 동안 features.json 의 음량·온셋을 현재 재생 위치로 읽어 비주얼 모드에 넘긴다(onDrive). */


export type MusicSection = {
  index: number; name: string; start_date: string; end_date: string; keyscale: string; mood_ko: string; energy_ko: string
  valence: number; arousal: number; start_sec: number; end_sec: number
}
export type MusicPlan = { music_id: string; narrative_id: string; keyword: string; bpm: number; time_signature: number; band: string; genre: string; sections: MusicSection[]; notes: string[] }
type Features = { fps: number; frames: number; rms: number[]; onset: number[]; duration?: number; sections?: { index: number; start_sec: number; end_sec: number }[] }
type Status = { music_id: string; state: 'ready' | 'running' | 'missing' | 'error' | 'unavailable'; stage?: string; done?: number; total?: number; error?: string; plan?: MusicPlan; audio?: string; video?: boolean; videos?: string[] }
const VIDEO_LABEL: Record<string, [string, string]> = { place: ['장소', 'video.mp4'], object: ['사물', 'video-object.mp4'], situation: ['상황', 'video-situation.mp4'] }
export type AudioDrive = { level: number; pulse: number; playing: boolean }

export function MusicPanel({ narrative, onDrive, onVideo }: {
  narrative: NarrativeObject | null
  onDrive: (drive: AudioDrive) => void
  onVideo: (url: string | null) => void
}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [plan, setPlan] = useState<MusicPlan | null>(null)
  const [features, setFeatures] = useState<Features | null>(null)
  const [showNotes, setShowNotes] = useState(false)
  const audioRef = useRef<HTMLAudioElement>(null)
  const pulse = useRef(0)
  const base = useCoreBase()
  const usable = narrative && narrative.evidence_grade !== '근거 부족'

  // 키워드가 바뀌면 이전 음악을 내리고, 계획(만들지 않고 값만)을 먼저 받아 보여 준다. 캐시가 있으면 바로 재생할 수 있다
  useEffect(() => {
    setStatus(null); setPlan(null); setFeatures(null); onVideo(null)
    if (!usable) return
    let cancelled = false
    coreFetch('/music/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keyword: narrative.keyword }) })
      .then((r) => (r.ok ? r.json() : null))
      .then(async (p: MusicPlan | null) => {
        if (cancelled || !p) return
        setPlan(p)
        const st: Status = await (await coreFetch(`/music/${p.music_id}`)).json()
        if (!cancelled) setStatus(st)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [narrative?.narrative_id]) // eslint-disable-line react-hooks/exhaustive-deps

  // 만드는 동안 2초마다 상태를 본다
  useEffect(() => {
    if (!status || status.state !== 'running') return
    const t = setTimeout(async () => {
      try { setStatus(await (await coreFetch(`/music/${status.music_id}`)).json()) } catch { /* 다음 차례에 다시 */ }
    }, 2000)
    return () => clearTimeout(t)
  }, [status])

  useEffect(() => {
    if (status?.state !== 'ready') return
    coreFetch(`/music/${status.music_id}/features.json`).then((r) => r.json()).then(setFeatures).catch(() => {})
  }, [status?.state, status?.music_id])

  const start = useCallback(async () => {
    if (!narrative) return
    setStatus((s) => ({ ...(s ?? { music_id: plan?.music_id ?? '' }), state: 'running', stage: '음악 생성 준비 중' }))
    try {
      const r = await coreFetch('/music', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keyword: narrative.keyword }) })
      setStatus(await r.json())
    } catch {
      setStatus((s) => ({ ...(s as Status), state: 'error', error: '로컬 기록 서버에 연결하지 못했습니다' }))
    }
  }, [narrative, plan])

  // 재생 위치의 음량·온셋을 매 프레임 넘긴다. 온셋은 바로 올라가고 0.25초에 걸쳐 잦아든다
  useEffect(() => {
    const el = audioRef.current
    if (!el || !features) return
    let raf = 0
    let last = performance.now()
    let sent = { level: -1, pulse: -1, at: 0 }
    const tick = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      const i = Math.min(features.frames - 1, Math.floor(el.currentTime * features.fps))
      const on = features.onset[i] ?? 0
      pulse.current = Math.max(on ** 1.5, pulse.current * Math.exp(-dt / 0.25))
      const level = features.rms[i] ?? 0
      // 화면 전체를 다시 그리지 않도록 초당 30번까지, 값이 눈에 띄게 바뀔 때만 넘긴다
      if (now - sent.at > 33 && (Math.abs(level - sent.level) > 0.02 || Math.abs(pulse.current - sent.pulse) > 0.02)) {
        sent = { level, pulse: pulse.current, at: now }
        onDrive({ level, pulse: pulse.current, playing: !el.paused })
      }
      if (!el.paused) raf = requestAnimationFrame(tick)
    }
    const play = () => { last = performance.now(); raf = requestAnimationFrame(tick) }
    const stop = () => { cancelAnimationFrame(raf); onDrive({ level: 0, pulse: 0, playing: false }) }
    el.addEventListener('play', play)
    el.addEventListener('pause', stop)
    el.addEventListener('ended', stop)
    return () => { el.removeEventListener('play', play); el.removeEventListener('pause', stop); el.removeEventListener('ended', stop); stop() }
  }, [features, onDrive])

  if (!usable) {
    return <div className="music-panel"><div className="music-head"><span>MUSIC</span><strong>기록의 음악</strong></div>
      <p className="music-note">{narrative
        ? `'${narrative.keyword}'는 근거가 부족해서 음악을 만들지 않습니다.`
        : '노벨 모드에서 키워드를 심으면 그 근거 기록의 정서 곡선으로 음악을 만듭니다.'}</p></div>
  }
  const sections = plan?.sections ?? []
  const real = features?.sections
  return (
    <div className="music-panel" data-testid="music-panel">
      <div className="music-head"><span>MUSIC</span><strong>기록의 음악 · {narrative.keyword}</strong></div>
      {plan && <p className="music-meta">{plan.bpm} BPM · {plan.time_signature}/4 · {plan.band} · {plan.genre}</p>}
      <ol className="music-sections">
        {sections.map((s, i) => (
          <li key={s.index}>
            <b>{s.name}</b> {koDate(s.start_date)}~{koDate(s.end_date)}
            <span>{s.keyscale} · {s.mood_ko} · {s.energy_ko}{real?.[i] ? ` · ${Math.round(real[i].start_sec)}초~` : ''}</span>
          </li>
        ))}
      </ol>
      {status?.state === 'ready' && base ? (
        <>
          <audio ref={audioRef} controls preload="auto" src={`${base}/music/${status.music_id}/${status.audio ?? 'music.m4a'}`} data-testid="music-audio" />
          {(status.videos ?? (status.video ? ['place'] : [])).length > 0 && (
            <div className="music-videos">
              <span>오디오 반응 영상</span>
              {(status.videos ?? ['place']).map((m) => (
                <button type="button" key={m} className="music-button small" onClick={() => { audioRef.current?.pause(); onVideo(`${base}/music/${status.music_id}/${VIDEO_LABEL[m]?.[1] ?? 'video.mp4'}`) }}>
                  {VIDEO_LABEL[m]?.[0] ?? m}
                </button>
              ))}
            </div>
          )}
        </>
      ) : status?.state === 'running' ? (
        <p className="music-note" role="status">{status.stage ?? '음악 생성 중'}{status.total ? ` · 구간 ${Math.min((status.done ?? 0) + 1, status.total)}/${status.total}` : ''}. 구간마다 1분쯤 걸립니다.</p>
      ) : (
        <>
          <button type="button" className="music-button" onClick={start} disabled={!plan}>음악 만들기</button>
          {(status?.state === 'unavailable' || status?.state === 'error') && <p className="music-note warn">{status.error}</p>}
        </>
      )}
      {plan && (
        <button type="button" className="music-link" onClick={() => setShowNotes((v) => !v)}>{showNotes ? '매핑 접기' : '정서 → 음악 매핑 보기'}</button>
      )}
      {showNotes && plan && <ul className="music-notes">{plan.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </div>
  )
}
