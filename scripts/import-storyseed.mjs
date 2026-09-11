import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const [novelSource, talkSource] = process.argv.slice(2)

if (!novelSource || !talkSource) {
  throw new Error('Usage: node scripts/import-storyseed.mjs <Storyseed_v9.html> <Storyseed_Talk_v4.html>')
}

const outputBySource = new Map([
  [novelSource, 'public/storyseed-novel.html'],
  [talkSource, 'public/storyseed-talk.html'],
])

const publicHint = '공개 버전에는 내장 키가 없습니다. 직접 입력한 키는 이 브라우저의 <em>localStorage</em>에만 저장됩니다. &nbsp;<span id="keyState">—</span>'

for (const [source, output] of outputBySource) {
  // storyseed-novel.html 에는 core 연결 코드(VIRTUEME CORE BRIDGE)가 들어 있다. 원본으로 덮어쓰면 사라지므로 막는다.
  const current = await readFile(resolve(output), 'utf8').catch(() => '')
  if (current.includes('VIRTUEME CORE BRIDGE') && process.env.STORYSEED_FORCE !== '1') {
    throw new Error(`${output} 에 core 연결 코드가 있어 덮어쓰지 않습니다. 원본 변경분을 손으로 합치거나, 지워도 되면 STORYSEED_FORCE=1 로 실행하세요.`)
  }
  const original = await readFile(resolve(source), 'utf8')
  const sanitized = original
    .replace(/\r\n/g, '\n')
    .replace(/const EMBED_KEY='[^']*';[^\n]*/u, "const EMBED_KEY=''; // 공개 빌드에는 내장 키를 포함하지 않습니다.")
    .replace(/<div class="cfghint">[\s\S]*?<span id="keyState">—<\/span><\/div>/u, `<div class="cfghint">${publicHint}</div>`)
    .replace(/\$\('keyState'\)\.textContent=k\?`\$\{own\?'직접 입력한 키':'내장 키'\} 사용 중/u, "$('keyState').textContent=k?`직접 입력한 키 사용 중")
    .replace(/[ \t]+$/gmu, '')

  if (!sanitized.includes("const EMBED_KEY=''")) {
    throw new Error(`Embedded key declaration was not sanitized: ${source}`)
  }
  if (/const EMBED_KEY='[^']+'/u.test(sanitized)) {
    throw new Error(`Refusing to write a Storyseed document with an embedded key: ${source}`)
  }

  await writeFile(resolve(output), sanitized, 'utf8')
  process.stdout.write(`${source} -> ${output}\n`)
}
