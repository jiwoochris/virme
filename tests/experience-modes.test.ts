import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { experienceModes } from '../src/experienceModes'

describe('integrated experience modes', () => {
  it('puts outputs first (webzine, novel, visual) and the analysis last, without talk', () => {
    expect(experienceModes.map((mode) => mode.id)).toEqual(['webzine', 'novel', 'visual', 'insight'])
    expect(experienceModes.map((mode) => mode.koLabel)).toEqual(['웹진', '노벨', '비주얼', '분석'])
  })

  it('routes both Storyseed modes to local public documents', () => {
    expect(experienceModes.find((mode) => mode.id === 'novel')?.document).toBe('storyseed-novel.html')
  })

  it.each(['storyseed-novel.html', 'storyseed-talk.html'])('%s contains no embedded API key', (name) => {
    const html = readFileSync(new URL(`../public/${name}`, import.meta.url), 'utf8')
    expect(html).toContain("const EMBED_KEY=''")
    expect(html).not.toMatch(/const EMBED_KEY='[^']+'/)
    expect(html).not.toContain('팀 공유용 키가 내장')
  })

  it('retains the complete novel engine and evidence features', () => {
    const html = readFileSync(new URL('../public/storyseed-novel.html', import.meta.url), 'utf8')
    for (const signature of [
      'function buildPersonas',
      'function synthEventsV2',
      'async function mapKeyword',
      'function buildContext',
      'function renderRule',
      'async function renderAI',
      'id="ctxBtn"',
      'id="modelSel"',
    ]) expect(html).toContain(signature)
  })

  it('retains the complete talk engine, history, starters, and streaming response', () => {
    const html = readFileSync(new URL('../public/storyseed-talk.html', import.meta.url), 'utf8')
    for (const signature of [
      'function buildPersonas',
      'function synthEventsV2',
      'function avatarSystem',
      'function greeting',
      'async function send',
      "let HIST=[]",
      "res.body.getReader()",
      'id="starters"',
      'id="modelSel"',
    ]) expect(html).toContain(signature)
  })
})
