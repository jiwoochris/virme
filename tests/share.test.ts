import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { groupByDay, koDateTime, pngName, reactionSummary, shareLink, shareSummary, type Share } from '../src/share'

const labels = { like: '❤️ 좋아요', same: '🙌 나도 그래요', cheer: '👏 응원해요', wow: '😮 몰랐어요' }
const make = (id: string, created: string, extra: Partial<Share> = {}): Share => ({
  id, kind: 'discovery', kind_ko: '발견', created, url: `/s/${id}`, files: { 'feed.png': `/s/${id}/feed.png`, 'story.png': `/s/${id}/story.png` },
  text: '밤 11시 이후 기록이 50%에서 4%로 줄었습니다.',
  reactions: { counts: { like: 0, same: 0, cheer: 0, wow: 0 }, total: 0, labels, recent: [] }, ...extra,
})

describe('공유 카드 화면 도우미', () => {
  it('core 주소가 상대 주소여도 붙여 넣을 수 있는 링크를 만든다', () => {
    expect(shareLink('/api/core', '/s/0123456789ab', 'http://127.0.0.1:5173/')).toBe('http://127.0.0.1:5173/api/core/s/0123456789ab')
    expect(shareLink('http://127.0.0.1:8767/', '/s/0123456789ab', 'http://localhost:5173/')).toBe('http://127.0.0.1:8767/s/0123456789ab')
  })

  it('반응은 많은 순으로, 없는 것은 빼고 적는다', () => {
    const s = make('a', '2026-10-03T10:00:00', { reactions: { counts: { like: 1, same: 3, cheer: 0, wow: 0 }, total: 4, labels, recent: [] } })
    expect(reactionSummary(s)).toBe('🙌 나도 그래요 3 · ❤️ 좋아요 1')
    expect(reactionSummary(make('b', '2026-10-03T10:00:00'))).toBe('')
  })

  it('날짜별로 묶고 최근이 위로 온다', () => {
    const g = groupByDay([make('a', '2026-10-02T09:00:00'), make('b', '2026-10-03T08:00:00'), make('c', '2026-10-03T21:30:00')])
    expect(g.map((x) => x.day)).toEqual(['2026-10-03', '2026-10-02'])
    expect(g[0].items.map((x) => x.id)).toEqual(['c', 'b'])
  })

  it('시각과 파일 이름을 한국어로 적는다', () => {
    expect(koDateTime('2026-10-03T15:05:00')).toBe('10월 3일 오후 3시 05분')
    expect(koDateTime('2026-10-03T00:40:00')).toBe('10월 3일 오전 12시 40분')
    expect(pngName({ id: '0123456789ab', kind_ko: '책 표지', created: '2026-10-03T15:05:00' }, 'story')).toBe('버추얼미-책표지-20261003-스토리-012345.png')
  })

  it('종류마다 한 줄 요약을 만든다', () => {
    expect(shareSummary(make('a', '2026-10-03T10:00:00', { kind: 'passage', lines: ['첫 문장.', '둘째 문장.'] }))).toBe('첫 문장. 둘째 문장.')
    expect(shareSummary(make('a', '2026-10-03T10:00:00', { kind: 'book', title: '러닝 메이트', text: '소개글' }))).toBe('「러닝 메이트」 소개글')
  })
})

describe('노벨 화면에서 고른 글 나누기 (core bridge)', () => {
  const g = globalThis as Record<string, unknown>
  g.window = {}
  g.document = { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} } }
  new Function(readFileSync('public/virtueme-core-bridge.js', 'utf8'))()
  const VX = (g.window as { VX: { pickLines: (t: string) => { lines: string[]; error: string | null } } }).VX

  it('문장 단위로 나누고 단락 경계를 지킨다', () => {
    expect(VX.pickLines('첫 러닝은 끔찍했다. 그런데 개운했다.\n그 뒤로 주 3회 뛰었다.')).toEqual({
      lines: ['첫 러닝은 끔찍했다.', '그런데 개운했다.', '그 뒤로 주 3회 뛰었다.'], error: null,
    })
  })

  it('세 문장·300자·한 문장 160자를 넘으면 이유를 알려 준다', () => {
    expect(VX.pickLines('하나. 둘. 셋. 넷.').error).toContain('3문장까지')
    expect(VX.pickLines('가'.repeat(170)).error).toContain('160자')
    expect(VX.pickLines(`${'가'.repeat(150)}. ${'나'.repeat(155)}.`).error).toContain('300자')
    expect(VX.pickLines('   ').error).toContain('먼저 골라')
  })
})
