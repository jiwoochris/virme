import { useEffect, useState } from 'react'
import { coreFetch } from './coreApi'
import { koDate, relabel } from './narrative'
import { ShareDialog } from './SharePanel'

/* 소설 재료 컬럼(GET /columns, virtueme.columns/1) 화면: 발견 문장, 인물, 장소, 생활 리듬, 장르 추천, 채워진 컬럼.
   값은 모두 core/columns.py 가 센 통계다. 색은 인사이트 화면과 같은 역할 색만 쓴다. */

const C = { line: '#3987e5', evidence: '#d95926', aqua: '#199e70', gray: '#6f6e69' }

type People = Record<string, string | null>
export type Discovery = { id?: string; column: string; text: string; evidence: string[] }
export type CatalogItem = { key: string; element: string; name: string; sources: string[]; available: boolean; how_to_get: string }
export type CastRow = {
  alias: string; relation: string | null; family?: boolean; turns: number; active_days: number; first: string; last: string
  monthly: Record<string, number>; night_share: number; roles: string[]; reply_min_mine?: number | null
}
export type Place = { key: string; name: string; named: boolean; visits: number; hours?: number; first: string; last: string; time_band?: string; km?: number }
type Trip = { first: string; last: string; places: string[]; km: number }
type Merchant = { name: string; visits: number; amount: number }
export type TurningPoint = { date: string; columns: { series: string; direction: string; delta_sd?: number }[]; strength: number }
export type GenreHint = { genre: string; index: number; score: number; reason: string }
export type Columns = {
  schema?: string
  data_scope?: { records: number; sources?: Record<string, number> }
  catalog?: CatalogItem[]
  cast?: CastRow[]
  places?: { anchors?: Place[]; regular?: Place[]; first_visits?: Place[]; far?: Place[]; trips?: Trip[]; merchants?: Merchant[] }
  rhythm?: {
    night_share?: number; night_share_monthly?: Record<string, number>; steps_monthly?: Record<string, number>
    sleep_min_monthly?: Record<string, number>; bedtime_first_third?: number | null; bedtime_last_third?: number | null
    workouts?: Record<string, number>; time_band?: string
  }
  turning_points?: TurningPoint[]
  discoveries?: Discovery[]
  genre_hint?: GenreHint[]
}

export const SOURCE_KO: Record<string, string> = {
  kakao: '카카오톡', sms: '문자', instagram: '인스타그램', photo: '사진', maps: '지도 타임라인', card: '카드', health: '건강',
  youtube: '유튜브', netflix: '넷플릭스', spotify: '스포티파이', search: '구글 검색',
}
const ELEMENT_OF: Record<string, string> = { cast: '인물', place: '배경', rhythm: '리듬', taste: '취향', turn: '전환점' }
const elementOf = (column: string) => ELEMENT_OF[column.split('.')[0]] ?? '기타'
const monthKo = (ym: string) => `${Number(ym.slice(5, 7))}월`
const period = (a: string, b: string) => (a === b ? koDate(a) : `${koDate(a)} ~ ${koDate(b)}`)
const clock = (h: number) => {
  const m = Math.round(h * 60) % (24 * 60)
  return `${Math.floor(m / 60)}시 ${String(m % 60).padStart(2, '0')}분`
}
/** 전환점 번호를 날짜 순으로 매긴다. 정서 곡선과 발견 카드가 같은 번호를 쓴다 */
export const strongTurns = (cols: Columns | null) =>
  (cols?.turning_points ?? []).filter((t) => t.strength >= 2).sort((a, b) => a.date.localeCompare(b.date))

type RecordView = { id: string; date_ko: string; weekday: string; time: string; source: string; text?: string; photo_tags?: string[]; room?: string }

