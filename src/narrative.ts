import { normalizePersonalData, derivePersonalFeatures, type NormalizedPersonalData, type PersonalFeatures } from './personalData'

/** core 서버의 서사 오브젝트 `virtueme.narrative/1` 가운데 화면이 쓰는 부분 (전체 정의는 core/schema/narrative.v1.schema.json). */
export type NarrativeSignals = {
  stability: number
  novelty: number
  conflict: number
  uncertainty: number
  possibility: number
  activity: number
  confidence: number
}

export type NarrativeObject = {
  schema: 'virtueme.narrative/1'
  narrative_id: string
  keyword: string
  evidence_grade: '강한 근거' | '약한 근거' | '근거 부족'
  grade_basis: { strong_records: number; weak_records?: number; strong_days: number; active_days: number; method?: string }
  data_scope: { records: number; date_range: [string, string] | null }
  stats?: { distinct_days: number; first_date: string; last_date: string; time_band?: string; peak_date?: string }
  emotion?: { series: { date: string; valence: number; arousal: number; evidence?: number; sparse: boolean }[]; mean: number | null; baseline: number | null }
  change_points?: { date: string; direction: '상승' | '하강'; delta: number; near_evidence?: boolean }[]
  clusters?: { id: string; label: string; share: number; size?: number; keywords?: string[] }[]
  mood_mix?: Record<string, { evidence: number; all: number }>
  social_mix?: Record<string, { evidence: number; all: number }>
  signals: NarrativeSignals
  message?: string
}

export const NARRATIVE_MESSAGE = 'virtueme:narrative'

export type NarrativeMessage = { type: typeof NARRATIVE_MESSAGE; narrative: NarrativeObject; people?: Record<string, string | null> }

export const isNarrativeMessage = (data: unknown): data is NarrativeMessage =>
  typeof data === 'object' && data !== null && (data as { type?: unknown }).type === NARRATIVE_MESSAGE &&
  typeof (data as { narrative?: { narrative_id?: unknown } }).narrative?.narrative_id === 'string'

/** 서사 오브젝트의 signals 를 비주얼 모드가 쓰는 PersonalFeatures 로. 이름이 같게 설계돼 있어 그대로 옮긴다. */
export const signalsToFeatures = (signals: NarrativeSignals, previous?: NormalizedPersonalData): PersonalFeatures =>
  derivePersonalFeatures(normalizePersonalData(signals), previous)

export const narrativeLabel = (no: NarrativeObject) => `${no.keyword} · ${no.evidence_grade} · #${no.narrative_id}`

export const koDate = (iso: string) => {
  const [, m, d] = iso.split('-').map(Number)
  return `${m}월 ${d}일`
}

const ALIAS_RE = /지인([A-Z]{1,2})(이가|이랑|에게|한테|이야|가|이|은|는|을|를|와|과|아|야|랑|의|도|만)?/g
const hasJong = (w: string) => {
  const c = w.charCodeAt(w.length - 1) - 0xac00
  return c >= 0 && c <= 11171 && c % 28 !== 0
}
const JO = (w: string, a: string, b: string) => (hasJong(w) ? a : b)

/** 관계가 붙은 가명("지인D")만 관계("회사 동기")로 바꾸고 조사를 받침에 맞춘다. 부르는 말(아·야)은 뗀다. */
export const relabel = (text: string, people?: Record<string, string | null> | null) =>
  text.replace(ALIAS_RE, (m, id: string, pt: string | undefined) => {
    const who = people?.[`지인${id}`]
    if (!who) return m
    if (!pt) return who
    const fix: Record<string, string> = {
      가: JO(who, '이', '가'), 이: JO(who, '이', '가'), 이가: JO(who, '이', '가'), 은: JO(who, '은', '는'), 는: JO(who, '은', '는'),
      을: JO(who, '을', '를'), 를: JO(who, '을', '를'), 와: JO(who, '과', '와'), 과: JO(who, '과', '와'), 랑: JO(who, '이랑', '랑'),
      이랑: JO(who, '이랑', '랑'), 아: '', 야: '', 이야: JO(who, '이야', '야'),
    }
    return who + (pt in fix ? fix[pt] : pt)
  })
