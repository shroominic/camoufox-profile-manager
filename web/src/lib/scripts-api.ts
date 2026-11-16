import { apiRequest } from './api'

export type ScriptInputType = 'text' | 'secret' | 'integer' | 'boolean' | 'select' | 'profile' | 'proxy'
export interface ScriptProxyChoice { id: string; name: string; server: string }
export interface ScriptInput {
  name: string
  label: string
  type: ScriptInputType
  required: boolean
  help?: string
  default?: string | number | boolean | null
  choices?: { value: string; label: string }[]
}
export interface ScriptStage { id: string; label: string }
export interface ScriptDefinition {
  name: string
  description: string
  language: 'python'
  code: string
  inputs: ScriptInput[]
  stages: ScriptStage[]
  max_runtime_seconds: number
  enabled: boolean
}
export interface ScriptSummary extends Omit<ScriptDefinition, 'code'> {
  id: string
  revision: number
  created_at: string
  updated_at: string
}
export interface SavedScript extends ScriptSummary { code: string }
export type ScriptRunStatus = 'queued' | 'running' | 'stopping' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export interface ScriptRun {
  id: string
  script_id: string
  name: string
  revision: number
  status: ScriptRunStatus
  stage?: string | null
  error_code?: string | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  resume_of?: string | null
  result?: Record<string, unknown> | null
  events?: { kind: string; stage?: string | null; created_at: string }[]
}
export type ScriptInputValues = Record<string, string | number | boolean>

/** Secret defaults are never filled, even if an imported definition supplies one. */
export function initialScriptInputs(
  inputs: ScriptInput[], proxies: readonly Pick<ScriptProxyChoice, 'id'>[] = [], previousValues: ScriptInputValues = {},
): ScriptInputValues {
  return Object.fromEntries(inputs.map(input => {
    const previousProxy = previousValues[input.name]
    const value = input.type === 'secret' ? '' : input.type === 'boolean' ? input.default === true
      : input.type === 'proxy' && typeof previousProxy === 'string' && (previousProxy === '' || proxies.some(proxy => proxy.id === previousProxy))
        ? previousProxy : input.default ?? (input.type === 'proxy' && proxies.length === 1 ? proxies[0].id : '')
    return [input.name, value]
  }))
}

/** Convert the form without reflecting any supplied value in an error. */
export function serializeScriptInputs(inputs: ScriptInput[], values: ScriptInputValues): ScriptInputValues {
  const result: ScriptInputValues = {}
  for (const input of inputs) {
    const value = values[input.name]
    if (input.type === 'boolean') {
      if (typeof value !== 'boolean') throw new Error('Invalid script input')
      result[input.name] = value
      continue
    }
    if (value === undefined || value === '') {
      if (input.required) throw new Error('Invalid script input')
      continue
    }
    if (input.type === 'integer') {
      const number = typeof value === 'number' ? value : typeof value === 'string' && /^-?\d+$/.test(value) ? Number(value) : NaN
      if (!Number.isSafeInteger(number)) throw new Error('Invalid script input')
      result[input.name] = number
    } else {
      if (typeof value !== 'string') throw new Error('Invalid script input')
      if (input.type === 'select' && !input.choices?.some(choice => choice.value === value)) throw new Error('Invalid script input')
      if (input.type === 'proxy' && !/^[a-zA-Z0-9]{8}$/.test(value)) throw new Error('Invalid script input')
      result[input.name] = value
    }
  }
  return result
}

export function definitionForExport(script: SavedScript): ScriptDefinition {
  return {
    name: script.name, description: script.description, language: script.language, code: script.code,
    inputs: script.inputs.map(input => input.type === 'secret' ? { ...input, default: undefined } : { ...input }),
    stages: script.stages.map(stage => ({ ...stage })),
    max_runtime_seconds: script.max_runtime_seconds, enabled: script.enabled,
  }
}

const ROOT = '/api/v1/studio'
const options = { cache: 'no-store' as const }
const mutation = { ...options, headers: { 'X-Studio-Request': '1' } }
export const scriptsAPI = {
  list: () => apiRequest<{ scripts: ScriptSummary[] }>(`${ROOT}/scripts`, options),
  get: (id: string) => apiRequest<SavedScript>(`${ROOT}/scripts/${encodeURIComponent(id)}`, options),
  create: (definition: ScriptDefinition) => apiRequest<SavedScript>(`${ROOT}/scripts`, { ...mutation, method: 'POST', body: JSON.stringify(definition) }),
  update: (id: string, definition: ScriptDefinition, revision: number) => apiRequest<SavedScript>(`${ROOT}/scripts/${encodeURIComponent(id)}`, { ...mutation, method: 'PUT', body: JSON.stringify({ ...definition, revision }) }),
  runs: (scriptId: string) => apiRequest<{ runs: ScriptRun[] }>(`${ROOT}/scripts/${encodeURIComponent(scriptId)}/runs`, options),
  run: (id: string) => apiRequest<ScriptRun>(`${ROOT}/script-runs/${encodeURIComponent(id)}`, options),
  start: (id: string, inputs: ScriptInputValues, resumeOf?: string) => apiRequest<ScriptRun>(`${ROOT}/scripts/${encodeURIComponent(id)}/runs`, { ...mutation, method: 'POST', body: JSON.stringify({ inputs, ...(resumeOf ? { resume_of: resumeOf } : {}) }) }),
  stop: (id: string) => apiRequest<ScriptRun>(`${ROOT}/script-runs/${encodeURIComponent(id)}/stop`, { ...mutation, method: 'POST' }),
}
