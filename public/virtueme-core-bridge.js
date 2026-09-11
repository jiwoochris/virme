/* VIRTUEME CORE BRIDGE: Storyseed 노벨·Talk 화면이 로컬 core 서버(uv run virtueme serve)와 이야기하는 공통 코드.
   서버가 없으면 VX.detect() 가 null 을 돌려주고, 화면은 원래 Storyseed 와 같게 동작한다.
   서버 응답의 텍스트는 이미 가명·개인정보 가리기를 거친 것이다. 근거 계약 v2 검증은 서버(core/verify.py)가 한다. */
(function () {
  'use strict'
  const BASES = ['/api/core', 'http://127.0.0.1:8767']
  const MODES = [['blur', '틀린 값은 흐리게'], ['plain', '강조만 빼기'], ['rewrite', '문장 다시 쓰기']]
  const LS_MODE = 'vx_mode'
  let base = null

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
        if (h && h.built) { base = b; return { base: b, health: h } }
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
  function unalias (html, people) {
    return String(html).replace(ALIAS_RE, (m, id, pt) => withParticle((people && people['지인' + id]) || '그 사람', pt))
  }
  function relabel (text, people) { /* 옆 패널·기록 팝업용: 관계가 붙은 가명만 관계로 */
    return String(text).replace(ALIAS_RE, (m, id, pt) => people && people['지인' + id] ? withParticle(people['지인' + id], pt) : m)
  }

  function mode () { try { return localStorage.getItem(LS_MODE) || 'blur' } catch (e) { return 'blur' } }
  function modeSelect () {
    const s = document.createElement('select'); s.className = 'vx-mode'; s.title = '기록과 맞지 않는 값을 어떻게 처리할지'
    s.innerHTML = MODES.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')
    s.value = mode()
    s.onchange = () => { try { localStorage.setItem(LS_MODE, s.value) } catch (e) {} }
    return s
  }

  /* 기록과 맞지 않은 값: 규칙 검사 실패 + 문맥 검사 불일치 + 앵커 밖의 틀린 날짜 */
  function badCount (stats) { return (stats.failed || 0) + (stats.context_mismatch || 0) + (stats.loose_unsupported || 0) }
  function legend (stats, modeName) {
    const ok = (stats.ok || 0) + (stats.ref_fixed || 0) + (stats.unreferenced_ok || 0)
    const bad = badCount(stats)
    const how = { blur: '흐리게 바꿈', plain: '강조만 뺌', rewrite: '문장을 다시 씀' }[modeName] || ''
    return `<div class="vx-legend"><b>금색</b>은 이야기가 인용한 기록과 날짜·시각·횟수·낱말을 한 기록 안에서 대조해 통과한 값 ${ok}개입니다.` +
      (bad ? ` 기록과 맞지 않은 값 ${bad}개는 <i>${how}</i>.` : ' 기록과 맞지 않은 값은 없었습니다.') +
      ` 금색 값을 누르면 그 기록을 보여 줍니다.</div>`
  }

  let pop = null
  function hidePop () { if (pop) { pop.remove(); pop = null } }
  async function showRecord (el, people) {
    hidePop()
    const rid = el.getAttribute('data-rid'); if (!rid) return
    pop = document.createElement('div'); pop.className = 'vx-pop'; pop.textContent = '기록을 불러오는 중'
    document.body.appendChild(pop)
    const r = el.getBoundingClientRect()
    pop.style.left = Math.max(8, Math.min(window.innerWidth - 350, r.left)) + 'px'
    pop.style.top = (r.bottom + 8) + 'px'
    try {
      const rec = await call('/records/' + rid)
      if (!pop) return
      const body = rec.text ? relabel(rec.text, people) : '사진 · ' + (rec.photo_tags || []).join(', ')
      pop.innerHTML = `<span class="d">${esc(rec.date_ko)} ${esc(rec.weekday)}요일 ${esc(rec.time)} · ${esc(rec.source)}${rec.room ? ' · ' + esc(relabel(rec.room, people)) : ''}</span>${esc(body)}`
    } catch (e) { if (pop) pop.textContent = '기록을 불러오지 못했습니다' }
  }
  function bindRecordClicks (root, peopleGetter) {
    root.addEventListener('click', e => {
      const el = e.target.closest && e.target.closest('.vx[data-rid]')
      if (el) { e.stopPropagation(); showRecord(el, peopleGetter()) } else hidePop()
    })
    window.addEventListener('scroll', hidePop, true)
  }

  window.VX = { detect, call, fmtPending, unalias, relabel, mode, modeSelect, legend, badCount, bindRecordClicks, esc, JO }
})()
