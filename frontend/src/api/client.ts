/**
 * Thin HTTP client.
 *
 * One place decides how a URL is built and how an error envelope is unwrapped,
 * so components never deal with `fetch` directly and every failure reaches the
 * UI as the same `ApiError`.
 */

import type {
  Conversation,
  DatasetInfo,
  DatasetListResponse,
  DatasetSchema,
} from '@/types'

/** Base of the API. Empty means "same origin", which is the nginx case. */
export const API_BASE = (import.meta.env['VITE_API_BASE'] ?? '') as string

/** An error the API reported in its structured envelope. */
export class ApiError extends Error {
  readonly code: string
  readonly detail: string | null
  readonly status: number

  constructor(status: number, code: string, message: string, detail: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; detail?: string | null }
}

export function apiUrl(path: string, params?: Record<string, string | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined) query.set(key, value)
  }
  const suffix = query.toString()
  return `${API_BASE}/api${path}${suffix ? `?${suffix}` : ''}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api${path}`, init)
  } catch (cause) {
    throw new ApiError(0, 'network_error', "Le serveur est injoignable.", String(cause))
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorEnvelope
    throw new ApiError(
      response.status,
      body.error?.code ?? `http_${response.status}`,
      body.error?.message ?? "La requête a échoué.",
      body.error?.detail ?? null,
    )
  }
  return (await response.json()) as T
}

export function listDatasets(): Promise<DatasetListResponse> {
  return request<DatasetListResponse>('/datasets')
}

export function fetchSchema(datasetId: string): Promise<DatasetSchema> {
  return request<DatasetSchema>(`/datasets/${encodeURIComponent(datasetId)}/schema`)
}

export function fetchConversation(conversationId: string): Promise<Conversation> {
  return request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`)
}

export function uploadDataset(file: File): Promise<DatasetInfo> {
  const body = new FormData()
  body.append('file', file)
  return request<DatasetInfo>('/datasets', { method: 'POST', body })
}

/** URL of the SSE endpoint; `EventSource` can only issue a GET. */
export function chatStreamUrl(
  message: string,
  datasetId: string,
  conversationId?: string,
): string {
  return apiUrl('/chat/stream', {
    message,
    dataset_id: datasetId,
    conversation_id: conversationId,
  })
}
