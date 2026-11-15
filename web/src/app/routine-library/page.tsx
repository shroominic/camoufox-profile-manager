'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpen, RefreshCw, Search } from 'lucide-react'

import { useI18n } from '@/components/i18n-provider'
import { routineLibraryAPI, type RoutineLibrary, type RoutinePool } from '@/lib/routine-library-api'

const SOURCE_MESSAGES = {
  'not-configured': 'The routine library is not connected. Set its catalog in the Studio launcher.',
  missing: 'The routine catalog is not available. Check its location, then refresh.',
  invalid: 'The routine catalog could not be read. Check the catalog, then refresh.',
}

function seconds(value: number) {
  return `${value < 10 ? value.toFixed(2) : value.toFixed(1)} s`
}

function duration(pools: RoutinePool[]) {
  if (!pools.length) return '—'
  return `${seconds(Math.min(...pools.map(pool => pool.minSeconds)))} – ${seconds(Math.max(...pools.map(pool => pool.maxSeconds)))}`
}

export default function RoutineLibraryPage() {
  const { t } = useI18n()
  const [library, setLibrary] = useState<RoutineLibrary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [length, setLength] = useState('')
  const [direction, setDirection] = useState('')
  const [status, setStatus] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setLibrary(await routineLibraryAPI.get())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The routine library could not load.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const rows = useMemo(() => (library?.routines ?? []).flatMap(routine => {
    if (!`${routine.title} ${routine.kind}`.toLowerCase().includes(search.toLowerCase())) return []
    const pools = routine.pools.filter(pool =>
      (!length || pool.length === length) &&
      (!direction || pool.direction === direction) &&
      (!status || pool.status === status),
    )
    if (!pools.length && (length || direction || status)) return []
    return [{ ...routine, pools, matching: pools.reduce((sum, pool) => sum + pool.count, 0) }]
  }), [library, search, length, direction, status])

  return (
    <>
      <header className="sticky top-0 z-20 flex min-h-[52px] flex-wrap items-center gap-3 border-b border-line bg-canvas/85 px-5 py-2 backdrop-blur">
        <BookOpen size={17} className="text-signal" aria-hidden="true" />
        <h1 className="text-[14px] font-semibold">{t('Routine library')}</h1>
        <span className="rounded border border-line px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-dim">{t('Read only')}</span>
        <button type="button" onClick={() => void load()} disabled={loading} className="btn btn-default ml-auto">
          <RefreshCw size={13} aria-hidden="true" /> {t('Refresh')}
        </button>
      </header>

      <div className="space-y-5 p-5">
        <div>
          <p className="text-ink-dim">{t('Browse recorded routines and their short, medium, and long variants.')}</p>
          <p className="mt-1 text-[12px] text-ink-faint">{t('Counts show the current catalog. Playback is not started from this page.')}</p>
        </div>

        {error && <div role="alert" className="rounded-lg border border-line bg-surface p-4 text-ink">{t(error)}</div>}
        {loading && !library && <p role="status" className="text-ink-dim">{t('Loading routine library…')}</p>}
        {library && library.status !== 'ready' && <div role="status" className="rounded-lg border border-line bg-surface p-5">{t(SOURCE_MESSAGES[library.status])}</div>}

        {library?.status === 'ready' && <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5" aria-label={t('Library totals')}>
            {[
              ['Routine types', library.routineCount],
              ['Recorded variants', library.variantCount],
              ['Ready in catalog', library.readyCount],
              ['Need review', library.reviewCount],
              ['Source candidates', library.candidateCount],
            ].map(([label, value]) => <div key={label} className="rounded-lg border border-line bg-surface px-4 py-3">
              <div className="font-mono text-[24px] font-medium text-ink">{Number(value).toLocaleString()}</div>
              <div className="mt-1 text-[12px] text-ink-dim">{t(String(label))}</div>
            </div>)}
          </div>

          <div className="flex flex-wrap items-center gap-2" aria-label={t('Routine filters')}>
            <label className="relative min-w-[200px] flex-1">
              <Search size={13} className="pointer-events-none absolute left-2.5 top-2.5 text-ink-faint" aria-hidden="true" />
              <input className="field pl-8" aria-label={t('Search routines')} placeholder={t('Search routines')} value={search} onChange={event => setSearch(event.target.value)} />
            </label>
            <select className="field w-auto" aria-label={t('Length')} value={length} onChange={event => setLength(event.target.value)}>
              <option value="">{t('All lengths')}</option>
              <option value="short">{t('Short')}</option>
              <option value="medium">{t('Medium')}</option>
              <option value="long">{t('Long')}</option>
              <option value="any">{t('Unbanded')}</option>
            </select>
            <select className="field w-auto" aria-label={t('Direction')} value={direction} onChange={event => setDirection(event.target.value)}>
              <option value="">{t('All directions')}</option>
              <option value="down">{t('Down')}</option>
              <option value="up">{t('Up')}</option>
              <option value="mixed">{t('Mixed')}</option>
              <option value="horizontal">{t('Horizontal')}</option>
              <option value="none">{t('No direction')}</option>
            </select>
            <select className="field w-auto" aria-label={t('Readiness')} value={status} onChange={event => setStatus(event.target.value)}>
              <option value="">{t('All catalog states')}</option>
              <option value="ready">{t('Ready')}</option>
              <option value="review">{t('Needs review')}</option>
            </select>
          </div>

          <div className="overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full border-collapse text-left text-[12px]">
              <thead className="bg-raised/40 text-ink-dim"><tr>
                {['Routine', 'Matching variants', 'Duration', 'Available pools'].map(label => <th key={label} className="px-4 py-3 font-medium">{t(label)}</th>)}
              </tr></thead>
              <tbody>
                {rows.map(routine => <tr key={routine.key} className="border-t border-line align-top">
                  <td className="min-w-[210px] px-4 py-3">
                    <div className="font-medium text-ink">{t(routine.title)}</div>
                    <div className="mt-1 text-ink-faint">{routine.parameterized ? t('Private login routine') : t(routine.kind)}</div>
                  </td>
                  <td className="px-4 py-3 font-mono text-ink">{routine.parameterized ? t('Parameters required') : routine.matching}</td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-ink-dim">{duration(routine.pools)}</td>
                  <td className="px-4 py-3">
                    <div className="flex max-w-[580px] flex-wrap gap-1.5">
                      {routine.pools.map(pool => <span key={`${pool.length}-${pool.direction}-${pool.status}`} className={`rounded border border-line px-2 py-1 text-[11px] ${pool.status === 'ready' ? 'bg-raised text-ink' : 'text-ink-faint'}`}>
                        {t(pool.length === 'any' ? 'Unbanded' : pool.length)}{pool.direction !== 'none' ? ` · ${t(pool.direction)}` : ''} · {pool.count} · {t(pool.status === 'ready' ? 'Ready' : 'Needs review')}
                      </span>)}
                      {routine.parameterized && <span className="text-ink-faint">{t('Email, password, and authenticator input stay private.')}</span>}
                    </div>
                  </td>
                </tr>)}
                {!rows.length && <tr><td colSpan={4} className="px-4 py-8 text-center text-ink-dim">{t('No routines match these filters.')}</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-ink-faint">{t('Short, medium, and long use each routine’s recorded duration bands. Review variants require a target and page-state review before use.')}</p>
        </>}
      </div>
    </>
  )
}
