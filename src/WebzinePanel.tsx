import { memo, useCallback, useEffect, useState } from 'react'
import { coreFetch, useCoreBase } from './coreApi'

/* 웹진 모드: core 서버가 만든 한 호(issue.html)를 그대로 보여 주고, 새 호 만들기·PDF 받기·템플릿 매칭 이유를 둔다.
   만들기는 백그라운드 작업이라 2초마다 상태를 묻는다. core 서버가 없으면 켜는 방법만 안내한다. */

type IssueItem = { id: string; title: string; range: [string, string]; period_ko: string; sections: string[]; cover?: string | null
  files: { html?: string; pdf?: string; pages?: string[] } }
type Signal = { id: string; label: string; count: number; points: number }
type Section = { template: string; label: string; slot: string; score: number; included: boolean; why: string; signals: Signal[] }
type Issue = { id: string; title: string; period_ko: string; records: number; sources: number; sections: Section[]
  checks: Record<string, { anchors: number; ok: number; blurred?: number }>; offline: boolean; seconds?: number }
type Job = { job: string; state: 'running' | 'done' | 'error'; log: string[]; issue_id: string | null; error: string | null; elapsed: number }

const SLOT_KO: Record<string, string> = { article: '기사', documentary: '다큐', fashion: '패션', digital_preview: '디지털',
  data_portrait: '데이터', novel: '소설', poem: '시' }

export const WebzinePanel = memo(function WebzinePanel({ active }: { active: boolean }) {
  const base = useCoreBase()
  const [state, setState] = useState<'loading' | 'ready' | 'offline'>('loading')
  const [issues, setIssues] = useState<IssueItem[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const [detail, setDetail] = useState<Issue | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [days, setDays] = useState(7)
  const [end, setEnd] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await coreFetch('/webzine', { signal: AbortSignal.timeout(8000) })
      if (!r.ok) throw new Error(String(r.status))
      const got = (await r.json()) as { issues: IssueItem[] }
      setIssues(got.issues)
      setCurrent((c) => c ?? got.issues[0]?.id ?? null)
      setState('ready')
    } catch {
      setState('offline')
    }
  }, [])

  useEffect(() => { if (active && state !== 'ready') void load() }, [active, state, load])

  useEffect(() => {
    if (!current) { setDetail(null); return }
    let alive = true
    void coreFetch(`/webzine/${current}/issue.json`).then(async (r) => {
      if (alive && r.ok) setDetail((await r.json()) as Issue)
    }).catch(() => undefined)
    return () => { alive = false }
  }, [current])

  useEffect(() => {
    if (!job || job.state !== 'running') return
    const t = setInterval(async () => {
      try {
        const r = await coreFetch(`/webzine/job/${job.job}`)
        if (!r.ok) return
        const next = (await r.json()) as Job
        setJob(next)
        if (next.state === 'done' && next.issue_id) {
          await load()
          setCurrent(next.issue_id)
        }
      } catch { /* 다음 차례에 다시 묻는다 */ }
    }, 2000)
    return () => clearInterval(t)
  }, [job, load])

  const make = async () => {
    const body: Record<string, unknown> = { days }
    if (end) body.end = end
    const r = await coreFetch('/webzine', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (r.ok) setJob((await r.json()) as Job)
    else setJob({ job: '', state: 'error', log: [], issue_id: null, error: `만들지 못했습니다 (HTTP ${r.status})`, elapsed: 0 })
  }

  if (state === 'offline') {
    return (
      <div className="webzine-empty">
        <h2>웹진은 core 서버가 만듭니다</h2>
        <p>터미널에서 <code>bun run core:serve</code> 로 core 서버를 켠 뒤 다시 열어 주세요. 기록을 넣는 방법은 core/README.md 에 있습니다.</p>
        <button type="button" onClick={() => void load()}>다시 연결</button>
      </div>
    )
  }

  const running = job?.state === 'running'
  const item = issues.find((i) => i.id === current)
  return (
    <div className="webzine">
      <aside className="webzine-side">
        <div className="wz-make">
          <span className="wz-k">NEW ISSUE</span>
          <div className="wz-row">
            <button type="button" className={days === 7 ? 'on' : ''} onClick={() => setDays(7)}>일주일</button>
            <button type="button" className={days === 1 ? 'on' : ''} onClick={() => setDays(1)}>하루</button>
          </div>
          <label className="wz-date">마지막 날 <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          <button type="button" className="wz-go" onClick={() => void make()} disabled={running}>
            {running ? `만드는 중… ${Math.round(job?.elapsed ?? 0)}초` : '이번 호 만들기'}
          </button>
          {running && job?.log.length ? <p className="wz-log">{job.log[job.log.length - 1]}</p> : null}
          {job?.state === 'error' ? <p className="wz-log err">{job.error}</p> : null}
        </div>
        <span className="wz-k">ISSUES</span>
        <ol className="wz-issues">
          {issues.map((i) => (
            <li key={i.id}>
              <button type="button" className={i.id === current ? 'on' : ''} onClick={() => setCurrent(i.id)}>
                <strong>{i.title}</strong><span>{i.period_ko}</span>
                <small>{i.cover ?? ''}</small>
              </button>
            </li>
          ))}
          {!issues.length && state === 'ready' ? <li className="wz-none">아직 만든 호가 없습니다.</li> : null}
        </ol>
        {detail ? (
          <div className="wz-why">
            <span className="wz-k">TEMPLATE MATCHING</span>
            <p className="wz-note">기록 {detail.records}건 · 출처 {detail.sources}종을 규칙표(rules.json)로 매겨 꼭지를 골랐습니다.</p>
            <ul>
              {detail.sections.map((s) => (
                <li key={s.template} className={s.included ? 'in' : 'out'}>
                  <b>{SLOT_KO[s.template] ?? s.template}</b>
                  <span className="sc">{s.score}</span>
                  <span className="w">{s.why}</span>
                  {s.signals.length ? <span className="sig">{s.signals.slice(0, 3).map((g) => `${g.label} ${g.count}`).join(' · ')}</span> : null}
                </li>
              ))}
            </ul>
            {Object.keys(detail.checks || {}).length ? (
              <p className="wz-note">근거 검사: {Object.entries(detail.checks).map(([k, c]) => `${SLOT_KO[k] ?? k} ${c.ok}/${c.anchors}`).join(' · ')}</p>
            ) : null}
          </div>
        ) : null}
      </aside>
      <section className="webzine-view">
        {item && base ? (
          <>
            <div className="wz-bar">
              <strong>VirtualME · {item.title}</strong><span>{item.period_ko}</span>
              <a href={`${base}/webzine/${item.id}/issue.html`} target="_blank" rel="noreferrer">새 창</a>
              {item.files.pdf ? <a href={`${base}/webzine/${item.id}/issue.pdf`} download={`virtualme-${item.id}.pdf`}>PDF</a> : null}
            </div>
            <iframe key={item.id} src={`${base}/webzine/${item.id}/issue.html`} title={`웹진 ${item.title}`} />
          </>
        ) : (
          <div className="webzine-empty"><h2>{state === 'loading' ? '불러오는 중…' : '왼쪽에서 이번 호를 만들어 주세요'}</h2>
            <p>일주일 기록으로 표지·기사·다큐·디지털 프리뷰·데이터 포트레이트·소설이나 시를 한 호로 묶습니다.</p></div>
        )}
      </section>
    </div>
  )
})
