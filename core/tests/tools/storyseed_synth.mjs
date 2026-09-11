// Storyseed 원본(public/storyseed-novel.html)의 합성 데이터 함수를 그대로 실행해 JSON 으로 출력한다.
// 사용: node tests/tools/storyseed_synth.mjs <html> <seed> [personaIndex]
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const [html, seedArg, idxArg] = process.argv.slice(2)
const src = readFileSync(html, 'utf8')
const start = src.indexOf('function mulberry32')
const end = src.indexOf('/* ============ 4. 특징 추출')
if (start < 0 || end < 0) throw new Error('Storyseed 합성 데이터 구간을 찾지 못했습니다')
const code = src.slice(start, end) + '\n;({buildPersonas, synthEventsV2, mulberry32, hashStr})'
const api = vm.runInNewContext(code, { Math })
const seed = Number(seedArg)
const pool = api.buildPersonas(seed, 30)
const k = idxArg === undefined ? Math.floor(api.mulberry32(seed)() * 30) : Number(idxArg)
const me = pool[k]
const ev = api.synthEventsV2(me, seed)
process.stdout.write(JSON.stringify({ k, persona: me, events: ev }))
