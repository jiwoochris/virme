/* 공유 카드(core: POST /share, GET /share, /s/<번호>) 화면 공용 코드.
   카드와 공유 페이지는 core 가 만들고, 화면은 만들기 요청·내려받기·링크 복사·반응 보기만 한다. */

export type ShareKind = 'discovery' | 'book' | 'passage'
export type Reaction = { ts: string; reaction: string; label: string; comment: string }
export type Share = {
  id: string
  kind: ShareKind
  kind_ko: string
  created: string
  title?: string
  text?: string
  lines?: string[]
  url: string
  files: Record<string, string>
  privacy?: { replaced: string[]; checked?: string[] }
  reactions: { counts: Record<string, number>; total: number; labels: Record<string, string>; recent: Reaction[] }
}

/** core 주소(/api/core 같은 상대 주소일 수 있음)와 공유 경로로 붙여 넣을 수 있는 절대 주소를 만든다 */
export const shareLink = (base: string, path: string, here: string = globalThis.location?.href ?? 'http://127.0.0.1/') =>
  new URL(base.replace(/\/$/, '') + path, here).href

/** "❤️ 좋아요 2 · 🙌 나도 그래요 1". 반응이 없으면 빈 문자열 */
export const reactionSummary = (s: Pick<Share, 'reactions'>) =>
  Object.entries(s.reactions.counts)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${s.reactions.labels[k] ?? k} ${n}`)
    .join(' · ')

const pad = (n: number) => String(n).padStart(2, '0')
/** "10월 3일 오후 3시 05분" (로컬 시각 문자열 2026-10-03T15:05:00 을 그대로 읽는다) */
export const koDateTime = (iso: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!m) return iso
  const h = Number(m[4])
  return `${Number(m[2])}월 ${Number(m[3])}일 ${h < 12 ? '오전' : '오후'} ${h % 12 || 12}시 ${m[5]}분`
}

/** 공유를 만든 날짜별로 묶는다 (최근 날짜가 위) */
export const groupByDay = (shares: Share[]) => {
  const out: { day: string; items: Share[] }[] = []
  for (const s of [...shares].sort((a, b) => b.created.localeCompare(a.created))) {
    const day = s.created.slice(0, 10)
    const last = out[out.length - 1]
    if (last && last.day === day) last.items.push(s)
    else out.push({ day, items: [s] })
  }
  return out
}

/** 카드에 실을 글 한 줄 요약 */
export const shareSummary = (s: Share) =>
  s.kind === 'passage' ? (s.lines ?? []).join(' ') : s.kind === 'book' ? `「${s.title ?? ''}」 ${s.text ?? ''}`.trim() : s.text ?? ''

/** 내려받을 파일 이름: 버추얼미-발견-20261003-a1b2c3.png */
export const pngName = (s: Pick<Share, 'id' | 'kind_ko' | 'created'>, which: 'feed' | 'story') =>
  `버추얼미-${s.kind_ko.replace(/\s+/g, '')}-${s.created.slice(0, 10).replace(/-/g, '')}-${which === 'feed' ? '피드' : '스토리'}-${s.id.slice(0, 6)}.png`

/** 다른 주소(core 서버)의 그림도 내려받기 이름이 붙게 blob 으로 받아 저장한다 */
export async function downloadPng(url: string, name: string) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const href = URL.createObjectURL(await r.blob())
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 4000)
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