function EvidenceList({ ids, people }: { ids: string[]; people: People }) {
  const [rows, setRows] = useState<RecordView[] | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let cancelled = false
    Promise.all(ids.slice(0, 8).map((id) => coreFetch(`/records/${id}`).then((r) => (r.ok ? (r.json() as Promise<RecordView>) : null))))
      .then((got) => !cancelled && setRows(got.filter((x): x is RecordView => Boolean(x))))
      .catch(() => !cancelled && setFailed(true))
    return () => { cancelled = true }
  }, [ids])
  if (failed) return <p className="disc-records-note">기록을 불러오지 못했습니다.</p>
  if (!rows) return <p className="disc-records-note">기록을 불러오는 중</p>
  return (
    <ul className="disc-records">
      {rows.map((r) => (
        <li key={r.id}>
          <span className="disc-when">{r.date_ko} ({r.weekday}) {r.time} · {r.source}</span>
          <span>{relabel(r.text || `사진 · ${(r.photo_tags ?? []).join(', ')}`, people).replace(/\n/g, ' / ')}</span>
        </li>
      ))}
      {ids.length > rows.length && <li className="disc-records-note">근거 {ids.length}건 가운데 {rows.length}건을 보여 드립니다.</li>}
    </ul>
  )
}

function Discoveries({ cols, people, onShared }: { cols: Columns; people: People; onShared?: () => void }) {
  const [open, setOpen] = useState<number | null>(null)
  const [sharing, setSharing] = useState<Discovery | null>(null)
  const list = cols.discoveries ?? []
  const turns = strongTurns(cols)
  if (!list.length) return <p className="insight-sub">아직 발견 문장이 없습니다. 기록을 더 넣으면 생깁니다.</p>
  return (
    <>
    <ol className="discoveries">
      {list.map((d, i) => {
        const turnNo = d.column === 'turn.points' ? turns.findIndex((t) => d.text.startsWith(koDate(t.date))) : -1
        return (
          <li key={i} className={open === i ? 'open' : ''}>
            <span className="disc-tag">{elementOf(d.column)}{turnNo >= 0 ? ` ${turnNo + 1}` : ''}</span>
            <p>{relabel(d.text, people)}</p>
            <span className="disc-actions">
              {d.evidence.length > 0 ? (
                <button type="button" className="disc-count" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                  근거 {d.evidence.length}건
                </button>
              ) : <span className="disc-count static">변화점</span>}
              {d.id && <button type="button" className="disc-share" onClick={() => setSharing(d)}>카드 만들기</button>}
            </span>
            {open === i && <div className="disc-detail"><EvidenceList ids={d.evidence} people={people} /></div>}
          </li>
        )
      })}
    </ol>
    {sharing?.id && (
      <ShareDialog request={{ kind: 'discovery', summary_id: sharing.id }} label={`발견 · ${elementOf(sharing.column)}`}
        onClose={() => setSharing(null)} onShared={onShared} />
    )}
    </>
  )
}

function Cast({ cast }: { cast: CastRow[] }) {
  const months = [...new Set(cast.flatMap((c) => Object.keys(c.monthly)))].sort()
  const max = Math.max(1, ...cast.flatMap((c) => Object.values(c.monthly)))
  if (!cast.length) return <p className="insight-sub">대화 기록이 없습니다.</p>
  return (
    <div className="cast">
      {cast.slice(0, 8).map((c) => (
        <div className="cast-row" key={c.alias}>
          <div className="cast-name">
            <strong>{c.relation ?? c.alias}</strong>
            {c.relation && <small>{c.alias}</small>}
            {c.roles.map((r) => <span key={r} className={`role-chip ${r.includes('새로') ? 'new' : r.includes('끊') || r.includes('멀어') ? 'fade' : ''}`}>{r}</span>)}
          </div>
          <div className="mini-bars" role="img" aria-label={months.map((m) => `${monthKo(m)} ${c.monthly[m] ?? 0}번`).join(', ')}>
            {months.map((m) => (
              <span key={m} title={`${monthKo(m)} ${c.monthly[m] ?? 0}번`}>
                <i style={{ height: `${((c.monthly[m] ?? 0) / max) * 100}%` }} />
                <em>{monthKo(m)}</em>
              </span>
            ))}
          </div>
          <div className="cast-nums">
            <span><b>{c.turns}</b>번</span>
            <span><b>{c.active_days}</b>일</span>
            <span>밤 <b>{Math.round(c.night_share * 100)}%</b></span>
          </div>
        </div>
      ))}
      <p className="cast-foot">막대는 달마다 나눈 대화 수, 오른쪽은 대화 수·대화한 날·밤 11시 이후 비율입니다.{cast.filter((c) => c.roles.some((r) => r.includes('새로'))).map((c) => ` ${c.relation ?? c.alias}의 첫 대화는 ${koDate(c.first)}입니다.`).join('')}</p>
    </div>
  )
}

