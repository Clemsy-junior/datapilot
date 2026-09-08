/**
 * `useAgentStream` — the browser side of the agent loop.
 *
 * Wraps a single `EventSource`, turns each SSE event into UI state, and owns the
 * lifetime of the connection: one stream at a time, closed on completion, on
 * cancellation and on unmount.
 *
 * On reconnection, deliberately conservative. `EventSource` reconnects by
 * default, but this endpoint is not a passive feed: a reconnect *re-runs the
 * whole agent turn*, which costs tokens and would duplicate the answer. So the
 * hook retries once, only if the stream died before delivering a single event
 * (a genuine "connection never came up"), and gives up loudly otherwise.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { chatStreamUrl } from '@/api/client'
import type {
  AgentEvent,
  ChartEvent,
  DoneEvent,
  ErrorEvent as AgentErrorEvent,
  LiveMessage,
  StatusEvent,
  TextDeltaEvent,
  ToolCallEvent,
  ToolInvocation,
  ToolResultEvent,
} from '@/types'
import { AGENT_EVENT_NAMES } from '@/types'

/** How many times a stream that never produced an event is retried. */
const MAX_RETRIES = 1
const RETRY_DELAY_MS = 800

let messageCounter = 0

function nextId(prefix: string): string {
  messageCounter += 1
  return `${prefix}_${messageCounter}`
}

function userMessage(text: string): LiveMessage {
  return {
    id: nextId('local_user'),
    role: 'user',
    content: text,
    toolInvocations: [],
    charts: [],
    pending: false,
  }
}

function pendingAssistant(): LiveMessage {
  return {
    id: nextId('local_assistant'),
    role: 'assistant',
    content: '',
    toolInvocations: [],
    charts: [],
    status: 'Connexion à l’agent…',
    pending: true,
  }
}

export interface UseAgentStreamResult {
  messages: LiveMessage[]
  isStreaming: boolean
  conversationId: string | null
  send: (text: string) => void
  cancel: () => void
  reset: () => void
}

/**
 * Drive one conversation with the agent.
 *
 * @param datasetId dataset the questions are asked about; `null` disables sending.
 */
export function useAgentStream(datasetId: string | null): UseAgentStreamResult {
  const [messages, setMessages] = useState<LiveMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)

  const sourceRef = useRef<EventSource | null>(null)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const conversationRef = useRef<string | null>(null)

  const closeStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  /** Apply one agent event to the assistant message currently being built. */
  const applyEvent = useCallback((event: AgentEvent) => {
    setMessages((current) => {
      const index = current.findIndex((message) => message.pending)
      if (index === -1) return current
      const target = current[index]
      if (target === undefined) return current

      const updated = reduceMessage(target, event)
      if (updated === target) return current
      const next = [...current]
      next[index] = updated
      return next
    })

    if (event.type === 'done') {
      conversationRef.current = event.conversation_id
      setConversationId(event.conversation_id)
    }
  }, [])

  const openStream = useCallback(
    (text: string, attempt: number) => {
      if (datasetId === null) return

      let receivedAnything = false
      const source = new EventSource(
        chatStreamUrl(text, datasetId, conversationRef.current ?? undefined),
      )
      sourceRef.current = source

      for (const name of AGENT_EVENT_NAMES) {
        source.addEventListener(name, (raw: Event) => {
          receivedAnything = true
          const parsed = parseEvent(raw)
          if (parsed === null) return
          applyEvent(parsed)
          if (parsed.type === 'done' || parsed.type === 'error') {
            closeStream()
            setIsStreaming(false)
          }
        })
      }

      source.onerror = () => {
        closeStream()
        if (!receivedAnything && attempt < MAX_RETRIES) {
          retryTimerRef.current = setTimeout(() => openStream(text, attempt + 1), RETRY_DELAY_MS)
          return
        }
        applyEvent({
          type: 'error',
          code: 'stream_closed',
          message: 'La connexion à l’agent a été interrompue.',
          detail: 'Vérifiez que le serveur est démarré, puis reposez la question.',
        })
        setIsStreaming(false)
      }
    },
    [applyEvent, closeStream, datasetId],
  )

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (trimmed === '' || datasetId === null || sourceRef.current !== null) return

      setMessages((current) => [...current, userMessage(trimmed), pendingAssistant()])
      setIsStreaming(true)
      openStream(trimmed, 0)
    },
    [datasetId, openStream],
  )

  const cancel = useCallback(() => {
    closeStream()
    setIsStreaming(false)
    setMessages((current) =>
      current.map((message) =>
        message.pending
          ? {
              ...message,
              pending: false,
              status: undefined,
              content: message.content || 'Analyse interrompue.',
            }
          : message,
      ),
    )
  }, [closeStream])

  const reset = useCallback(() => {
    closeStream()
    setIsStreaming(false)
    setMessages([])
    conversationRef.current = null
    setConversationId(null)
  }, [closeStream])

  // A stream must never outlive the component that opened it.
  useEffect(() => closeStream, [closeStream])

  return { messages, isStreaming, conversationId, send, cancel, reset }
}

/** Decode the JSON payload of an SSE message, ignoring anything malformed. */
function parseEvent(raw: Event): AgentEvent | null {
  const data = (raw as MessageEvent<string>).data
  if (typeof data !== 'string') return null
  try {
    return JSON.parse(data) as AgentEvent
  } catch {
    return null
  }
}

/** Pure reducer: message + event -> new message. Exported for testing. */
export function reduceMessage(message: LiveMessage, event: AgentEvent): LiveMessage {
  switch (event.type) {
    case 'status':
      return applyStatus(message, event)
    case 'tool_call':
      return applyToolCall(message, event)
    case 'tool_result':
      return applyToolResult(message, event)
    case 'text_delta':
      return applyTextDelta(message, event)
    case 'chart':
      return applyChart(message, event)
    case 'error':
      return applyError(message, event)
    case 'done':
      return applyDone(message, event)
  }
}

function applyStatus(message: LiveMessage, event: StatusEvent): LiveMessage {
  return { ...message, status: event.message }
}

function applyToolCall(message: LiveMessage, event: ToolCallEvent): LiveMessage {
  const invocation: ToolInvocation = {
    id: event.id,
    name: event.name,
    arguments: event.arguments,
    duration_ms: 0,
    ok: true,
    result: null,
    error: null,
  }
  return {
    ...message,
    status: `Appel de ${event.name}…`,
    toolInvocations: [...message.toolInvocations, invocation],
  }
}

function applyToolResult(message: LiveMessage, event: ToolResultEvent): LiveMessage {
  return {
    ...message,
    toolInvocations: message.toolInvocations.map((invocation) =>
      invocation.id === event.id
        ? {
            ...invocation,
            ok: event.ok,
            duration_ms: event.duration_ms,
            result: event.result ?? null,
            error: event.error ?? null,
          }
        : invocation,
    ),
  }
}

function applyTextDelta(message: LiveMessage, event: TextDeltaEvent): LiveMessage {
  return { ...message, content: message.content + event.text }
}

function applyChart(message: LiveMessage, event: ChartEvent): LiveMessage {
  return { ...message, charts: [...message.charts, event.chart] }
}

function applyError(message: LiveMessage, event: AgentErrorEvent): LiveMessage {
  return {
    ...message,
    pending: false,
    status: undefined,
    error: { code: event.code, message: event.message, detail: event.detail ?? null },
  }
}

function applyDone(message: LiveMessage, event: DoneEvent): LiveMessage {
  return {
    ...message,
    id: event.message_id || message.id,
    pending: false,
    status: undefined,
    stoppedReason: event.stopped_reason,
  }
}
