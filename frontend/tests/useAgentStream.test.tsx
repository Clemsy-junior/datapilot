import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentStream } from '@/hooks/useAgentStream'
import type { AgentEvent } from '@/types'

/** A controllable `EventSource` double: tests decide what the server "sends". */
class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  readonly listeners = new Map<string, ((event: Event) => void)[]>()
  onerror: ((event: Event) => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, handler: (event: Event) => void): void {
    const existing = this.listeners.get(name) ?? []
    this.listeners.set(name, [...existing, handler])
  }

  close(): void {
    this.closed = true
  }

  /** Deliver one agent event, exactly as the browser would. */
  emit(event: AgentEvent): void {
    const handlers = this.listeners.get(event.type) ?? []
    const message = new MessageEvent(event.type, { data: JSON.stringify(event) })
    for (const handler of handlers) handler(message)
  }

  fail(): void {
    this.onerror?.(new Event('error'))
  }
}

function latest(): FakeEventSource {
  const instance = FakeEventSource.instances.at(-1)
  if (instance === undefined) throw new Error('aucun EventSource ouvert')
  return instance
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('useAgentStream', () => {
  it('opens one stream carrying the question and the dataset', () => {
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('Quelle région performe le mieux ?'))

    expect(FakeEventSource.instances).toHaveLength(1)
    const url = new URL(latest().url, 'http://localhost')
    expect(url.pathname).toBe('/api/chat/stream')
    expect(url.searchParams.get('message')).toBe('Quelle région performe le mieux ?')
    expect(url.searchParams.get('dataset_id')).toBe('sales')
    expect(result.current.isStreaming).toBe(true)
  })

  it('ignores an empty question and sends nothing', () => {
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('   '))

    expect(FakeEventSource.instances).toHaveLength(0)
    expect(result.current.isStreaming).toBe(false)
  })

  it('refuses to send while a stream is already open', () => {
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('première'))
    act(() => result.current.send('seconde'))

    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('builds the assistant message from the event stream', async () => {
    const { result } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('Quelle région performe le mieux ?'))
    const source = latest()

    act(() => {
      source.emit({ type: 'status', stage: 'thinking', message: 'Réflexion…', iteration: 1 })
      source.emit({
        type: 'tool_call',
        id: 'c1',
        name: 'aggregate',
        arguments: { group_by: ['region'] },
        iteration: 1,
      })
      source.emit({
        type: 'tool_result',
        id: 'c1',
        name: 'aggregate',
        ok: true,
        duration_ms: 2.5,
        result: { rows: [{ region: 'APAC', value: 5130 }] },
      })
      source.emit({
        type: 'chart',
        chart: {
          type: 'bar',
          title: 'CA par région',
          x_key: 'region',
          series: [{ key: 'value', label: 'CA' }],
          data: [{ region: 'APAC', value: 5130 }],
        },
      })
      source.emit({ type: 'text_delta', text: 'APAC domine' })
      source.emit({ type: 'text_delta', text: ' avec 5130.' })
      source.emit({
        type: 'done',
        conversation_id: 'conv_1',
        message_id: 'msg_1',
        stopped_reason: 'completed',
        iterations: 2,
      })
    })

    await waitFor(() => expect(result.current.isStreaming).toBe(false))

    const [question, answer] = result.current.messages
    expect(question?.role).toBe('user')
    expect(answer?.content).toBe('APAC domine avec 5130.')
    expect(answer?.toolInvocations).toHaveLength(1)
    expect(answer?.toolInvocations[0]?.duration_ms).toBe(2.5)
    expect(answer?.charts).toHaveLength(1)
    expect(answer?.pending).toBe(false)
    expect(answer?.id).toBe('msg_1')
    expect(result.current.conversationId).toBe('conv_1')
    expect(source.closed).toBe(true)
  })

  it('reuses the conversation id on the next question', () => {
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('première'))
    act(() =>
      latest().emit({
        type: 'done',
        conversation_id: 'conv_42',
        message_id: 'msg_1',
        stopped_reason: 'completed',
        iterations: 1,
      }),
    )
    act(() => result.current.send('seconde'))

    const url = new URL(latest().url, 'http://localhost')
    expect(url.searchParams.get('conversation_id')).toBe('conv_42')
  })

  it('surfaces a server error event and stops streaming', async () => {
    const { result } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('question'))

    act(() =>
      latest().emit({
        type: 'error',
        code: 'llm_error',
        message: "Le service d'IA est injoignable.",
        detail: 'connexion refusée',
      }),
    )

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.messages[1]?.error?.code).toBe('llm_error')
    expect(result.current.messages[1]?.pending).toBe(false)
  })

  it('records why the agent stopped early', async () => {
    const { result } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('question'))

    act(() =>
      latest().emit({
        type: 'done',
        conversation_id: 'conv_1',
        message_id: 'msg_1',
        stopped_reason: 'max_iterations',
        iterations: 8,
      }),
    )

    await waitFor(() => expect(result.current.messages[1]?.stoppedReason).toBe('max_iterations'))
  })

  it('retries once when the connection dies before any event', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('question'))
    act(() => latest().fail())
    act(() => void vi.advanceTimersByTime(1000))

    expect(FakeEventSource.instances).toHaveLength(2)
  })

  it('gives up loudly rather than replaying a turn that already started', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useAgentStream('sales'))

    act(() => result.current.send('question'))
    act(() =>
      latest().emit({ type: 'status', stage: 'thinking', message: 'Réflexion…', iteration: 1 }),
    )
    act(() => latest().fail())
    act(() => void vi.advanceTimersByTime(2000))

    // No second stream: re-running the agent would double the cost and the answer.
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(result.current.messages[1]?.error?.code).toBe('stream_closed')
  })

  it('cancels a stream on demand', () => {
    const { result } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('question'))
    const source = latest()

    act(() => result.current.cancel())

    expect(source.closed).toBe(true)
    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages[1]?.content).toBe('Analyse interrompue.')
  })

  it('clears everything on reset', () => {
    const { result } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('question'))

    act(() => result.current.reset())

    expect(result.current.messages).toHaveLength(0)
    expect(result.current.conversationId).toBeNull()
    expect(latest().closed).toBe(true)
  })

  it('closes the stream when the component unmounts', () => {
    const { result, unmount } = renderHook(() => useAgentStream('sales'))
    act(() => result.current.send('question'))
    const source = latest()

    unmount()

    expect(source.closed).toBe(true)
  })

  it('does nothing without a dataset', () => {
    const { result } = renderHook(() => useAgentStream(null))

    act(() => result.current.send('question'))

    expect(FakeEventSource.instances).toHaveLength(0)
  })
})
