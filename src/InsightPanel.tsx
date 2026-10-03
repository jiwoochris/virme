import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { coreFetch, useCoreBase } from './coreApi'
import { koDate, relabel, type NarrativeObject } from './narrative'
import { ColumnsSections, strongTurns, type Columns, type TurningPoint } from './ColumnsPanel'
import { MyShares } from './SharePanel'
import type { Share } from './share'

/* 시각화 렌더러: 서사 오브젝트 + core 서버의 전체 정서 곡선·주제 묶음·관계 그래프·간극.
   색은 역할로만 쓴다 (dataviz 기준 팔레트의 어두운 단계, 2026-09-11 validate_palette.js 통과):
   선 = 1번 파랑, 근거 날짜 점 = 2번 주황, 그래프 종류 = 파랑·주황·청록 3가지 + 그 밖 회색. 글자는 모두 텍스트 색. */

const C = { line: '#3987e5', evidence: '#d95926', aqua: '#199e70', gray: '#6f6e69', grid: 'rgba(255,255,255,.07)',
  axis: 'rgba(255,255,255,.16)', muted: '#898781', sub: '#c3c2b7' }

type DaySeries = { date: string; valence: number; valence_raw: number | null; records: number; sparse: boolean }
type Topic = { id: string; label: string; size: number; keywords: string[]; first: string | null; last: string | null }
type GraphNode = { id: string; type: string; type_ko: string; name: string; aliases?: string[]; count: number; first: string; last: string; valence: number | null; focus: number | null }
type GraphEdge = { source: string; target: string; weight: number; predicates: Record<string, number> }
type Gap = { claim: string; topic: string; date: string; quote: string; verdict: '간극' | '일치' | '판단 보류'; summary: string; evidence_after: number }
type People = Record<string, string | null>
type MapPoint = { id: string; x: number; y: number; topic: string; date: string; source: string; text: string; ev: 'strong' | 'weak' | null }
type MapTopic = { id: string; label: string; size: number; cx: number; cy: number }
type SemMap = { points: MapPoint[]; topics: MapTopic[]; keyword: string | null }
type Core = { series: DaySeries[]; topics: Topic[]; other: number; graph: { nodes: GraphNode[]; edges: GraphEdge[] }; map: SemMap | null; gaps: Gap[] | null; people: People; records: number; columns: Columns | null }

async function getJson<T>(path: string): Promise<T> {
  const r = await coreFetch(path, { signal: AbortSignal.timeout(120000) })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<T>
}

