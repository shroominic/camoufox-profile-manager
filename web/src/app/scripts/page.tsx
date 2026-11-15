'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Code2, Download, Pencil, Play, Plus, RefreshCw, Square, Upload, X } from 'lucide-react'

import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { profilesAPI } from '@/lib/api'
import {
  definitionForExport, initialScriptInputs, scriptsAPI, serializeScriptInputs,
  type SavedScript, type ScriptDefinition, type ScriptInput, type ScriptInputValues,
  type ScriptRun, type ScriptRunStatus, type ScriptSummary,
} from '@/lib/scripts-api'

const ACTIVE = new Set<ScriptRunStatus>(['queued', 'running', 'stopping'])
const RESUMABLE = new Set<ScriptRunStatus>(['failed', 'cancelled', 'interrupted'])
const STATUS_LABELS: Record<ScriptRunStatus, string> = {
  queued: 'Queued', running: 'Running', stopping: 'Stopping', succeeded: 'Succeeded',
  failed: 'Failed', cancelled: 'Stopped', interrupted: 'Interrupted',
}
const ERROR_LABELS: Record<string, string> = {
  script_error: 'The script stopped with an error. Check its saved recovery state before retrying.',
  timeout: 'The run reached its time limit. Check the saved profile before retrying.',
  stopped: 'The run was stopped. Some actions may have completed.',
  interrupted: 'The app stopped during this run. It was not restarted automatically.',
  worker_unavailable: 'The script worker could not start. Check the local tool setup.',
  worker_protocol_error: 'The script worker could not finish. Check the saved profile before retrying.',
  dependency_unavailable: 'A script dependency is unavailable. Check the local tool setup.',
  preflight_failed: 'The script could not pass its startup checks. No new run should be started until its saved state is checked.',
  invalid_inputs: 'The script inputs were not valid. Enter them again before retrying.',
  login_target_changed: 'The login page or target changed. The saved profile is kept. Review the page before resuming.',
  login_field_focus: 'The login field lost focus. The saved profile is kept. Review the page before resuming.',
  login_field_type: 'The login field type did not match the expected input. No further credentials were sent.',
  login_page_timeout: 'The next login page did not finish loading. The saved profile is kept.',
  login_manual_action: 'Login requires manual action. Check the browser for a challenge, account notice, or verification choice.',
  login_state_unavailable: 'The login page state could not be read. The saved profile is kept.',
}
const EMPTY_DEFINITION: ScriptDefinition = {
  name: '', description: '', language: 'python', code: 'context.stage("started")\n', inputs: [],
  stages: [{ id: 'started', label: 'Started' }], max_runtime_seconds: 900, enabled: true,
}
type Editor = { id?: string; revision?: number; definition: ScriptDefinition; inputsJSON: string; stagesJSON: string }
type ProfileChoice = { id: string; name: string }

function editorFor(definition: ScriptDefinition, id?: string, revision?: number): Editor {
  return { id, revision, definition, inputsJSON: JSON.stringify(definition.inputs, null, 2), stagesJSON: JSON.stringify(definition.stages, null, 2) }
}

