/**
 * Server-Sent Events emitted by the agent loop.
 *
 * Mirror of `backend/app/agent/events.py`. Keep both files in sync — the
 * discriminated union below is what lets `useAgentStream` handle every event
 * exhaustively, so adding an event server-side without adding it here is a
 * compile error rather than a silent no-op.
 */

import type { ChartSpec } from './chart'

/** Anything JSON-shaped that a tool accepted as an argument or produced. */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

export type JsonObject = Record<string, JsonValue>

export type AgentStage = 'started' | 'thinking' | 'tool' | 'answering' | 'stopped'

export type StoppedReason = 'completed' | 'max_iterations' | 'timeout' | 'error'

export interface StatusEvent {
  type: 'status'
  stage: AgentStage
  message: string
  iteration: number
}

export interface ToolCallEvent {
  type: 'tool_call'
  id: string
  name: string
  arguments: JsonObject
  iteration: number
}

export interface ToolResultEvent {
  type: 'tool_result'
  id: string
  name: string
  ok: boolean
  duration_ms: number
  result?: JsonObject | null
  error?: string | null
}

export interface TextDeltaEvent {
  type: 'text_delta'
  text: string
}

export interface ChartEvent {
  type: 'chart'
  chart: ChartSpec
}

export interface ErrorEvent {
  type: 'error'
  code: string
  message: string
  detail?: string | null
}

export interface DoneEvent {
  type: 'done'
  conversation_id: string
  message_id: string
  stopped_reason: StoppedReason
  iterations: number
}

export type AgentEvent =
  | StatusEvent
  | ToolCallEvent
  | ToolResultEvent
  | TextDeltaEvent
  | ChartEvent
  | ErrorEvent
  | DoneEvent

/** The event names the server emits, used to subscribe on the EventSource. */
export const AGENT_EVENT_NAMES = [
  'status',
  'tool_call',
  'tool_result',
  'text_delta',
  'chart',
  'error',
  'done',
] as const satisfies readonly AgentEvent['type'][]