// 비주얼 모드가 음악에 맞춰 초당 수십 번 다시 그려질 때 이 화면까지 다시 그리지 않게 memo 로 감싼다
export const InsightPanel = memo(function InsightPanel({ narrative, active }: { narrative: NarrativeObject | null; active: boolean }) {
  const [core, setCore] = useState<Core | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'offline'>('loading')
  const base = useCoreBase()
  const [shares, setShares] = useState<Share[] | null>(null)
  const [shareTick, setShareTick] = useState(0)
  const keyword = narrative && narrative.evidence_grade !== '근거 부족' ? narrative.keyword : null

  useEffect(() => {
    if (!active) return
    let cancelled = false
    setState((s) => (s === 'ready' ? s : 'loading'))
    Promise.all([
      getJson<{ status: string; records: number }>('/health'),
      getJson<{ series: DaySeries[] }>('/emotion'),
      getJson<{ topics: Topic[]; other?: number }>('/clusters'),
      getJson<{ nodes: GraphNode[]; edges: GraphEdge[]; people?: People }>(`/graph?limit=34${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ''}`),
      getJson<SemMap>(`/map${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''}`).catch(() => null),
      getJson<Columns>('/columns').catch(() => null),
    ])
      .then(([h, e, t, g, m, cols]) => {
        if (cancelled) return
        const people = g.people ?? {}
        const named = { ...g, nodes: g.nodes.map((n) => ({ ...n, name: relabel(n.name, people) })) }
        setCore((prev) => ({ series: e.series, topics: t.topics, other: t.other ?? 0, graph: named, map: m, gaps: prev?.gaps ?? null, people, records: h.records, columns: cols }))
        setState('ready')
        // 간극은 처음 한 번 LLM 을 부르므로 따로, 늦게 받는다
        getJson<{ gaps: Gap[] }>('/gaps').then((x) => !cancelled && setCore((c) => (c ? { ...c, gaps: x.gaps } : c))).catch(() => {})
      })
      .catch(() => !cancelled && setState('offline'))
    return () => { cancelled = true }
  }, [active, keyword])

  // 내 공유: 화면을 열 때마다, 그리고 이 화면에서 카드를 만들거나 지운 뒤에 다시 받는다 (노벨 모드에서 만든 것도 열 때 보인다)
  useEffect(() => {
    if (!active) return
    let cancelled = false
    getJson<{ shares: Share[] }>('/share').then((d) => !cancelled && setShares(d.shares)).catch(() => !cancelled && setShares([]))
    return () => { cancelled = true }
  }, [active, shareTick])

  if (state === 'offline') {
    return (
      <div className="insight-empty">
        <strong>로컬 기록 서버가 꺼져 있습니다</strong>
        <p>이 화면은 내 기록을 처리하는 core 서버가 켜져 있을 때만 보입니다. <code>bun run core:serve</code> 로 켠 뒤 다시 열어 주세요.</p>
      </div>
    )
  }
  if (!core) return <div className="insight-empty"><p>기록을 불러오는 중입니다</p></div>
  const turns = strongTurns(core.columns)

  return (
    <div className={`insight ${state === 'loading' ? 'refetching' : ''}`}>
      <Kpis narrative={narrative} core={core} />
      <section className="insight-card wide">
        <h2>날짜별 정서 흐름</h2>
        <p className="insight-sub">하루 기록의 정서가를 7일 이동평균으로 이은 선입니다. 0 보다 위는 긍정, 아래는 부정입니다{narrative?.change_points?.length ? '. 세로선은 흐름이 꺾인 변화점(PELT)입니다' : ''}{turns.length ? '. 번호가 달린 점선은 대화량·밤 기록·장소·지출·걸음 수 같은 여러 흐름이 함께 꺾인 전환점입니다' : ''}.</p>
        <EmotionChart series={core.series} narrative={narrative} turns={turns} />
      </section>
      <ColumnsSections cols={core.columns} people={core.people} importUrl={base ? `${base}/import` : null} onShared={() => setShareTick((t) => t + 1)} />
      <div className="insight-heading" id="my-shares">
        <h2>내 공유</h2>
        <p>밖에 보여 주려고 만든 카드를 날짜별로 모았습니다. 공유 페이지를 연 사람이 남긴 반응과 한마디는 여기에서만 보입니다. 공유를 지우려면 내 기록 넣기 화면의 4. 밖으로 나간 글에서 지웁니다.</p>
      </div>
      <section className="insight-card wide" data-testid="shares">
        <MyShares shares={shares} base={base} />
      </section>
      {core.map && core.map.points.length > 0 && (
        <section className="insight-card wide">
          <h2>의미 지도</h2>
          <p className="insight-sub">본인 기록을 뜻(bge-m3 벡터)이 가까운 것끼리 가깝게 2차원에 펼쳤습니다(UMAP). 점 하나가 기록 하나이고, 글자는 주제 묶음 이름입니다{core.map.keyword ? `. 흰 테두리는 '${core.map.keyword}' 근거 기록입니다` : ''}.</p>
          <SemanticMap map={core.map} narrative={narrative} people={core.people} />
        </section>
      )}
      <section className="insight-card">
        <h2>{narrative?.clusters?.length ? `'${narrative.keyword}' 근거가 속한 주제` : '기록 주제 묶음'}</h2>
        <p className="insight-sub">{narrative?.clusters?.length
          ? '근거 기록 가운데 각 주제 묶음(BERTopic)에 든 비율입니다.'
          : `전체 본인 기록을 주제로 묶은 크기입니다.${core.other ? ` 공통 활동이 없는 짧은 말 ${core.other}건은 뺐습니다.` : ''}`}</p>
        <TopicBars narrative={narrative} topics={core.topics} />
      </section>
      <section className="insight-card">
        <h2>기분과 함께 여부</h2>
        <p className="insight-sub">{narrative?.mood_mix ? `'${narrative.keyword}' 근거 기록과 전체 기록의 비율을 나란히 봅니다.` : '키워드를 심으면 그 근거 기록과 전체 기록을 비교합니다.'}</p>
        {narrative?.mood_mix && <Dumbbell title="기분" mix={narrative.mood_mix} />}
        {narrative?.social_mix && <Dumbbell title="함께 여부" mix={narrative.social_mix} />}
      </section>
      <section className="insight-card">
        <h2>{keyword ? `'${keyword}' 근처의 사람·장소·활동` : '자주 나오는 사람·장소·활동'}</h2>
        <p className="insight-sub">가운데가 나입니다. 가까울수록 {keyword ? '근거 기록에' : '기록에'} 자주 함께 나왔습니다.</p>
        <RelationGraph graph={core.graph} />
      </section>
      <section className="insight-card">
        <h2>스스로 말한 나와 기록</h2>
        <p className="insight-sub">자기 성향을 단정한 말을 찾아, 그 뒤의 기록과 비교했습니다. 문장의 수치는 기록에서 센 값입니다.</p>
        <Gaps gaps={core.gaps} people={core.people} />
      </section>
    </div>
  )
})