function Places({ places }: { places: NonNullable<Columns['places']> }) {
  const top = [...(places.anchors ?? []), ...(places.regular ?? [])].slice(0, 6)
  const trip = (places.trips ?? [])[0]
  const tripNames = trip ? [...new Set(trip.places.filter((n) => n !== '이름 없는 장소' && !n.endsWith('찍은 곳')))].slice(0, 4) : []
  const merchants = (places.merchants ?? []).slice(0, 4)
  return (
    <div className="places">
      {top.length > 0 && (
        <ul className="place-list">
          {top.map((p) => (
            <li key={p.key}>
              <strong>{p.name}</strong>
              <span>{p.visits}일 · {p.time_band ?? ''} · {period(p.first, p.last)}</span>
            </li>
          ))}
        </ul>
      )}
      {trip && (
        <p className="place-note"><b>멀리 간 날</b> {period(trip.first, trip.last)} · 집에서 {trip.km}km{tripNames.length ? ` · ${tripNames.join(', ')}` : ''}</p>
      )}
      {merchants.length > 0 && (
        <p className="place-note"><b>자주 결제한 가게</b> {merchants.map((m) => `${m.name} ${m.visits}번`).join(', ')}</p>
      )}
      {!top.length && !trip && !merchants.length && <p className="insight-sub">지도 타임라인·사진 위치·카드 기록이 없습니다.</p>}
    </div>
  )
}

