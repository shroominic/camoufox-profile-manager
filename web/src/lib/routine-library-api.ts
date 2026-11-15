import { apiRequest } from './api'

export interface RoutinePool {
  length: 'any' | 'short' | 'medium' | 'long'
  direction: 'none' | 'down' | 'up' | 'mixed' | 'horizontal'
  status: 'ready' | 'review'
  count: number
  minSeconds: number
  maxSeconds: number
}

export interface RoutineSummary {
  key: string
  title: string
  kind: string
  parameterized: boolean
  variantCount: number
  readyCount: number
  reviewCount: number
  candidateCount: number
  pools: RoutinePool[]
}

export interface RoutineLibrary {
  status: 'ready' | 'not-configured' | 'missing' | 'invalid'
  readOnly: true
  routineCount: number
  variantCount: number
  readyCount: number
  reviewCount: number
  candidateCount: number
  routines: RoutineSummary[]
}

export const routineLibraryAPI = {
  async get(): Promise<RoutineLibrary> {
    return (await apiRequest<{ data: RoutineLibrary }>('/api/v1/routine-library', {
      cache: 'no-store',
    })).data
  },
}