// 산점도는 모든 점 쌍이 구분돼야 하므로 dataviz 기준 팔레트에서 모든 쌍 검사를 통과하는 첫 세 색만 주제에 쓰고, 나머지는 회색으로 둔다
function SemanticMap({ map, narrative, people }: { map: SemMap; narrative: NarrativeObject | null; people: People }) {
  const [hover, setHover] = useState<MapPoint | null>(null)
  const W = 920, H = 440, P = 22
  const x = (v: number) => P + v * (W - 2 * P)
  const y = (v: number) => P + v * (H - 2 * P)
  const colored = useMemo(() => {
    const fromNarr = (narrative?.clusters ?? []).map((c) => c.id.replace(/^topic:/, '')).filter((id) => map.topics.some((t) => t.id === id))
    const ids = (fromNarr.length ? fromNarr : map.topics.map((t) => t.id)).slice(0, 3)
    return new Map(ids.map((id, i) => [id, [C.line, C.evidence, C.aqua][i]]))
  }, [map, narrative])
  const label = useMemo(() => new Map(map.topics.map((t) => [t.id, t.label])), [map])
  const ordered = useMemo(() => [...map.points].sort((a, b) => Number(Boolean(a.ev)) - Number(Boolean(b.ev))), [map])
  const evidence = map.points.filter((p) => p.ev)
  const focus = Boolean(map.keyword)
  // 주제 이름표가 겹치면 큰 묶음부터 자리를 잡고, 겹치는 이름표는 위아래로 비켜 놓는다
  const labels = useMemo(() => {
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = []
    const out: { id: string; label: string; x: number; y: number }[] = []
    for (const t of [...map.topics].sort((a, b) => b.size - a.size)) {
      const w = [...t.label].reduce((n, ch) => n + (/[가-힣]/.test(ch) ? 12.5 : 7), 0), h = 15
      const cx = P + t.cx * (W - 2 * P), cy = P + t.cy * (H - 2 * P)
      for (const dy of [0, 16, -16, 32, -32, 48]) {
        const box = { x0: cx - w / 2, y0: cy + dy - h + 3, x1: cx + w / 2, y1: cy + dy + 3 }
        if (!placed.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) {
          placed.push(box)
          out.push({ id: t.id, label: t.label, x: cx, y: cy + dy })
          break
        }
      }
    }
    return out
  }, [map])
  return (
    <div className="graph-wrap semmap">
      <div className="legend small">
        {[...colored].map(([id, col]) => <span key={id}><i className="key-dot" style={{ background: col }} />{label.get(id)}</span>)}
        <span><i className="key-dot" style={{ background: C.gray }} />그 밖의 주제·짧은 말</span>
        {focus && <span><i className="key-dot ring" />'{map.keyword}' 근거 {evidence.length}건</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={`본인 기록 ${map.points.length}건의 의미 지도`}>
        {ordered.map((p) => {
          const col = colored.get(p.topic) ?? C.gray
          const dim = focus && !p.ev
          return (
            <g key={p.id} onPointerEnter={() => setHover(p)} onPointerLeave={() => setHover(null)}
              tabIndex={p.ev ? 0 : -1} onFocus={() => setHover(p)} onBlur={() => setHover(null)}
              aria-label={p.ev ? `${koDate(p.date)} ${label.get(p.topic) ?? '짧은 말·기타'} 근거 기록` : undefined}>
              <circle cx={x(p.x)} cy={y(p.y)} r={9} fill="transparent" />
              <circle cx={x(p.x)} cy={y(p.y)} r={p.ev ? 5.5 : 3.6} fill={col}
                fillOpacity={dim ? 0.28 : colored.has(p.topic) ? 0.9 : 0.5}
                stroke={p.ev ? '#ffffff' : '#15161d'} strokeWidth={p.ev ? 1.8 : 1} />
            </g>
          )
        })}
        {labels.map((t) => (
          <text key={t.id} x={t.x} y={t.y} textAnchor="middle" className="node-label map-label">{t.label}</text>
        ))}
      </svg>
      {hover && (
        <div className="graph-tip">
          <strong>{label.get(hover.topic) ?? '짧은 말·기타'}</strong> <span>{koDate(hover.date)} · {hover.source}{hover.ev ? ` · ${hover.ev === 'strong' ? '강한' : '약한'} 근거` : ''}</span>
          <br /><span>{relabel(hover.text, people)}</span>
        </div>
      )}
      {focus && evidence.length > 0 && (
        <details className="table-view">
          <summary>근거 기록 목록 보기</summary>
          <table>
            <thead><tr><th>날짜</th><th>주제</th><th>기록</th></tr></thead>
            <tbody>
              {[...evidence].sort((a, b) => a.date.localeCompare(b.date)).slice(0, 40).map((p) => (
                <tr key={p.id}><td>{koDate(p.date)}</td><td>{label.get(p.topic) ?? '짧은 말·기타'}</td><td>{relabel(p.text, people)}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

function Kpis({ narrative, core }: { narrative: NarrativeObject | null; core: Core }) {
  const base = useCoreBase()
  const first = core.series[0]?.date, last = core.series[core.series.length - 1]?.date
  if (!narrative) {
    return (
      <div className="kpi-row">
        <Tile label="기록" value={`${core.records.toLocaleString()}건`} />
        <Tile label="기간" value={first && last ? `${koDate(first)} ~ ${koDate(last)}` : '—'} />
        <Tile label="주제 묶음" value={`${core.topics.length}개`} />
        <div className="kpi-note">노벨 모드에서 키워드를 심으면 이 화면과 비주얼 모드가 같은 근거로 바뀝니다.
          {base && <> 기록을 더 넣으려면 <a href={`${base}/import`} target="_blank" rel="noreferrer">내 기록 넣기</a>.</>}</div>
      </div>
    )
  }
  const b = narrative.grade_basis, em = narrative.emotion
  const delta = em && em.mean != null && em.baseline != null ? em.mean - em.baseline : null
  return (
    <div className="kpi-row">
      <Tile label="키워드" value={narrative.keyword} foot={narrative.evidence_grade} />
      <Tile label="강한 근거" value={`${b.strong_records}건`} foot={`기록이 있는 ${b.active_days}일 가운데 ${b.strong_days}일`} />
      {narrative.stats && <Tile label="근거 기간" value={`${koDate(narrative.stats.first_date)} ~ ${koDate(narrative.stats.last_date)}`} />}
      {em?.mean != null && <Tile label="근거 기록 정서 평균" value={em.mean.toFixed(2)}
        foot={delta != null ? `전체 ${em.baseline!.toFixed(2)}보다 ${delta >= 0 ? '+' : ''}${delta.toFixed(2)}` : undefined} />}
      <Tile label="서사 번호" value={`#${narrative.narrative_id}`} foot="노벨·비주얼과 같은 번호면 같은 결과" />
    </div>
  )
}

function Tile({ label, value, foot }: { label: string; value: string; foot?: string }) {
  return <div className="kpi"><span>{label}</span><strong>{value}</strong>{foot && <small>{foot}</small>}</div>
}

function EmotionChart({ series, narrative, turns }: { series: DaySeries[]; narrative: NarrativeObject | null; turns: TurningPoint[] }) {
  const W = 920, H = 250, BAR = 70, PAD = { l: 40, r: 16, t: 14, b: 26 }
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const ev = useMemo(() => new Map((narrative?.emotion?.series ?? []).map((d) => [d.date, d.evidence ?? 0])), [narrative])
  const n = series.length
  const x = (i: number) => PAD.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - PAD.l - PAD.r))
  const y = (v: number) => PAD.t + (1 - (v + 1) / 2) * (H - PAD.t - PAD.b)
  const path = series.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d.valence).toFixed(1)}`).join('')
  const idx = new Map(series.map((d, i) => [d.date, i]))
  const maxEv = Math.max(1, ...Array.from(ev.values()))
  const ticks = series.map((d, i) => ({ d, i })).filter(({ d }) => d.date.endsWith('-01') || d.date.endsWith('-15'))
  const pick = (clientX: number) => {
    const r = svgRef.current?.getBoundingClientRect(); if (!r) return
    const px = ((clientX - r.left) / r.width) * W
    setHover(Math.max(0, Math.min(n - 1, Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1)))))
  }
  const h = hover != null ? series[hover] : null
  return (
    <div className="chart-wrap">
      {(narrative?.emotion || turns.length > 0) && (
        <div className="legend">
          <span><i className="key-line" style={{ background: C.line }} />하루 정서 (7일 평균)</span>
          {narrative?.emotion && <span><i className="key-dot" style={{ background: C.evidence }} />'{narrative.keyword}' 근거가 나온 날</span>}
          {turns.length > 0 && <span><i className="key-line dashed" style={{ borderColor: C.aqua }} />여러 흐름이 함께 꺾인 전환점</span>}
        </div>
      )}
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H + BAR}`} className="chart" role="img" tabIndex={0}
        aria-label="날짜별 정서가 선 그래프. 방향키로 날짜를 옮기며 값을 볼 수 있습니다"
        onPointerMove={(e) => pick(e.clientX)} onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') setHover((v) => Math.min(n - 1, (v ?? -1) + 1))
          if (e.key === 'ArrowLeft') setHover((v) => Math.max(0, (v ?? n) - 1))
        }}>
        {[-1, -0.5, 0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke={v === 0 ? C.axis : C.grid} strokeWidth={1} />
            <text x={PAD.l - 8} y={y(v) + 3} textAnchor="end" className="tick">{v > 0 ? `+${v}` : v}</text>
          </g>
        ))}
        {(narrative?.change_points ?? []).map((c) => {
          const i = idx.get(c.date); if (i == null) return null
          return (
            <g key={c.date}>
              <line x1={x(i)} x2={x(i)} y1={PAD.t} y2={H - PAD.b} stroke={C.sub} strokeWidth={1} opacity={0.5} />
              <text x={x(i) + 5} y={PAD.t + 10} className="annot">{koDate(c.date)} {c.direction} {c.delta.toFixed(2)}</text>
            </g>
          )
        })}
        {turns.map((t, k) => {
          const i = idx.get(t.date); if (i == null) return null
          return (
            <g key={`turn-${t.date}`} data-testid="turning-point">
              <title>{`전환점 ${k + 1} · ${koDate(t.date)} 무렵: ${t.columns.map((c) => `${c.series} ${c.direction}`).join(', ')}`}</title>
              <line x1={x(i)} x2={x(i)} y1={PAD.t + 16} y2={H - PAD.b} stroke={C.aqua} strokeWidth={1.2} strokeDasharray="3 4" opacity={0.85} />
              <circle cx={x(i)} cy={H - PAD.b - 9} r={8} fill="#15161d" stroke={C.aqua} strokeWidth={1.5} />
              <text x={x(i)} y={H - PAD.b - 5.5} textAnchor="middle" className="turn-no">{k + 1}</text>
            </g>
          )
        })}
        <path d={path} fill="none" stroke={C.line} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {series.map((d, i) => (ev.get(d.date) ?? 0) > 0 && (
          <circle key={d.date} cx={x(i)} cy={y(d.valence)} r={4} fill={C.evidence} stroke="#15161d" strokeWidth={2} />
        ))}
        {/* 아래 칸: 근거가 나온 날 (같은 가로축, 따로 그린 작은 막대) */}
        {narrative?.emotion && series.map((d, i) => {
          const c = ev.get(d.date) ?? 0; if (!c) return null
          const bh = (c / maxEv) * (BAR - 22)
          const bw = Math.max(2, Math.min(6, (W - PAD.l - PAD.r) / n - 2))
          return <rect key={d.date} x={x(i) - bw / 2} y={H + BAR - 8 - bh} width={bw} height={bh} rx={Math.min(2, bw / 2)} fill={C.evidence} />
        })}
        {narrative?.emotion && <text x={PAD.l} y={H + 12} className="tick">근거 기록 수 (많은 날 {maxEv}건)</text>}
        <line x1={PAD.l} x2={W - PAD.r} y1={H + BAR - 8} y2={H + BAR - 8} stroke={C.axis} strokeWidth={1} opacity={narrative?.emotion ? 1 : 0} />
        {ticks.map(({ d, i }) => <text key={d.date} x={x(i)} y={H - 8} textAnchor="middle" className="tick">{koDate(d.date)}</text>)}
        {h && hover != null && (
          <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H + BAR - 8} stroke={C.sub} strokeWidth={1} opacity={0.6} />
        )}
      </svg>
      {h && hover != null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <strong>{h.valence >= 0 ? '+' : ''}{h.valence.toFixed(2)}</strong> 정서 (7일 평균)
          <br /><span>{koDate(h.date)} · 기록 {h.records}건{h.sparse ? ' · 기록이 적은 날' : ''}</span>
          {(ev.get(h.date) ?? 0) > 0 && <><br /><span><i className="key-line" style={{ background: C.evidence }} /> 근거 {ev.get(h.date)}건</span></>}
        </div>
      )}
      {turns.length > 0 && (
        <ol className="turn-list">
          {turns.map((t, k) => (
            <li key={t.date}><b>{k + 1}</b> {koDate(t.date)} 무렵 · {t.columns.map((c) => `${c.series} ${c.direction}`).join(', ')}</li>
          ))}
        </ol>
      )}
      <details className="table-view">
        <summary>표로 보기</summary>
        <table>
          <thead><tr><th>날짜</th><th>정서 (7일 평균)</th><th>그날 정서</th><th>기록 수</th>{narrative && <th>근거 수</th>}</tr></thead>
          <tbody>{series.map((d) => (
            <tr key={d.date}><td>{d.date}</td><td>{d.valence.toFixed(3)}</td><td>{d.valence_raw == null ? '기록 없음' : d.valence_raw.toFixed(3)}</td>
              <td>{d.records}</td>{narrative && <td>{ev.get(d.date) ?? 0}</td>}</tr>))}</tbody>
        </table>
      </details>
    </div>
  )
}

