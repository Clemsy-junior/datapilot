/**
 * Dataset description.
 *
 * Mirror of `backend/app/datasets/models.py` (ColumnInfo, DatasetInfo,
 * DatasetSchema). Keep both files in sync.
 */

export type ColumnKind = 'numeric' | 'categorical' | 'datetime' | 'boolean'

/** A JSON value as it comes back from pandas, once nulls are normalised. */
export type CellValue = string | number | boolean | null

/** One column, as described to both the agent and the sidebar. */
export interface ColumnInfo {
  name: string
  kind: ColumnKind
  dtype: string
  null_count: number
  cardinality: number
  sample_values: CellValue[]
}

/** Identity and size of a loaded dataset. */
export interface DatasetInfo {
  id: string
  name: string
  row_count: number
  column_count: number
  size_bytes: number
  source: 'bundled' | 'upload'
}

/** Columns plus a short preview, returned by `GET /api/datasets/{id}/schema`. */
export interface DatasetSchema {
  dataset: DatasetInfo
  columns: ColumnInfo[]
  preview: Record<string, CellValue>[]
}

/** Payload of `GET /api/datasets`. */
export interface DatasetListResponse {
  datasets: DatasetInfo[]
  suggested_questions: string[]
}
