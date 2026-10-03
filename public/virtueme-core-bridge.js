/* VIRTUEME CORE BRIDGE: Storyseed 노벨·Talk 화면이 로컬 core 서버(uv run virtueme serve)와 이야기하는 공통 코드.
   서버가 없으면 VX.detect() 가 null 을 돌려주고, 화면은 원래 Storyseed 와 같게 동작한다.
   서버 응답의 텍스트는 이미 가명·개인정보 가리기를 거친 것이다. 근거 계약 v2 검증은 서버(core/verify.py)가 한다. */
(function () {
  'use strict'
  const BASES = ['/api/core', 'http://127.0.0.1:8767']
  const MODES = [['blur', '틀린 값은 흐리게'], ['plain', '강조만 빼기'], ['rewrite', '문장 다시 쓰기']]
  const LS_MODE = 'vx_mode'
  let base = null
  let dataId = null // core 의 기록 묶음 식별자. 기록을 지우면 바뀌어서, 지운 기록으로 만든 이야기·답은 다시 보여 주지 않는다

  const css = `
.vx{font-style:normal;color:var(--gold);cursor:pointer;border-bottom:1px dotted rgba(217,179,106,.45)}
.vx:hover{background:rgba(217,179,106,.12)}
.vx-pending{font-style:normal;color:rgba(217,179,106,.55)}
.vx-blur{color:var(--mut);border-bottom:1px dashed rgba(155,160,176,.5);cursor:help}
.vx-drop{color:inherit}
.vx-legend{margin-top:22px;padding-top:12px;border-top:1px solid var(--line2);font-size:11.5px;color:var(--mut);line-height:1.7}
.vx-legend b{color:var(--gold);font-weight:600}
.vx-legend i{font-style:normal;color:var(--sub)}
.vx-pop{position:fixed;z-index:1000;max-width:340px;padding:10px 12px;border-radius:10px;background:#171923;
  border:1px solid rgba(217,179,106,.4);box-shadow:0 12px 30px rgba(0,0,0,.45);font-size:12px;line-height:1.6;color:#D8DBE4}
.vx-pop .d{font-family:var(--mono);font-size:10.5px;color:var(--dim);display:block;margin-bottom:4px}
.vx.vx-summary{border-bottom:3px double rgba(217,179,106,.65)}
.vx.vx-summary:hover{background:rgba(217,179,106,.16)}
.vx-unsupported{border-bottom:1px dashed rgba(232,163,127,.75);cursor:help}
.vx-pop .vals{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 2px}
.vx-pop .vals span{font-size:10.5px;padding:1px 7px;border-radius:999px;border:1px solid rgba(217,179,106,.35);color:var(--gold)}
.vx-pop button{margin-top:8px;font-size:11px;padding:4px 10px}
.vx-pop ul{list-style:none;margin:8px 0 0;padding:0;max-height:220px;overflow-y:auto}
.vx-pop li{padding:5px 0;border-top:1px solid rgba(255,255,255,.07)}
.vx-share-busy{margin-top:8px;font-size:13px;color:var(--sub)}
.vx-share-busy i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--pink2);margin-right:9px;animation:pu 1.1s ease-in-out infinite}
.vx-share-shots{display:grid;grid-template-columns:1fr .79fr;gap:14px;align-items:end;margin-top:6px}
.vx-share-shots figure{margin:0}
.vx-share-shots img{display:block;width:100%;height:auto;border-radius:6px;box-shadow:0 10px 30px rgba(0,0,0,.4)}
.vx-share-shots figcaption{margin-top:6px;font-size:11.5px;color:var(--mut)}
.vx-share-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.vx-share-actions button,.vx-share-actions a{font-size:13px;padding:8px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.3);background:transparent;color:var(--ink);text-decoration:none;cursor:pointer;font-weight:600}
.vx-share-actions button:hover,.vx-share-actions a:hover{border-color:#fff}
.vx-share-note{min-height:1.4em;margin:8px 0 0;font-size:12.5px;color:var(--sub);word-break:break-all}
.vx-share-fine{margin:2px 0 0;font-size:11.5px;color:var(--dim);line-height:1.65}
.vx-selshare{position:absolute;z-index:950;font-size:12.5px;font-weight:700;padding:7px 13px;border-radius:999px;border:none;background:var(--grad);color:#fff;box-shadow:0 6px 18px rgba(0,0,0,.45);cursor:pointer}
.vx-selshare small{font-weight:400;opacity:.85;margin-left:6px}
.vx-mode{font-size:11.5px;padding:6px 10px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.035);color:var(--sub)}
`
  function injectCss () {
    if (document.getElementById('vx-css')) return
    const s = document.createElement('style'); s.id = 'vx-css'; s.textContent = css; document.head.appendChild(s)
  }

  async function detect () {
    injectCss()
    for (const b of BASES) {
      try {
        const r = await fetch(b + '/health', { signal: AbortSignal.timeout(1500) })
        if (!r.ok) continue
        const h = await r.json()
        if (h && h.built) { base = b; dataId = h.data_id || null; return { base: b, health: h } }
      } catch (e) {}
    }
    return null
  }

  async function call (path, body, timeoutMs) {
    const opt = { signal: AbortSignal.timeout(timeoutMs || 180000) }
    if (body !== undefined) Object.assign(opt, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const r = await fetch(base + path, opt)
    if (!r.ok) {
      let m = ''; try { m = (await r.json()).detail || '' } catch (e) {}
      throw new Error(`로컬 기록 서버 오류 (HTTP ${r.status})${m ? ' — ' + (typeof m === 'string' ? m : JSON.stringify(m)) : ''}`)
    }
    return r.json()
  }

  function esc (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }

  /* 스트리밍 중 표시: 모델 글자는 전부 이스케이프하고, <em ref="…"> 만 "검증 전" 모양으로 살린다 */
  function fmtPending (raw) {
    return esc(String(raw).replace(/\*\*(.+?)\*\*/g, '$1').replace(/^#+\s*/gm, ''))
      .replace(/&lt;em(?:\s+ref\s*=\s*(?:&quot;|")?[^&"]*(?:&quot;|")?)?\s*&gt;/g, '<em class="vx-pending">')
      .replace(/&lt;\/em&gt;/g, '</em>')
  }

  const ALIAS_RE = /지인([A-Z]{1,2})(이가|이랑|에게|한테|이야|가|이|은|는|을|를|와|과|아|야|랑|의|도|만)?/g
  function hasJong (w) { const c = w.charCodeAt(w.length - 1); return c >= 0xAC00 && c <= 0xD7A3 && (c - 0xAC00) % 28 !== 0 }
  const JO = (w, a, b) => hasJong(w) ? a : b
  /* 가명 표기가 남으면 관계어나 "그 사람"으로 바꾸고 조사를 맞춘다 */
  function withParticle (who, pt) { /* 바꾼 이름의 받침에 맞춰 조사를 고친다. 부르는 말(아·야)은 뗀다 */
    if (!pt) return who
    const fix = { '가': JO(who, '이', '가'), '이': JO(who, '이', '가'), '이가': JO(who, '이', '가'), '은': JO(who, '은', '는'), '는': JO(who, '은', '는'),
      '을': JO(who, '을', '를'), '를': JO(who, '을', '를'), '와': JO(who, '과', '와'), '과': JO(who, '과', '와'), '랑': JO(who, '이랑', '랑'),
      '이랑': JO(who, '이랑', '랑'), '아': '', '야': '', '이야': JO(who, '이야', '야') }
    return who + (pt in fix ? fix[pt] : pt)
  }
  /* 가린 표기가 LLM 답에 그대로 남았을 때: "(나)" 는 나로, "(누군가)" 는 그 사람으로 (조사는 받침에 맞춤) */
  const MASK_RE = /\((나|누군가)\)(이가|이랑|이는|이도|이를|이야|이한테|가|이|은|는|을|를|랑|아|야|씨|님|의|도|만|한테)?/g
  const SELF_FORM = { '이가': '내가', '가': '내가', '이': '나', '이는': '나는', '는': '나는', '은': '나는', '이를': '나를', '를': '나를',
    '을': '나를', '이랑': '나랑', '랑': '나랑', '이도': '나도', '도': '나도', '이한테': '나한테', '한테': '나한테', '의': '나의', '만': '나만',
    '이야': '나야', '아': '', '야': '', '씨': '', '님': '' }
  function unmask (text) {
    return String(text).replace(MASK_RE, (m, who, pt) => who === '나'
      ? (pt ? (SELF_FORM[pt] ?? '나' + pt) : '나')
      : pt === '님' || pt === '씨' ? '그 사람'
        : withParticle('그 사람', pt && pt.startsWith('이') && pt.length > 1 ? pt.slice(1) : pt))
  }
  function unalias (html, people) {
    return unmask(String(html).replace(ALIAS_RE, (m, id, pt) => withParticle((people && people['지인' + id]) || '그 사람', pt)))
  }
  function relabel (text, people) { /* 옆 패널·기록 팝업용: 관계가 붙은 가명만 관계로 */
    return String(text).replace(ALIAS_RE, (m, id, pt) => people && people['지인' + id] ? withParticle(people['지인' + id], pt) : m)
  }

  /* 발표 대비: 검사까지 끝난 이야기(키워드별 12편)와 대화 답(질문별 40개)을 이 브라우저에 남긴다.
     네트워크나 키 문제로 새로 만들지 못하면 지난 것을 만든 날짜와 함께 보여 준다.
     저장할 때의 기록 묶음(data_id)과 지금 core 의 기록 묶음이 다르면(기록을 지우거나 새로 넣음) 쓰지 않는다. */
  function makeCache (lsKey, limit, norm) {
    const read = () => { try { return JSON.parse(localStorage.getItem(lsKey) || '{}') } catch (e) { return {} } }
    return {
      save (key, entry) {
        try {
          const all = read()
          all[norm(key)] = { ...entry, data_id: dataId, saved: new Date().toISOString() }
          Object.keys(all).sort((a, b) => all[b].saved.localeCompare(all[a].saved)).slice(limit).forEach((k) => delete all[k])
          localStorage.setItem(lsKey, JSON.stringify(all))
        } catch (e) {}
      },
      load (key) {
        const got = read()[norm(key)] || null
        return got && got.data_id && dataId && got.data_id !== dataId ? null : got
      },
    }
  }
  const storyCache = makeCache('vx_story_cache', 12, (k) => String(k).trim())
  /* 질문은 띄어쓰기·끝 문장부호를 무시하고 맞춘다 ("처음 뛴 날 기억나?" = "처음 뛴 날 기억나") */
  const talkCache = makeCache('vx_talk_cache', 40, (q) => String(q).replace(/\s+/g, '').replace(/[?!.~…]+$/, ''))
  const saveStory = storyCache.save, loadStory = storyCache.load
  const saveTalk = talkCache.save, loadTalk = talkCache.load

  function mode () { try { return localStorage.getItem(LS_MODE) || 'blur' } catch (e) { return 'blur' } }
  function modeSelect () {
    const s = document.createElement('select'); s.className = 'vx-mode'; s.title = '기록과 맞지 않는 값을 어떻게 처리할지'
    s.innerHTML = MODES.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')
    s.value = mode()
    s.onchange = () => { try { localStorage.setItem(LS_MODE, s.value) } catch (e) {} }
    return s
  }

  /* 기록과 맞지 않은 값: 규칙 검사 실패 + 문맥 검사 불일치 + 앵커 밖의 틀린 날짜.
     앵커 밖 숫자(비율·걸음 수·금액 등)는 글을 바꾸지 않고 점선 표시만 하므로 따로 센다 */
  function looseNumbers (stats) { return stats.loose_numeric_unsupported || 0 }
  function badCount (stats) {
    return (stats.failed || 0) + (stats.context_mismatch || 0) + Math.max(0, (stats.loose_unsupported || 0) - looseNumbers(stats))
  }
  function legend (stats, modeName) {
    const ok = (stats.ok || 0) + (stats.ref_fixed || 0) + (stats.unreferenced_ok || 0)
    const bad = badCount(stats)
    const how = { blur: '흐리게 바꿈', plain: '강조만 뺌', rewrite: '문장을 다시 씀' }[modeName] || ''
    return `<div class="vx-legend"><b>금색</b>은 이야기가 인용한 기록과 날짜·시각·횟수·낱말을 한 기록 안에서 대조해 통과한 값 ${ok}개입니다.` +
      (bad ? ` 기록과 맞지 않은 값 ${bad}개는 <i>${how}</i>.` : ' 기록과 맞지 않은 값은 없었습니다.') +
      (looseNumbers(stats) ? ` <i>점선 밑줄</i>은 기록에서 확인되지 않은 숫자 ${looseNumbers(stats)}개입니다.` : '') +
      ` 금색 값을 누르면 그 기록을, 겹밑줄 값을 누르면 여러 기록을 센 요약을 보여 줍니다.</div>`
  }

  let pop = null
  function hidePop () { if (pop) { pop.remove(); pop = null } }
  /* 요약 기록(s + 11자리): GET /columns 의 summaries. 한 번 받아 두고 쓴다 */
  let summaries = null
  async function summaryOf (sid) {
    if (!summaries) {
      const cols = await call('/columns')
      summaries = new Map((cols.summaries || []).map((x) => [x.id, x]))
    }
    return summaries.get(sid) || null
  }
  function valueChips (v) {
    const out = []
    const md = (d) => { const [, m, dd] = String(d).split('-').map(Number); return `${m}월 ${dd}일` }
    ;(v.dates || []).forEach((d) => out.push(md(d)))
    ;(v.numbers || []).forEach((n) => out.push(`${Number(n.value).toLocaleString()}${n.unit || ''}`))
    ;(v.percents || []).forEach((p) => out.push(`${Math.round(p)}%`))
    ;(v.times || []).forEach((t) => out.push(t))
    return out
  }
  async function recordLine (rid, people) {
    const rec = await call('/records/' + rid)
    const body = rec.text ? relabel(rec.text, people) : '사진 · ' + (rec.photo_tags || []).join(', ')
    return `<li><span class="d">${esc(rec.date_ko)} ${esc(rec.weekday)}요일 ${esc(rec.time)} · ${esc(rec.source)}</span>${esc(body)}</li>`
  }
  async function showSummary (el, sid, people) {
    const s = await summaryOf(sid)
    if (!pop) return
    if (!s) { pop.textContent = '요약 기록을 찾지 못했습니다'; return }
    const ev = s.evidence || []
    const chips = valueChips(s.values || {})
    pop.innerHTML = `<span class="d">요약 기록 · ${esc(s.about || '')} · 여러 기록을 센 값</span>${esc(relabel(s.text, people))}` +
      (chips.length ? `<div class="vals">${chips.map((c) => `<span>${esc(c)}</span>`).join('')}</div>` : '') +
      (ev.length ? `<button type="button">근거 기록 ${ev.length}건 보기</button>` : '')
    const btn = pop.querySelector('button'); if (!btn) return
    btn.onclick = async (e) => {
      e.stopPropagation()
      btn.disabled = true; btn.textContent = '기록을 불러오는 중'
      try {
        const lines = await Promise.all(ev.slice(0, 8).map((id) => recordLine(id, people).catch(() => '')))
        if (!pop) return
        btn.remove()
        pop.insertAdjacentHTML('beforeend', `<ul>${lines.join('')}</ul>`)
      } catch (err) { btn.textContent = '기록을 불러오지 못했습니다' }
    }
  }
  async function showRecord (el, people) {
    hidePop()
    const rid = el.getAttribute('data-rid'), sid = el.getAttribute('data-sid')
    if (!rid && !sid) return
    pop = document.createElement('div'); pop.className = 'vx-pop'; pop.textContent = '기록을 불러오는 중'
    pop.addEventListener('click', (e) => e.stopPropagation())
    document.body.appendChild(pop)
    const r = el.getBoundingClientRect()
    pop.style.left = Math.max(8, Math.min(window.innerWidth - 350, r.left)) + 'px'
    pop.style.top = (r.bottom + 8) + 'px'
    if (sid) {
      try { await showSummary(el, sid, people) } catch (e) { if (pop) pop.textContent = '요약 기록을 불러오지 못했습니다' }
      return
    }
    try {
      const rec = await call('/records/' + rid)
      if (!pop) return
      const body = rec.text ? relabel(rec.text, people) : '사진 · ' + (rec.photo_tags || []).join(', ')
      pop.innerHTML = `<span class="d">${esc(rec.date_ko)} ${esc(rec.weekday)}요일 ${esc(rec.time)} · ${esc(rec.source)}${rec.room ? ' · ' + esc(relabel(rec.room, people)) : ''}</span>${esc(body)}`
    } catch (e) { if (pop) pop.textContent = '기록을 불러오지 못했습니다' }
  }
  function bindRecordClicks (root, peopleGetter) {
    root.addEventListener('click', e => {
      const el = e.target.closest && e.target.closest('.vx[data-rid], .vx[data-sid]')
      if (el) { e.stopPropagation(); showRecord(el, peopleGetter()) } else hidePop()
    })
    window.addEventListener('scroll', (e) => { if (!pop || !pop.contains(e.target)) hidePop() }, true)
    // 앵커 밖의 확인되지 않은 숫자: 풍선 도움말을 화면 문구로 맞춘다
    root.addEventListener('mouseover', e => {
      const el = e.target.closest && e.target.closest('.vx-unsupported')
      if (el) el.title = '기록에서 확인되지 않은 숫자'
    })
  }

  /* ---------- 공유 카드 (core: POST /share). 이야기 속 문장·책 표지로 카드와 공유 페이지를 만든다 ---------- */
  /* 고른 글을 카드에 실을 줄(문장)로 나눈다. 세 문장·300자·한 문장 160자까지 */
  function pickLines (text, maxLines, maxChars) {
    maxLines = maxLines || 3; maxChars = maxChars || 300
    const clean = String(text || '').replace(/\u00a0/g, ' ').trim()
    if (!clean) return { lines: [], error: '카드에 넣을 문장을 먼저 골라 주세요.' }
    const lines = clean.split(/\n+/)
      .flatMap((p) => p.replace(/\s+/g, ' ').trim().split(/(?<=[.!?。…])\s+(?=\S)/))
      .map((x) => x.trim()).filter(Boolean)
    if (lines.length > maxLines) return { lines: lines.slice(0, maxLines), error: `${maxLines}문장까지 고를 수 있습니다. 지금 ${lines.length}문장을 골랐습니다.` }
    if (lines.join(' ').length > maxChars) return { lines, error: `${maxChars}자까지 고를 수 있습니다.` }
    if (lines.some((x) => x.length > 160)) return { lines, error: '한 문장이 너무 깁니다. 160자 안으로 골라 주세요.' }
    return { lines, error: null }
  }
  function shareLink (path) { return new URL(String(base || '').replace(/\/$/, '') + path, location.href).href }
  async function savePng (url, name) {
    const r = await fetch(url); if (!r.ok) throw new Error('HTTP ' + r.status)
    const href = URL.createObjectURL(await r.blob())
    const a = document.createElement('a'); a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 4000)
  }
  /* el 안에 만들기 진행·결과(미리보기, 내려받기, 링크 복사)를 그린다 */
  async function shareInto (el, body, label) {
    el.hidden = false
    el.innerHTML = `<h3>공유 카드 · ${esc(label)}</h3><div class="vx-share-busy"><i></i>카드 생성 중</div>`
    let s
    try { s = await call('/share', body, 120000) } catch (e) {
      el.innerHTML = `<h3>공유 카드 · ${esc(label)}</h3><div class="err">카드를 만들지 못했습니다. ${esc(String(e.message || e).replace(/^로컬 기록 서버 오류 \(HTTP \d+\) — /, ''))}</div>`
      return null
    }
    const link = shareLink(s.url), day = String(s.created || '').slice(0, 10).replace(/-/g, '')
    const name = (w) => `버추얼미-${String(s.kind_ko).replace(/\s+/g, '')}-${day}-${w}-${s.id.slice(0, 6)}.png`
    const replaced = (s.privacy && s.privacy.replaced) || []
    el.innerHTML = `<h3>공유 카드 · ${esc(label)}</h3>
      <div class="vx-share-shots"><figure><img src="${base + s.files['feed.png']}" alt="피드 카드"><figcaption>피드 1080×1350</figcaption></figure>
        <figure><img src="${base + s.files['story.png']}" alt="스토리 카드"><figcaption>스토리 1080×1920</figcaption></figure></div>
      <div class="vx-share-actions"><button data-a="feed">피드 카드 내려받기</button><button data-a="story">스토리 카드 내려받기</button>
        <button data-a="copy">링크 복사</button><a href="${esc(link)}" target="_blank" rel="noopener">공유 페이지 열기</a></div>
      <p class="vx-share-note" aria-live="polite"></p>
      <p class="vx-share-fine">${replaced.length ? `카드에 넣기 전에 바꾼 것: ${esc(replaced.join(', '))}. ` : '실명·가명·좌표·주소·연락처가 없는지 확인했습니다. '}공유한 카드와 받은 반응은 INSIGHT 의 내 공유에서 봅니다. 링크는 이 컴퓨터의 기록 서버가 켜져 있을 때 열립니다.</p>`
    const note = el.querySelector('.vx-share-note')
    el.querySelectorAll('button[data-a]').forEach((b) => b.onclick = async () => {
      const a = b.dataset.a
      try {
        if (a === 'copy') { await navigator.clipboard.writeText(link); note.textContent = '링크를 복사했습니다.' }
        else { await savePng(base + s.files[a + '.png'], name(a === 'feed' ? '피드' : '스토리')); note.textContent = a === 'feed' ? '피드 카드를 내려받았습니다.' : '스토리 카드를 내려받았습니다.' }
      } catch (e) { note.textContent = a === 'copy' ? link : '내려받지 못했습니다.' }
    })
    return s
  }

  window.VX = { detect, call, base: () => base, pickLines, shareInto, looseNumbers, fmtPending, unalias, relabel, mode, modeSelect, legend, badCount, bindRecordClicks, esc, JO, saveStory, loadStory,
    saveTalk, loadTalk }
})()
