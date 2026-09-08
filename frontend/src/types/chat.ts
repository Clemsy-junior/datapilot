/**
 * Conversation models.
 *
 * Mirror of `backend/app/models/chat.py`. Keep both files in sync.
 */

import type { ChartSpec } from './chart'
import type { JsonObject, StoppedReason } from './events'

/** One tool call, as displayed by AgentTrace. */
export interface ToolInvocation {
  id: string
  name: string
  arguments: JsonObject
  duration_ms: number
  ok: boolean
  result?: JsonObject | null
  error?: string | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  tool_invocations: ToolInvocation[]
  charts: ChartSpec[]
}

export interface Conversation {
  id: string
  dataset_id: string
  messages: ChatMessage[]
  created_at: string
}

/**
 * A message as the UI holds it while the agent is still working.
 *
 * `pending` messages are built incrementally from the SSE stream, so they carry
 * the same shape as a stored `ChatMessage` plus the live status line.
 */
export interface LiveMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolInvocations: ToolInvocation[]
  charts: ChartSpec[]
  status?: string
  stoppedReason?: StoppedReason
  pending: boolean
  error?: { code: string; message: string; detail?: string | null }
}