function date(value?: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

export default function ScriptsPage() {
  const { t } = useI18n()
  const [scripts, setScripts] = useState<ScriptSummary[]>([])
  const [selected, setSelected] = useState<SavedScript | null>(null)
  const [runs, setRuns] = useState<ScriptRun[]>([])
  const [profiles, setProfiles] = useState<ProfileChoice[]>([])
  const [values, setValues] = useState<ScriptInputValues>({})
  const [resumeOf, setResumeOf] = useState<string | undefined>()
  const [editor, setEditor] = useState<Editor | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [exportJSON, setExportJSON] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const selectionRequest = useRef(0)
  const mounted = useRef(true)

  const refreshList = useCallback(async () => {
    const result = await scriptsAPI.list()
    if (mounted.current) setScripts(result.scripts)
    return result.scripts
  }, [])

  const selectScript = useCallback(async (id: string) => {
    const request = ++selectionRequest.current
    setValues({})
    setResumeOf(undefined)
    setRuns([])
    setSelected(null)
    setError(null)
    try {
      const [script, history] = await Promise.all([scriptsAPI.get(id), scriptsAPI.runs(id)])
      if (!mounted.current || request !== selectionRequest.current) return
      setSelected(script)
      setValues(initialScriptInputs(script.inputs))
      setRuns(history.runs)
    } catch {
      if (mounted.current && request === selectionRequest.current) setError('The script could not load. Refresh and try again.')
    }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await refreshList()
      if (!mounted.current) return
      const target = selected?.id ?? list[0]?.id
      if (target) await selectScript(target)
    } catch {
      if (mounted.current) setError('The script library could not load. Check the app connection.')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [refreshList, selectScript, selected])

  useEffect(() => {
    mounted.current = true
    let cancelled = false
    void (async () => {
      try {
        const list = await refreshList()
        if (cancelled) return
        if (list[0]) await selectScript(list[0].id)
        const choices: ProfileChoice[] = []
        for (let page = 1; page <= 100; page++) {
          const response = await profilesAPI.getProfiles({ page, per_page: 100 })
          if (cancelled) return
          choices.push(...response.profiles.map(profile => ({ id: profile.id, name: profile.name })))
          if (!response.has_next) break
        }
        if (mounted.current) setProfiles(choices)
      } catch {
        if (mounted.current) setError('The script library could not load. Check the app connection.')
      } finally {
        if (mounted.current) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      mounted.current = false
    }
  }, [refreshList, selectScript])

  const hasActiveRun = runs.some(run => ACTIVE.has(run.status))
  useEffect(() => {
    if (!selected || !hasActiveRun) return
    let stopped = false
    let pending = false
    const scriptId = selected.id
    const timer = window.setInterval(async () => {
      if (pending) return
      pending = true
      try {
        const response = await scriptsAPI.runs(scriptId)
        if (!stopped) setRuns(response.runs)
      } catch {
        if (!stopped) setError('Run status could not refresh. The worker may still be active.')
      } finally { pending = false }
    }, 1500)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [selected, hasActiveRun])

  function setInput(input: ScriptInput, value: string | boolean) {
    setValues(previous => ({ ...previous, [input.name]: value }))
  }

  async function start(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || busy || hasActiveRun) return
    let input: ScriptInputValues
    try { input = serializeScriptInputs(selected.inputs, values) }
    catch { setError('Check the required script inputs. No run was started.'); return }
    const scriptId = selected.id
    const resume = resumeOf
    // Clear every input before the request completes; never keep account values in history.
    setValues(initialScriptInputs(selected.inputs))
    setResumeOf(undefined)
    setBusy(true)
    setError(null)
    try {
      const run = await scriptsAPI.start(scriptId, input, resume)
      if (mounted.current) setRuns(previous => [run, ...previous.filter(item => item.id !== run.id)])
    } catch {
      if (mounted.current) setError('The run was not confirmed. Refresh its history before trying again.')
    } finally {
      // Release the submitted object after fetch has consumed its serialized body.
      for (const key of Object.keys(input)) delete input[key]
      if (mounted.current) setBusy(false)
    }
  }

  async function stop(run: ScriptRun) {
    setBusy(true)
    setError(null)
    try {
      const updated = await scriptsAPI.stop(run.id)
      if (mounted.current) setRuns(previous => previous.map(item => item.id === run.id ? updated : item))
    } catch {
      if (mounted.current) setError('Stop was not confirmed. Refresh the run status before taking further action.')
    } finally { if (mounted.current) setBusy(false) }
  }

  function prepareResume(run: ScriptRun) {
    if (!selected || run.revision !== selected.revision) return
    setValues(initialScriptInputs(selected.inputs))
    setResumeOf(run.id)
    setError(null)
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editor) return
    let definition: ScriptDefinition
    try {
      const inputs: unknown = JSON.parse(editor.inputsJSON)
      const stages: unknown = JSON.parse(editor.stagesJSON)
      if (!Array.isArray(inputs) || !Array.isArray(stages)) throw new Error()
      definition = { ...editor.definition, inputs, stages }
    } catch { setFormError('Inputs and stages must be valid JSON arrays.'); return }
    setSaving(true)
    setFormError(null)
    try {
      const saved = editor.id
        ? await scriptsAPI.update(editor.id, definition, editor.revision!)
        : await scriptsAPI.create(definition)
      setEditor(null)
      await refreshList()
      await selectScript(saved.id)
    } catch {
      setFormError('The script could not be saved. Check its definition and reload if another edit was saved.')
    } finally { setSaving(false) }
  }

  async function importDefinition(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.size > 512 * 1024) { setError('Choose a script JSON file smaller than 512 KB.'); return }
    try {
      const parsed = JSON.parse(await file.text())
      if (!parsed || typeof parsed !== 'object' || typeof parsed.name !== 'string' || typeof parsed.code !== 'string' || parsed.language !== 'python' || !Array.isArray(parsed.inputs) || !Array.isArray(parsed.stages)) throw new Error()
      const definition: ScriptDefinition = {
        name: parsed.name, description: typeof parsed.description === 'string' ? parsed.description : '', language: 'python',
        code: parsed.code, inputs: parsed.inputs, stages: parsed.stages,
        max_runtime_seconds: parsed.max_runtime_seconds ?? 900, enabled: parsed.enabled ?? true,
      }
      setFormError(null)
      setEditor(editorFor(definition))
    } catch { setError('The file is not a valid script definition. Nothing was imported.'); }
  }

  function editDefinition(key: keyof ScriptDefinition, value: string | number | boolean) {
    setEditor(previous => previous ? { ...previous, definition: { ...previous.definition, [key]: value } } : null)
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex min-h-[52px] flex-wrap items-center gap-2 border-b border-line bg-canvas/85 px-5 py-2 backdrop-blur">
        <Code2 size={17} className="text-signal" aria-hidden="true" />
        <h1 className="text-[14px] font-semibold">{t('Scripts')}</h1>
        <span className="font-mono text-ink-faint">{scripts.length}</span>
        <button type="button" className="btn btn-default ml-auto" disabled={loading || busy} onClick={() => void refresh()}><RefreshCw size={13} aria-hidden="true" />{t('Refresh')}</button>
        <button type="button" className="btn btn-default" onClick={() => fileInput.current?.click()}><Upload size={13} aria-hidden="true" />{t('Import JSON')}</button>
        <input ref={fileInput} type="file" accept="application/json,.json" className="hidden" onChange={event => void importDefinition(event)} aria-label={t('Import script definition')} />
        <button type="button" className="btn btn-primary" onClick={() => { setFormError(null); setEditor(editorFor(EMPTY_DEFINITION)) }}><Plus size={13} aria-hidden="true" />{t('New script')}</button>
      </header>

      <div className="space-y-4 p-5">
        <p className="text-[12px] text-ink-dim">{t('Scripts are saved in the local database. Each run uses a fixed revision and runs with your Mac user permissions.')}</p>
        {error && <div role="alert" className="rounded-lg border border-line bg-surface p-3 text-ink">{t(error)}</div>}
        {loading && !scripts.length && <p role="status" className="text-ink-dim">{t('Loading scripts…')}</p>}
        {!loading && !scripts.length && <div className="rounded-lg border border-line bg-surface p-8 text-center">
          <h2 className="font-semibold">{t('No scripts yet')}</h2>
          <p className="mt-2 text-ink-dim">{t('Create a script or import its JSON definition. Saving a script does not run it.')}</p>
        </div>}

        {!!scripts.length && <div className="grid items-start gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
          <section className="overflow-hidden rounded-lg border border-line bg-surface" aria-label={t('Saved scripts')}>
            {scripts.map(script => <button type="button" key={script.id} disabled={busy} onClick={() => void selectScript(script.id)} aria-pressed={script.id === selected?.id}
              className={`block w-full border-b border-line px-4 py-3 text-left last:border-b-0 ${script.id === selected?.id ? 'bg-raised' : 'hover:bg-raised/50'}`}>
              <span className="block font-medium text-ink">{script.name}</span>
              <span className="mt-1 block text-[11px] text-ink-faint">{t('Revision {revision}', { revision: script.revision })}{!script.enabled ? ` · ${t('Disabled')}` : ''}</span>
            </button>)}
          </section>

          {selected && <div className="min-w-0 space-y-4">
            <section className="rounded-lg border border-line bg-surface p-4">
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1"><h2 className="text-[16px] font-semibold">{selected.name}</h2><p className="mt-1 whitespace-pre-wrap text-ink-dim">{selected.description}</p></div>
                <button type="button" className="btn btn-default" disabled={busy} onClick={() => { setFormError(null); setEditor(editorFor(definitionForExport(selected), selected.id, selected.revision)) }}><Pencil size={13} aria-hidden="true" />{t('Edit')}</button>
                <button type="button" className="btn btn-default" onClick={() => setExportJSON(JSON.stringify(definitionForExport(selected), null, 2))}><Download size={13} aria-hidden="true" />{t('Export JSON')}</button>
              </div>
              <div className="mt-3 flex flex-wrap gap-4 text-[11px] text-ink-faint"><span>{t('Revision {revision}', { revision: selected.revision })}</span><span>{t('Time limit: {seconds} seconds', { seconds: selected.max_runtime_seconds })}</span></div>
              {selected.stages.length > 0 && <ol className="mt-4 flex flex-wrap gap-2" aria-label={t('Script stages')}>
                {selected.stages.map((stage, index) => <li key={stage.id} className="rounded border border-line px-2 py-1 text-[11px] text-ink-dim">{index + 1}. {stage.label}</li>)}
              </ol>}
            </section>

            <section className="rounded-lg border border-line bg-surface p-4">
              <h2 className="font-semibold">{resumeOf ? t('Resume with fresh inputs') : t('Run script')}</h2>
              <p className="mt-1 text-[11px] text-ink-faint">{t('Run inputs stay in memory and are cleared after submission. Credentials are not saved in run history.')}</p>
              {resumeOf && <div className="mt-3 rounded border border-line p-3 text-[12px]">
                <div className="flex items-center gap-2"><span>{t('Resume run {id}', { id: resumeOf.slice(0, 8) })}</span><button type="button" className="ml-auto rounded p-1 hover:bg-raised" aria-label={t('Cancel resume')} onClick={() => setResumeOf(undefined)}><X size={13} /></button></div>
                <p className="mt-2 text-ink-faint">{t('Resume reuses the saved run state. Enter private inputs again. A saved profile or browser window may still be open.')}</p>
              </div>}
              <form onSubmit={event => void start(event)} autoComplete="off" className="mt-4 space-y-3">
                {selected.inputs.map(input => <label key={input.name} className="block">
                  <span className="mb-1 block text-[12px] text-ink">{input.label}{input.required ? ' *' : ''}</span>
                  {input.type === 'boolean'
                    ? <input type="checkbox" checked={values[input.name] === true} onChange={event => setInput(input, event.target.checked)} disabled={busy || hasActiveRun} aria-label={input.label} />
                    : input.type === 'select' || input.type === 'profile'
                      ? <select className="field" required={input.required} value={String(values[input.name] ?? '')} onChange={event => setInput(input, event.target.value)} disabled={busy || hasActiveRun} aria-label={input.label}>
                        <option value="">{input.required ? t('Choose an option') : t('Automatic / not set')}</option>
                        {(input.type === 'profile' ? profiles.map(profile => ({ value: profile.id, label: `${profile.name} (${profile.id})` })) : input.choices ?? []).map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                      </select>
                      : <input className="field" type={input.type === 'secret' ? 'password' : input.type === 'integer' ? 'number' : 'text'} step={input.type === 'integer' ? 1 : undefined}
                        value={String(values[input.name] ?? '')} onChange={event => setInput(input, event.target.value)} required={input.required} disabled={busy || hasActiveRun}
                        autoComplete="off" autoCorrect="off" autoCapitalize="none" spellCheck={false} aria-label={input.label} data-1p-ignore data-lpignore="true" />}
                  {input.help && <span className="mt-1 block text-[11px] text-ink-faint">{input.help}</span>}
                </label>)}
                <button type="submit" className="btn btn-primary" disabled={!selected.enabled || busy || hasActiveRun}><Play size={13} aria-hidden="true" />{busy ? t('Submitting…') : resumeOf ? t('Resume run') : t('Run script')}</button>
                {!selected.enabled && <p className="text-[12px] text-ink-dim">{t('This script is disabled. Enable it in the editor to run it.')}</p>}
              </form>
            </section>

            <section className="overflow-hidden rounded-lg border border-line bg-surface">
              <h2 className="border-b border-line px-4 py-3 font-semibold">{t('Recent runs')}</h2>
              {!runs.length && <p className="px-4 py-5 text-ink-dim">{t('No runs yet. The script has not been started from this app.')}</p>}
              {runs.map(run => <article key={run.id} className="border-b border-line p-4 last:border-b-0">
                <div className="flex flex-wrap items-center gap-2"><span className="rounded border border-line px-2 py-0.5 text-[11px]">{t(STATUS_LABELS[run.status] ?? 'Unknown')}</span><span className="font-mono text-[11px] text-ink-faint">{run.id.slice(0, 8)}</span><span className="text-[11px] text-ink-faint">{t('Revision {revision}', { revision: run.revision })}</span>
                  {ACTIVE.has(run.status) && <button type="button" className="btn btn-default ml-auto" disabled={busy || run.status === 'stopping'} onClick={() => void stop(run)}><Square size={12} aria-hidden="true" />{t('Stop')}</button>}
                  {RESUMABLE.has(run.status) && run.revision === selected.revision && !hasActiveRun && <button type="button" className="btn btn-default ml-auto" disabled={busy} onClick={() => prepareResume(run)}>{t('Prepare resume')}</button>}
                </div>
                <p className="mt-2 text-[12px] text-ink-dim">{selected.stages.find(stage => stage.id === run.stage)?.label ?? run.stage ?? t('Waiting to start')}</p>
                <p className="mt-1 text-[11px] text-ink-faint">{date(run.started_at ?? run.created_at)}{run.finished_at ? ` – ${date(run.finished_at)}` : ''}</p>
                {run.error_code && <p className="mt-2 text-[12px] text-ink-dim">{t(ERROR_LABELS[run.error_code] ?? 'The run needs attention. Check its saved state before starting another run.')}</p>}
                {run.result && Object.keys(run.result).length > 0 && <details className="mt-2"><summary className="cursor-pointer text-[12px] text-ink-dim">{t('Run result')}</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap rounded bg-canvas p-3 text-[11px] text-ink-faint">{JSON.stringify(run.result, null, 2)}</pre></details>}
              </article>)}
            </section>
          </div>}
        </div>}
      </div>

      <Modal open={editor !== null} onClose={() => { if (!saving) setEditor(null) }} title={editor?.id ? t('Edit script') : t('New script')} width={900}>
        {editor && <form onSubmit={event => void save(event)} className="space-y-4">
          <p className="text-[12px] text-ink-dim">{t('Review the code before saving. Put account values in run inputs, not in the script source.')}</p>
          {formError && <p role="alert" className="text-[12px] text-ink">{t(formError)}</p>}
          <label className="block text-[12px]">{t('Name')}<input className="field mt-1" required maxLength={160} value={editor.definition.name} onChange={event => editDefinition('name', event.target.value)} /></label>
          <label className="block text-[12px]">{t('Description')}<textarea className="field mt-1" rows={2} value={editor.definition.description} onChange={event => editDefinition('description', event.target.value)} /></label>
          <label className="block text-[12px]">{t('Python source')}<textarea className="field mt-1 font-mono text-[11px]" required rows={12} value={editor.definition.code} onChange={event => editDefinition('code', event.target.value)} autoComplete="off" spellCheck={false} /></label>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block text-[12px]">{t('Inputs JSON')}<textarea className="field mt-1 font-mono text-[11px]" rows={8} value={editor.inputsJSON} onChange={event => setEditor({ ...editor, inputsJSON: event.target.value })} spellCheck={false} /></label>
            <label className="block text-[12px]">{t('Stages JSON')}<textarea className="field mt-1 font-mono text-[11px]" rows={8} value={editor.stagesJSON} onChange={event => setEditor({ ...editor, stagesJSON: event.target.value })} spellCheck={false} /></label>
          </div>
          <p className="text-[11px] text-ink-faint">{t('Input fields use name, label, type, required, help, default, and choices. Secret fields cannot have defaults. Stages use id and label.')}</p>
          <div className="flex flex-wrap items-end gap-4">
            <label className="text-[12px]">{t('Time limit in seconds')}<input className="field mt-1 w-40" type="number" min={5} max={7200} required value={editor.definition.max_runtime_seconds} onChange={event => editDefinition('max_runtime_seconds', Number(event.target.value))} /></label>
            <label className="flex items-center gap-2 pb-2 text-[12px]"><input type="checkbox" checked={editor.definition.enabled} onChange={event => editDefinition('enabled', event.target.checked)} />{t('Enabled')}</label>
          </div>
          <div className="flex justify-end gap-2"><button type="button" className="btn btn-default" disabled={saving} onClick={() => setEditor(null)}>{t('Cancel')}</button><button type="submit" className="btn btn-primary" disabled={saving}>{saving ? t('Saving…') : t('Save script')}</button></div>
        </form>}
      </Modal>

      <Modal open={exportJSON !== null} onClose={() => setExportJSON(null)} title={t('Export script definition')} width={900}>
        <p className="mb-3 text-[12px] text-ink-dim">{t('This JSON contains the script definition only. Run inputs and run history are excluded.')}</p>
        <textarea className="field font-mono text-[11px]" rows={20} value={exportJSON ?? ''} readOnly aria-label={t('Script definition JSON')} onFocus={event => event.target.select()} />
        <div className="mt-3 flex justify-end"><button type="button" className="btn btn-default" onClick={() => setExportJSON(null)}>{t('Close')}</button></div>
      </Modal>
    </>
  )
}