function MonthBars({ title, data, fmt, color }: { title: string; data: Record<string, number>; fmt: (v: number) => string; color: string }) {
  const rows = Object.entries(data).sort(([a], [b]) => a.localeCompare(b))
  const max = Math.max(...rows.map(([, v]) => v), 0.0001)
  return (
    <div className="month-bars">
      <div className="month-title">{title}</div>
      <div className="bars">
        {rows.map(([m, v]) => (
          <div className="bar-row" key={m}>
            <span className="bar-label">{monthKo(m)}</span>
            <span className="bar-track"><i style={{ width: `${(v / max) * 100}%`, background: color }} /></span>
            <span className="bar-value">{fmt(v)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Rhythm({ r }: { r: NonNullable<Columns['rhythm']> }) {
  const hasSleep = r.sleep_min_monthly && Object.keys(r.sleep_min_monthly).length > 0
  const hasSteps = r.steps_monthly && Object.keys(r.steps_monthly).length > 0
  const workouts = Object.entries(r.workouts ?? {})
  return (
    <div className="rhythm">
      {r.night_share_monthly && <MonthBars title="밤 11시 이후 기록 비율" data={r.night_share_monthly} fmt={(v) => `${Math.round(v * 100)}%`} color={C.line} />}
      {hasSteps && <MonthBars title="하루 평균 걸음 수" data={r.steps_monthly!} fmt={(v) => v.toLocaleString()} color={C.aqua} />}
      {hasSleep && <MonthBars title="하루 평균 수면" data={r.sleep_min_monthly!} fmt={(v) => `${Math.floor(v / 60)}시간 ${v % 60}분`} color={C.gray} />}
      {(r.bedtime_first_third != null && r.bedtime_last_third != null) && (
        <p className="place-note"><b>평균 잠든 시각</b> 기간 처음 무렵 {clock(r.bedtime_first_third)}, 끝 무렵 {clock(r.bedtime_last_third)}</p>
      )}
      {workouts.length > 0 && <p className="place-note"><b>운동</b> {workouts.map(([k, v]) => `${k} ${v}번`).join(', ')}</p>}
      {!hasSteps && !hasSleep && <p className="place-note">걸음·수면은 삼성 헬스나 애플 건강 내보내기를 넣으면 나옵니다.</p>}
    </div>
  )
}

function Genres({ hints }: { hints: GenreHint[] }) {
  const max = Math.max(...hints.map((h) => h.score), 0.0001)
  return (
    <ol className="genres">
      {hints.map((h, i) => (
        <li key={h.genre} className={i === 0 ? 'top' : ''}>
          <div className="bar-row">
            <span className="bar-label">{h.genre}{i === 0 && <span className="role-chip new">추천</span>}</span>
            <span className="bar-track"><i style={{ width: `${(h.score / max) * 100}%`, background: i === 0 ? C.evidence : C.gray }} /></span>
            <span className="bar-value">{h.score.toFixed(2)}</span>
          </div>
          <p>{h.reason}</p>
        </li>
      ))}
    </ol>
  )
}

function Catalog({ items, sources }: { items: CatalogItem[]; sources: Record<string, number> }) {
  const filled = items.filter((c) => c.available).length
  const groups = [...new Set(items.map((c) => c.element))]
  return (
    <>
      <p className="insight-sub">컬럼 {items.length}개 가운데 {filled}개가 채워졌습니다. 비어 있는 컬럼에는 파일을 받는 방법이 함께 나옵니다. 그 파일을 <b>내 기록 넣기</b>에 올리면 채워집니다.</p>
      <div className="catalog">
        {groups.map((g) => (
          <div key={g} className="catalog-group">
            <div className="month-title">{g}</div>
            <ul>
              {items.filter((c) => c.element === g).map((c) => (
                <li key={c.key} className={c.available ? 'on' : 'off'}>
                  <i aria-hidden="true">{c.available ? '●' : '○'}</i>
                  <span className="cat-name">{c.name}</span>
                  <span className="cat-src">{c.available
                    ? (c.sources.filter((s) => sources[s]).map((s) => `${SOURCE_KO[s] ?? s} ${sources[s].toLocaleString()}건`).join(' · ') || '모든 기록')
                    : <>비어 있음 · {c.how_to_get}</>}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </>
  )
}

/** 인사이트 화면의 "발견" 묶음. 카드 여러 장을 돌려주므로 .insight 그리드 안에 그대로 넣는다 */
export function ColumnsSections({ cols, people, importUrl, onShared }: { cols: Columns | null; people: People; importUrl: string | null; onShared?: () => void }) {
  if (!cols || !cols.schema) return null
  return (
    <>
      <div className="insight-heading" id="discover">
        <h2>발견</h2>
        <p>키워드 없이 기록 전체에서 센 값입니다. 대화·장소·시각·결제·시청 기록처럼 따로 의식하지 않고 남은 흔적에서 나왔습니다.</p>
      </div>
      <section className="insight-card wide" data-testid="discoveries">
        <h2>기억은 흐리지만 기록에는 남은 것</h2>
        <p className="insight-sub">문장마다 숫자와 근거 기록이 붙어 있습니다. 근거 수를 누르면 그 기록을 보여 드립니다. 이 문장들은 노벨 모드가 이야기를 쓸 때 배경으로 들어갑니다. <b>카드 만들기</b>를 누르면 SNS 에 올릴 이미지 카드와 공유 링크를 만듭니다.</p>
        <Discoveries cols={cols} people={people} onShared={onShared} />
      </section>
      <section className="insight-card">
        <h2>등장인물</h2>
        <p className="insight-sub">대화 상대를 대화 수 순으로 놓았습니다. 역할은 처음 등장한 때와 마지막 대화 뒤 공백으로 정합니다.</p>
        <Cast cast={cols.cast ?? []} />
      </section>
      <section className="insight-card">
        <h2>자주 간 곳</h2>
        <p className="insight-sub">지도 타임라인과 사진 위치를 장소별로 묶어 간 날을 셌습니다. 좌표는 이 컴퓨터 밖으로 나가지 않습니다.</p>
        {cols.places ? <Places places={cols.places} /> : <p className="insight-sub">장소 기록이 없습니다.</p>}
        {importUrl && <p className="place-note">이름 없는 장소는 <a href={`${importUrl}#places`} target="_blank" rel="noreferrer">내 기록 넣기 → 장소 이름 붙이기</a>에서 부를 이름을 정합니다.</p>}
      </section>
      <section className="insight-card">
        <h2>생활 리듬</h2>
        <p className="insight-sub">달마다 평균을 냈습니다.</p>
        {cols.rhythm ? <Rhythm r={cols.rhythm} /> : null}
      </section>
      <section className="insight-card">
        <h2>장르 추천</h2>
        <p className="insight-sub">기록의 모양으로 장르마다 점수를 매겼습니다. 노벨 모드에서 장르를 고를 때 참고하는 기본값입니다.</p>
        <Genres hints={cols.genre_hint ?? []} />
      </section>
      {cols.catalog && cols.catalog.length > 0 && (
        <section className="insight-card wide" data-testid="catalog">
          <h2>어떤 컬럼이 채워졌나</h2>
          <Catalog items={cols.catalog} sources={cols.data_scope?.sources ?? {}} />
        </section>
      )}
    </>
  )
}
