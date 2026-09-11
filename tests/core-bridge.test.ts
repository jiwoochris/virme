import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// public/virtueme-core-bridge.js 는 Storyseed HTML 이 <script> 로 읽는 파일이라 모듈이 아니다. 최소한의 전역만 두고 실행한다
type VX = {
  unalias: (html: string, people?: Record<string, string | null>) => string
  badCount: (stats: Record<string, number>) => number
  saveStory: (kw: string, entry: Record<string, unknown>) => void
  loadStory: (kw: string) => { html: string; saved: string } | null
}
const loadBridge = (): VX => {
  const store = new Map<string, string>()
  const g = globalThis as Record<string, unknown>
  g.window = {}
  g.document = { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} } }
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) }
  new Function(readFileSync('public/virtueme-core-bridge.js', 'utf8'))()
  return (g.window as { VX: VX }).VX
}

describe('core bridge', () => {
  const VX = loadBridge()

  it('turns pseudonyms and masked names into readable words', () => {
    const people = { 지인D: '회사 동기' }
    expect(VX.unalias('지인D아 고마워, 지인D가 알려줬어', people)).toBe('회사 동기 고마워, 회사 동기가 알려줬어')
    expect(VX.unalias('(나)이가 강아지를 데려온 거 맞지?', people)).toBe('내가 강아지를 데려온 거 맞지?')
    expect(VX.unalias('(누군가)님도 수고하셨어요', people)).toBe('그 사람도 수고하셨어요')
  })

  it('counts rule failures, context mismatches, and loose wrong dates as bad values', () => {
    expect(VX.badCount({ ok: 3, failed: 1, context_mismatch: 2, loose_unsupported: 1 })).toBe(4)
  })

  it('keeps verified stories per keyword for the demo', () => {
    VX.saveStory('콩이', { html: '<p>이야기</p>', narrative_id: '6b7cf889' })
    expect(VX.loadStory('콩이')?.html).toBe('<p>이야기</p>')
    expect(VX.loadStory('러닝')).toBeNull()
  })
})
