/**
 * `DatasetPicker` — select a loaded dataset, or upload a CSV.
 *
 * Upload errors are shown next to the control that caused them rather than in a
 * global banner: "ce fichier fait 40 Mo" only makes sense where the file was
 * chosen.
 */

import { useRef, useState } from 'react'

import { ApiError, uploadDataset } from '@/api/client'
import type { DatasetInfo } from '@/types'

import './DatasetPicker.css'

export interface DatasetPickerProps {
  datasets: DatasetInfo[]
  selectedId: string | null
  disabled: boolean
  onSelect: (datasetId: string) => void
  onUploaded: (dataset: DatasetInfo) => void
}

export function DatasetPicker({
  datasets,
  selectedId,
  disabled,
  onSelect,
  onUploaded,
}: DatasetPickerProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleFile = async (file: File): Promise<void> => {
    setUploading(true)
    setError(null)
    try {
      const dataset = await uploadDataset(file)
      onUploaded(dataset)
    } catch (cause) {
      const message =
        cause instanceof ApiError
          ? [cause.message, cause.detail].filter(Boolean).join(' ')
          : "L'envoi du fichier a échoué."
      setError(message)
    } finally {
      setUploading(false)
      if (inputRef.current !== null) inputRef.current.value = ''
    }
  }

  return (
    <div className="picker">
      <label className="picker__label" htmlFor="dataset-select">
        Jeu de données
      </label>
      <select
        id="dataset-select"
        className="picker__select"
        value={selectedId ?? ''}
        disabled={disabled || datasets.length === 0}
        onChange={(event) => onSelect(event.target.value)}
      >
        {datasets.length === 0 ? <option value="">Aucun</option> : null}
        {datasets.map((dataset) => (
          <option key={dataset.id} value={dataset.id}>
            {dataset.name} ({dataset.row_count.toLocaleString('fr-FR')} lignes)
          </option>
        ))}
      </select>

      <button
        type="button"
        className="picker__upload"
        disabled={disabled || uploading}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? 'Envoi…' : 'Importer un CSV'}
      </button>

      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="visually-hidden"
        aria-label="Importer un fichier CSV"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file !== undefined) void handleFile(file)
        }}
      />

      {error !== null ? (
        <p className="picker__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}
