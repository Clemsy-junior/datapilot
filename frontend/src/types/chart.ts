/**
 * Chart specification.
 *
 * Mirror of `backend/app/models/chart.py` (ChartSpec, ChartSeries, ChartType).
 * Keep both files in sync: the server builds these objects, this file is the
 * only place the browser is allowed to assume what they contain.
 */

export type ChartType = 'bar' | 'line' | 'area' | 'scatter' | 'pie'

/** One drawn series, pointing at a key of every row in `ChartSpec.data`. */
export interface ChartSeries {
  key: string
  label: string
}

/** A row of chart data: values are whatever the tool computed. */
export type ChartRow = Record<string, string | number | boolean | null>

/** Everything the front end needs to draw a chart, with no further round-trip. */
export interface ChartSpec {
  type: ChartType
  title: string
  x_key: string
  series: ChartSeries[]
  data: ChartRow[]
  x_label?: string | null
  y_label?: string | null
  note?: string | null
}
