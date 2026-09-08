/**
 * `App` — composition root.
 *
 * Owns exactly three pieces of state: which datasets exist, which one is
 * selected, and its schema. The conversation itself lives in `useAgentStream`.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchSchema, listDatasets } from '@/api/client'
import { ChatPanel } from '@/components/ChatPanel'
import { DatasetPicker } from '@/components/DatasetPicker'
import { SchemaSidebar } from '@/components/SchemaSidebar'
import { useAgentStream } from '@/hooks/useAgentStream'
import type { DatasetInfo, DatasetSchema } from '@/types'

// TODO(R16): every string is hard-coded in French — extracting them behind a
// translation function is the first step towards FR/EN.
export function App(): JSX.Element {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [schema, setSchema] = useState<DatasetSchema | null>(null)
  const [loadingSchema, setLoadingSchema] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const { messages, isStreaming, send, cancel, reset } = useAgentStream(selectedId)

  useEffect(() => {
    let cancelled = false
    listDatasets()
      .then((response) => {
        if (cancelled) return
        setDatasets(response.datasets)
        setSuggestions(response.suggested_questions)
        setSelectedId((current) => current ?? response.datasets[0]?.id ?? null)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(
          cause instanceof ApiError
            ? `${cause.message} ${cause.detail ?? ''}`.trim()
            : 'Impossible de contacter le serveur.',
        )
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (selectedId === null) {
      setSchema(null)
      return
    }
    let cancelled = false
    setLoadingSchema(true)
    fetchSchema(selectedId)
      .then((response) => {
        if (!cancelled) setSchema(response)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setSchema(null)
        setError(cause instanceof ApiError ? cause.message : 'Schéma indisponible.')
      })
      .finally(() => {
        if (!cancelled) setLoadingSchema(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const selectDataset = useCallback(
    (datasetId: string) => {
      setSelectedId(datasetId)
      // A conversation is bound to one dataset server-side; switching starts a
      // new one rather than silently mixing two schemas in the same history.
      reset()
    },
    [reset],
  )

  const addDataset = useCallback(
    (dataset: DatasetInfo) => {
      setDatasets((current) => [...current, dataset])
      selectDataset(dataset.id)
    },
    [selectDataset],
  )

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">
          <h1 className="app__title">DataPilot</h1>
          <p className="app__tagline">Le modèle orchestre, les outils calculent.</p>
        </div>

        <DatasetPicker
          datasets={datasets}
          selectedId={selectedId}
          disabled={isStreaming}
          onSelect={selectDataset}
          onUploaded={addDataset}
        />
      </header>

      {error !== null ? (
        <p className="app__error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="app__body">
        <SchemaSidebar schema={schema} loading={loadingSchema} />
        <ChatPanel
          messages={messages}
          isStreaming={isStreaming}
          disabled={selectedId === null}
          suggestions={suggestions}
          onSend={send}
          onCancel={cancel}
        />
      </div>
    </div>
  )
}
