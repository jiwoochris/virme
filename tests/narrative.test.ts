import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { isNarrativeMessage, narrativeLabel, relabel, signalsToFeatures, type NarrativeObject } from '../src/narrative'

const narrative = {
  schema: 'virtueme.narrative/1',
  narrative_id: '1a2b3c4d',
  keyword: '러닝',
  evidence_grade: '강한 근거',
  grade_basis: { strong_records: 35, strong_days: 23, active_days: 88 },
  data_scope: { records: 749, date_range: ['2026-06-01', '2026-08-29'] },
  signals: { stability: 0.52, novelty: 0.86, conflict: 0.6, uncertainty: 0.05, possibility: 0.51, activity: 0.52, confidence: 0.83 },
} satisfies NarrativeObject

describe('세 화면이 같은 서사 오브젝트를 쓴다', () => {
  it('노벨 iframe 이 보내는 메시지만 받는다', () => {
    expect(isNarrativeMessage({ type: 'virtueme:narrative', narrative })).toBe(true)
    expect(isNarrativeMessage({ type: 'virtueme:narrative', narrative: {} })).toBe(false)
    expect(isNarrativeMessage({ type: 'other', narrative })).toBe(false)
  })

  it('비주얼 모드 신호는 서사 오브젝트 signals 와 같은 값이다', () => {
    const f = signalsToFeatures(narrative.signals)
    expect(f.signals).toEqual([0.52, 0.86, 0.6, 0.05, 0.51])
    expect(f.activity).toBe(0.52)
    expect(f.confidence).toBe(0.83)
    expect(f.change).toBe(0)
    const next = signalsToFeatures({ ...narrative.signals, novelty: 0.36 }, { signals: f.signals, activity: f.activity, confidence: f.confidence })
    expect(next.change).toBeCloseTo(0.1)
  })

  it('세 화면에 같은 번호를 표시한다', () => {
    expect(narrativeLabel(narrative)).toBe('러닝 · 강한 근거 · #1a2b3c4d')
    const html = readFileSync(new URL('../public/storyseed-novel.html', import.meta.url), 'utf8')
    expect(html).toContain("postMessage({type:'virtueme:narrative'")
    expect(html).toContain('narrative_id')
  })
})

describe('relabel', () => {
  it('replaces only pseudonyms that have a relation and fixes particles', () => {
    const people = { 지인D: '회사 동기', 지인E: '러닝 크루 친구', 지인F: null }
    expect(relabel('지인D아 고마워 지인D가 소개해줘서', people)).toBe('회사 동기 고마워 회사 동기가 소개해줘서')
    expect(relabel('지인E는 먼저 갔고 지인F랑 남았어', people)).toBe('러닝 크루 친구는 먼저 갔고 지인F랑 남았어')
    expect(relabel('엄마랑 산책', people)).toBe('엄마랑 산책')
  })
})