function TopicBars({ narrative, topics }: { narrative: NarrativeObject | null; topics: Topic[] }) {
  const rows = narrative?.clusters?.length
    ? narrative.clusters.map((c) => ({ label: c.label, value: c.share, text: `${Math.round(c.share * 100)}%` }))
    : topics.slice(0, 7).map((t) => ({ label: t.label, value: t.size, text: `${t.size}건` }))
  const max = Math.max(...rows.map((r) => r.value), 0.0001)
  if (!rows.length) return <p className="insight-sub">묶음이 아직 없습니다. <code>virtueme build</code> 를 다시 돌려 주세요.</p>
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="bar-row" key={r.label} title={`${r.label} ${r.text}`}>
          <span className="bar-label">{r.label}</span>
          <span className="bar-track"><i style={{ width: `${(r.value / max) * 100}%`, background: C.line }} /></span>
          <span className="bar-value">{r.text}</span>
        </div>
      ))}
    </div>
  )
}

function Dumbbell({ title, mix }: { title: string; mix: Record<string, { evidence: number; all: number }> }) {
  const rows = Object.entries(mix)
  const W = 400, row = 26, L = 80, R = 46
  const x = (v: number) => L + v * (W - L - R)
  return (
    <div className="dumbbell">
      <div className="legend small">
        <span>{title}</span>
        <span><i className="key-dot" style={{ background: C.line }} />이 키워드 근거</span>
        <span><i className="key-dot" style={{ background: C.gray }} />전체 기록</span>
      </div>
      <svg viewBox={`0 0 ${W} ${rows.length * row + 8}`} className="chart" role="img" aria-label={`${title}: 근거 기록과 전체 기록의 비율`}>
        {rows.map(([k, v], i) => {
          const cy = 12 + i * row
          return (
            <g key={k}>
              <text x={L - 10} y={cy + 4} textAnchor="end" className="tick label">{k}</text>
              <line x1={x(0)} x2={x(1)} y1={cy} y2={cy} stroke={C.grid} strokeWidth={1} />
              <line x1={x(Math.min(v.evidence, v.all))} x2={x(Math.max(v.evidence, v.all))} y1={cy} y2={cy} stroke={C.axis} strokeWidth={2} />
              <circle cx={x(v.all)} cy={cy} r={4.5} fill={C.gray} stroke="#15161d" strokeWidth={2}><title>전체 {Math.round(v.all * 100)}%</title></circle>
              <circle cx={x(v.evidence)} cy={cy} r={4.5} fill={C.line} stroke="#15161d" strokeWidth={2}><title>근거 {Math.round(v.evidence * 100)}%</title></circle>
              <text x={W - R + 8} y={cy + 4} className="tick">{Math.round(v.evidence * 100)}%</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

const GROUP: Record<string, { name: string; color: string }> = {
  person: { name: '사람·동물', color: C.line }, animal: { name: '사람·동물', color: C.line },
  place: { name: '장소', color: C.evidence }, activity: { name: '활동', color: C.aqua },
}
const groupOf = (t: string) => GROUP[t] ?? { name: '그 밖', color: C.gray }

function RelationGraph({ graph }: { graph: { nodes: GraphNode[]; edges: GraphEdge[] } }) {
  const [hover, setHover] = useState<string | null>(null)
  const S = 420, cx = S / 2, cy = S / 2
  const layout = useMemo(() => {
    const weight = (n: GraphNode) => n.focus ?? n.count
    const center = graph.nodes.find((n) => n.name === '나' && n.type === 'person')
    const rest = graph.nodes.filter((n) => n !== center).sort((a, b) => weight(b) - weight(a))
    const order = ['사람·동물', '장소', '활동', '그 밖']
    const groups = order.map((g) => rest.filter((n) => groupOf(n.type).name === g))
    const total = Math.max(1, rest.length)
    const wmax = Math.max(1, ...rest.map(weight))
    const pos = new Map<string, { x: number; y: number; r: number }>()
    if (center) pos.set(center.id, { x: cx, y: cy, r: 10 })
    let a0 = -Math.PI / 2
    groups.forEach((g) => {
      const span = (g.length / total) * Math.PI * 2
      g.forEach((n, i) => {
        const a = a0 + (span * (i + 0.5)) / Math.max(1, g.length)
        const w = weight(n) / wmax
        const rad = (S / 2 - 34) * (0.42 + 0.55 * (1 - Math.sqrt(w))) + (i % 2) * 10
        pos.set(n.id, { x: cx + Math.cos(a) * rad, y: cy + Math.sin(a) * rad, r: 4 + 8 * Math.sqrt(w) })
      })
      a0 += span
    })
    // 이름표 자리: 위 → 아래 → 오른쪽 → 왼쪽 순서로, 먼저 놓인 이름표·다른 점과 겹치지 않는 곳에 둔다
    const labels = new Map<string, { x: number; y: number; anchor: 'middle' | 'start' | 'end' }>()
    const boxes: { x0: number; y0: number; x1: number; y1: number }[] = []
    const textW = (t: string) => [...t].reduce((s, ch) => s + (/[가-힣]/.test(ch) ? 10.5 : 6), 0)
    const labeled = graph.nodes.filter((n) => { const p = pos.get(n.id); return p && (p.r >= 7 || n === center) })
    labeled.sort((a, b) => (pos.get(b.id)!.r - pos.get(a.id)!.r))
    for (const n of labeled) {
      const p = pos.get(n.id)!, w = textW(n.name), h = 12
      const tries = [
        { x: p.x, y: p.y - p.r - 5, anchor: 'middle' as const, box: { x0: p.x - w / 2, y0: p.y - p.r - 5 - h, x1: p.x + w / 2, y1: p.y - p.r - 5 } },
        { x: p.x, y: p.y + p.r + 13, anchor: 'middle' as const, box: { x0: p.x - w / 2, y0: p.y + p.r + 1, x1: p.x + w / 2, y1: p.y + p.r + 13 } },
        { x: p.x + p.r + 4, y: p.y + 4, anchor: 'start' as const, box: { x0: p.x + p.r + 4, y0: p.y - 8, x1: p.x + p.r + 4 + w, y1: p.y + 4 } },
        { x: p.x - p.r - 4, y: p.y + 4, anchor: 'end' as const, box: { x0: p.x - p.r - 4 - w, y0: p.y - 8, x1: p.x - p.r - 4, y1: p.y + 4 } },
      ]
      const hit = (a: { x0: number; y0: number; x1: number; y1: number }, b: typeof a) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0
      const dots = [...pos.entries()].filter(([id]) => id !== n.id).map(([, q]) => ({ x0: q.x - q.r, y0: q.y - q.r, x1: q.x + q.r, y1: q.y + q.r }))
      const free = tries.find((t) => !boxes.some((b) => hit(t.box, b)) && !dots.some((b) => hit(t.box, b)))
      if (!free) continue // 네 자리 모두 겹치면 이름표를 빼고, 가리키면 보이게 둔다
      boxes.push(free.box)
      labels.set(n.id, { x: free.x, y: free.y, anchor: free.anchor })
    }
    return { pos, labels }
  }, [graph, cx, cy])
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const wmaxE = Math.max(1, ...graph.edges.map((e) => e.weight))
  const hn = hover ? byId.get(hover) : null
  if (!graph.nodes.length) return <p className="insight-sub">개체가 아직 없습니다. 키가 있을 때 <code>virtueme build</code> 가 뽑습니다.</p>
  return (
    <div className="graph-wrap">
      <div className="legend small">
        {['사람·동물', '장소', '활동', '그 밖'].map((g) => (
          <span key={g}><i className="key-dot" style={{ background: g === '장소' ? C.evidence : g === '활동' ? C.aqua : g === '그 밖' ? C.gray : C.line }} />{g}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${S} ${S}`} className="chart graph" role="img" aria-label="사람·장소·활동 관계 그래프">
        {graph.edges.map((e) => {
          const a = layout.pos.get(e.source), b = layout.pos.get(e.target); if (!a || !b) return null
          const on = hover && (e.source === hover || e.target === hover)
          return <line key={e.source + e.target} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#ffffff"
            strokeOpacity={on ? 0.55 : 0.05 + 0.18 * (e.weight / wmaxE)} strokeWidth={on ? 1.6 : 1} />
        })}
        {graph.nodes.map((n) => {
          const p = layout.pos.get(n.id); if (!p) return null
          const lab = layout.labels.get(n.id)
          return (
            <g key={n.id} onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)} tabIndex={0}
              onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}>
              <circle cx={p.x} cy={p.y} r={Math.max(p.r, 12)} fill="transparent" />
              <circle cx={p.x} cy={p.y} r={p.r} fill={n.name === '나' ? '#ffffff' : groupOf(n.type).color} stroke="#15161d" strokeWidth={2} />
              {lab ? <text x={lab.x} y={lab.y} textAnchor={lab.anchor} className="node-label">{n.name}</text>
                : hover === n.id && <text x={p.x} y={p.y - p.r - 5} textAnchor="middle" className="node-label">{n.name}</text>}
            </g>
          )
        })}
      </svg>
      {hn && (
        <div className="graph-tip">
          <strong>{hn.name}</strong> <span>{hn.type_ko} · 기록 {hn.count}건{hn.focus != null ? ` · 근거 ${hn.focus}건` : ''}</span>
          <br /><span>{koDate(hn.first)} ~ {koDate(hn.last)}{hn.valence != null ? ` · 정서 평균 ${hn.valence.toFixed(2)}` : ''}</span>
          {hn.aliases?.length ? <><br /><span>함께 센 이름: {hn.aliases.join(', ')}</span></> : null}
        </div>
      )}
    </div>
  )
}

function Gaps({ gaps, people }: { gaps: Gap[] | null; people: People }) {
  if (gaps == null) return <p className="insight-sub">자기 서술을 찾는 중입니다 (처음 한 번은 1분쯤 걸립니다).</p>
  if (!gaps.length) return <p className="insight-sub">스스로를 단정한 말을 찾지 못했습니다.</p>
  return (
    <ul className="gaps">
      {gaps.slice(0, 6).map((g) => (
        <li key={g.claim + g.date}>
          <span className={`gap-tag gap-${g.verdict === '간극' ? 'gap' : g.verdict === '일치' ? 'match' : 'hold'}`}>{g.verdict}</span>
          <p>{relabel(g.summary, people)}</p>
        </li>
      ))}
    </ul>
  )
}
