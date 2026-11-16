import assert from 'node:assert/strict'
import test from 'node:test'
import { definitionForExport, initialScriptInputs, scriptsAPI, serializeScriptInputs, type SavedScript, type ScriptInput } from '../src/lib/scripts-api'

const fields: ScriptInput[] = [
  { name: 'account', label: 'Account', type: 'secret', required: true, default: 'must-not-fill' },
  { name: 'count', label: 'Count', type: 'integer', required: false, default: 2 },
  { name: 'mode', label: 'Mode', type: 'select', required: true, choices: [{ value: 'slow', label: 'Slow' }] },
  { name: 'visible', label: 'Visible', type: 'boolean', required: false, default: false },
]

test('run form never fills a secret default and keeps nonsecret typed defaults', () => {
  assert.deepEqual(initialScriptInputs(fields), { account: '', count: 2, mode: '', visible: false })
})

test('proxy inputs select a sole saved account, respect defaults, and leave multiple accounts unselected', () => {
  const input: ScriptInput = { name: 'connection', label: 'Connection', type: 'proxy', required: false }
  const first = { id: 'abcd1234' }
  const second = { id: 'efgh5678' }
  assert.deepEqual(initialScriptInputs([input]), { connection: '' })
  assert.deepEqual(initialScriptInputs([input], [first]), { connection: first.id })
  assert.deepEqual(initialScriptInputs([input], [first, second]), { connection: '' })
  assert.deepEqual(initialScriptInputs([{ ...input, default: second.id }], [first, second]), { connection: second.id })
  assert.deepEqual(initialScriptInputs([{ ...input, default: '' }], [first]), { connection: '' })
  assert.deepEqual(initialScriptInputs([{ ...input, default: null }], [first]), { connection: first.id })
})

test('form resets preserve saved proxy choices while clearing account input', () => {
  const inputs: ScriptInput[] = [...fields, { name: 'connection', label: 'Connection', type: 'proxy', required: false }]
  const proxies = [{ id: 'abcd1234' }, { id: 'efgh5678' }]
  const previous = { account: 'must-clear', connection: proxies[1].id, count: 55, mode: 'slow', visible: true }
  assert.deepEqual(initialScriptInputs(inputs, proxies, previous), {
    account: '', count: 2, mode: '', visible: false, connection: proxies[1].id,
  })
  assert.equal(initialScriptInputs(inputs, proxies, { connection: '' }).connection, '')
  assert.equal(initialScriptInputs(inputs, [proxies[0]], previous).connection, proxies[0].id)
})

test('proxy serialization only sends a saved account reference and validates required and optional inputs', () => {
  const input: ScriptInput = { name: 'connection', label: 'Connection', type: 'proxy', required: false }
  assert.deepEqual(serializeScriptInputs([input], { connection: 'abcd1234' }), { connection: 'abcd1234' })
  assert.deepEqual(serializeScriptInputs([input], { connection: '' }), {})
  for (const value of ['proxy.example:8080:user:private', 'abcd1234-extra', 'short', 12345678]) {
    assert.throws(() => serializeScriptInputs([input], { connection: value }), { message: 'Invalid script input' })
  }
  assert.throws(() => serializeScriptInputs([{ ...input, required: true }], { connection: '' }), { message: 'Invalid script input' })
})

test('run serialization preserves literal secrets and rejects invalid typed fields without echoing them', () => {
  const privateValue = 'exact:private$value'
  assert.deepEqual(serializeScriptInputs(fields, { account: privateValue, count: '3', mode: 'slow', visible: false }), {
    account: privateValue, count: 3, mode: 'slow', visible: false,
  })
  for (const override of [{ count: '3.2' }, { count: Number.MAX_SAFE_INTEGER + 1 }, { mode: privateValue }, { account: '' }]) {
    assert.throws(() => serializeScriptInputs(fields, { account: privateValue, count: 3, mode: 'slow', visible: false, ...override }), error => error instanceof Error && error.message === 'Invalid script input' && !error.message.includes(privateValue))
  }
})

test('definition export excludes runtime metadata and secret defaults', () => {
  const script: SavedScript = {
    id: 'local-script', revision: 3, name: 'Example', description: '', language: 'python', code: 'context.stage("ready")',
    inputs: fields, stages: [{ id: 'ready', label: 'Ready' }], max_runtime_seconds: 30, enabled: true,
    created_at: 'test', updated_at: 'test',
  }
  const exported = definitionForExport(script)
  assert.equal('id' in exported, false)
  assert.equal('revision' in exported, false)
  assert.equal(exported.inputs[0].default, undefined)
  assert.equal(exported.code, script.code)
  assert.equal(script.inputs[0].default, 'must-not-fill')
})

test('script execution uses the local mutation guard and keeps inputs out of the URL', async () => {
  const originalFetch = globalThis.fetch
  const calls: { url: string; options?: RequestInit }[] = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options })
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    await scriptsAPI.start('example-id', { account: 'private:account:record' })
    await scriptsAPI.stop('run-id')
    for (const call of calls) {
      const headers = new Headers(call.options?.headers)
      assert.equal(headers.get('X-Studio-Request'), '1')
      assert.equal(headers.get('Content-Type'), 'application/json')
      assert.equal(call.options?.cache, 'no-store')
      assert.equal(call.options?.method, 'POST')
      assert.equal(call.url.includes('private'), false)
    }
    assert.deepEqual(JSON.parse(String(calls[0].options?.body)), { inputs: { account: 'private:account:record' } })
  } finally { globalThis.fetch = originalFetch }
})
