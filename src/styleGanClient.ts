import type { PersonalFeatures } from './personalData'
import type { VisualStateId } from './visualStates'

export type StyleGanRequest = {
  signals: number[]
  activity: number
  change: number
  confidence: number
  intensity: number
  state: VisualStateId
  seed: number
  truncation: number
  variation: number
}

export type StyleGanHealth = {
  status: 'ready' | 'loading' | 'error'
  backend?: string
  device?: string
  precision?: string
  concurrent_requests?: boolean
  gpu_name?: string
  gpu_memory_gb?: number
  cuda_version?: string
  model?: string
  resolution?: number
  render_mode?: string
  feature_resolution?: number
  detail?: string
}

const clamp = (value: number) => Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0

const deterministicSeed = (values: number[], state: string) => {
  let seed = 17
  for (const value of values) seed = (seed * 131 + Math.round(clamp(value) * 1000)) % 1_000_000
  for (const character of state) seed = (seed * 33 + character.charCodeAt(0)) % 1_000_000
  return seed
}

export const buildStyleGanRequest = (
  features: PersonalFeatures,
  state: VisualStateId,
  intensity: number,
  variation = 0,
): StyleGanRequest => {
  const signals = Array.from({ length: 5 }, (_, index) => clamp(features.signals[index] ?? 0))
  const activity = clamp(features.activity)
  const change = clamp(features.change)
  const confidence = clamp(features.confidence)
  const boundedIntensity = clamp(intensity)
  const seed = (deterministicSeed([...signals, activity, change, confidence, boundedIntensity], state) + variation * 7919) % 2_147_483_647
  const truncation = clamp(0.42 + confidence * 0.2 + boundedIntensity * 0.1 + activity * 0.075 - change * 0.05)
  return { signals, activity, change, confidence, intensity: boundedIntensity, state, seed, truncation, variation }
}

const fetchWithTimeout = async (input: RequestInfo, init?: RequestInit, timeout = 5000) => {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeout)
  try {
    return await fetch(input, { ...init, signal: controller.signal })
  } finally {
    window.clearTimeout(timer)
  }
}

export const getStyleGanHealth = async (): Promise<StyleGanHealth> => {
  const response = await fetchWithTimeout('/api/stylegan/health')
  if (!response.ok) throw new Error(`StyleGAN health failed: ${response.status}`)
  return response.json() as Promise<StyleGanHealth>
}

export const generateStyleGanFrame = async (request: StyleGanRequest): Promise<Blob> => {
  const response = await fetchWithTimeout('/api/stylegan/generate', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(request),
  }, 30_000)
  if (!response.ok) throw new Error(`StyleGAN generation failed: ${response.status}`)
  return response.blob()
}
