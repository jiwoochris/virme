import { useEffect, useState } from 'react'

/* core 서버 주소 찾기. 개발 서버(Vite)는 /api/core 를 127.0.0.1:8767 로 넘기고, 빌드한 정적 파일로 띄우면 프록시가 없으므로
   127.0.0.1:8767 에 바로 붙는다 (core 의 CORS 가 localhost·127.0.0.1 을 허용). 찾은 주소만 기억하고, 못 찾으면 다음에 다시 찾는다. */
const CANDIDATES = ['/api/core', 'http://127.0.0.1:8767']
let found: string | null = null

export async function coreBase(): Promise<string | null> {
  if (found) return found
  for (const base of CANDIDATES) {
    try {
      const r = await fetch(`${base}/health`, { signal: AbortSignal.timeout(1500) })
      if (!r.ok) continue
      const h = (await r.json()) as { status?: string }
      if (h && typeof h.status === 'string') return (found = base)
    } catch { /* 다음 후보 */ }
  }
  return null
}

export async function coreFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = await coreBase()
  if (!base) throw new Error('core 서버를 찾지 못했습니다')
  return fetch(base + path, init)
}

/** 화면에서 음원·영상 주소를 만들 때 쓰는 core 주소. 찾기 전에는 null */
export function useCoreBase(): string | null {
  const [base, setBase] = useState<string | null>(found)
  useEffect(() => {
    if (!base) void coreBase().then(setBase)
  }, [base])
  return base
}
