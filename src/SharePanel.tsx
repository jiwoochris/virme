import { useEffect, useState } from 'react'
import { coreFetch, useCoreBase } from './coreApi'
import { copyText, downloadPng, groupByDay, koDateTime, pngName, reactionSummary, shareLink, shareSummary, type Share } from './share'

/* 공유 카드 만들기 창(발견 카드에서 연다)과 인사이트 화면의 "내 공유" 기록.
   카드와 공유 페이지는 core 가 만든다(POST /share). 실제 SNS 에 자동으로 올리지 않고, 사용자가 PNG 를 내려받거나 링크를 복사한다. */

export type ShareRequest = { kind: 'discovery'; summary_id: string } | { kind: 'book'; book_id: string } |
  { kind: 'passage'; lines: string[]; title?: string; style_book_id?: string }

async function readError(r: Response) {
  try {
    const d = (await r.json()) as { detail?: unknown }
    return typeof d.detail === 'string' ? d.detail : `HTTP ${r.status}`
  } catch {
    return `HTTP ${r.status}`
  }
}

export function ShareResult({ share, base, onDeleted }: { share: Share; base: string; onDeleted?: () => void }) {
  const [note, setNote] = useState('')
  const link = shareLink(base, share.url)
  const save = async (which: 'feed' | 'story') => {
    try {
      await downloadPng(base + share.files[`${which}.png`], pngName(share, which))
      setNote(which === 'feed' ? '피드 카드를 내려받았습니다.' : '스토리 카드를 내려받았습니다.')
    } catch {
      setNote('내려받지 못했습니다.')
    }
  }
  const remove = async () => {
    const r = await coreFetch(`/share/${share.id}/delete`, { method: 'POST' })
    if (r.ok) onDeleted?.()
    else setNote('지우지 못했습니다.')
  }
  return (
    <div className="share-result" data-testid="share-result">
      <div className="share-shots">
        <figure><img src={base + share.files['feed.png']} alt="피드 카드 (1080×1350)" /><figcaption>피드 1080×1350</figcaption></figure>
        <figure><img src={base + share.files['story.png']} alt="스토리 카드 (1080×1920)" /><figcaption>스토리 1080×1920</figcaption></figure>
      </div>
      <div className="share-actions">
        <button type="button" onClick={() => void save('feed')}>피드 카드 내려받기</button>
        <button type="button" onClick={() => void save('story')}>스토리 카드 내려받기</button>
        <button type="button" onClick={() => void copyText(link).then((ok) => setNote(ok ? '링크를 복사했습니다.' : link))}>링크 복사</button>
        <a href={link} target="_blank" rel="noreferrer">공유 페이지 열기</a>
        {onDeleted && <button type="button" className="danger" onClick={() => void remove()}>이 공유 지우기</button>}
      </div>
      <p className="share-note" aria-live="polite">{note}</p>
      <p className="share-fine">
        {share.privacy?.replaced.length ? `카드에 넣기 전에 바꾼 것: ${share.privacy.replaced.join(', ')}. ` : '실명·가명·좌표·주소·연락처가 없는지 확인했습니다. '}
        링크는 이 컴퓨터의 기록 서버가 켜져 있을 때 열립니다. 다른 사람에게 보여 주려면 core/COLUMNS.md 「공유 카드」의 내보내기를 씁니다.
      </p>
    </div>
  )
}

/** 만들기 요청을 한 번 보내고 결과를 보여 주는 창 */
export function ShareDialog({ request, label, onClose, onShared }: { request: ShareRequest; label: string; onClose: () => void; onShared?: () => void }) {
  const base = useCoreBase()
  const [state, setState] = useState<{ s: 'busy' } | { s: 'done'; share: Share } | { s: 'error'; msg: string }>({ s: 'busy' })
  useEffect(() => {
    let cancelled = false
    coreFetch('/share', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal: AbortSignal.timeout(120000) })
      .then(async (r) => {
        if (!r.ok) throw new Error(await readError(r))
        return (await r.json()) as Share
      })
      .then((share) => { if (!cancelled) { setState({ s: 'done', share }); onShared?.() } })
      .catch((e: Error) => !cancelled && setState({ s: 'error', msg: e.message }))
    return () => { cancelled = true }
    // 창 하나에 요청 하나
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="share-backdrop" onClick={onClose}>
      <div className="share-dialog" role="dialog" aria-modal="true" aria-label="공유 카드" onClick={(e) => e.stopPropagation()}>
        <div className="share-head">
          <h3>공유 카드 · {label}</h3>
          <button type="button" className="share-close" onClick={onClose} aria-label="닫기">닫기</button>
        </div>
        {state.s === 'busy' && <p className="share-busy"><i />카드 생성 중</p>}
        {state.s === 'error' && <p className="share-error">카드를 만들지 못했습니다. {state.msg}</p>}
        {state.s === 'done' && base && <ShareResult share={state.share} base={base} onDeleted={() => { onShared?.(); onClose() }} />}
      </div>
    </div>
  )
}

/** 인사이트 화면의 "내 공유": 무엇을 언제 공유했고 어떤 반응이 왔는지 날짜별로 */
export function MyShares({ shares, base }: { shares: Share[] | null; base: string | null }) {
  if (!shares) return <p className="insight-sub">공유 기록을 불러오는 중</p>
  if (!shares.length) {
    return <p className="insight-sub">아직 공유한 카드가 없습니다. 발견 문장, 노벨 모드의 책, 이야기 속 문장에서 <b>카드 만들기</b>를 누르면 여기에 쌓입니다.</p>
  }
  return (
    <ol className="share-timeline" data-testid="my-shares">
      {groupByDay(shares).map((g) => (
        <li key={g.day}>
          <div className="share-day">{koDateTime(`${g.day}T00:00`).split(' ').slice(0, 2).join(' ')}</div>
          <ul>
            {g.items.map((s) => (
              <li key={s.id} className="share-item">
                {base && <a href={shareLink(base, s.url)} target="_blank" rel="noreferrer"><img src={base + s.files['feed.png']} alt="" loading="lazy" /></a>}
                <div>
                  <div className="share-meta"><span className="disc-tag">{s.kind_ko}</span><span>{koDateTime(s.created).split(' ').slice(2).join(' ')}</span></div>
                  <p className="share-text">{shareSummary(s)}</p>
                  <p className="share-react">{s.reactions.total ? reactionSummary(s) : '아직 반응이 없습니다'}</p>
                  {s.reactions.recent.filter((r) => r.comment).slice(0, 3).map((r, i) => (
                    <p className="share-comment" key={i}>“{r.comment}” <small>{r.label} · {koDateTime(r.ts)}</small></p>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  )
}
