/**
 * `ChartRenderer` — draws a `ChartSpec` with Recharts.
 *
 * The component is a pure function of the spec: it never fetches, never
 * computes an aggregate, never derives a value the server did not send. If a
 * number appears on screen, a Python tool produced it.
 *
 * Entry animations are switched off on every series. They add nothing to a
 * static analytical chart, they replay each time a streamed answer re-renders
 * the message, and they honour no motion preference of their own.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ChartSpec } from '@/types'

import './ChartRenderer.css'

/** Categorical palette: distinguishable in both light and colour-blind vision. */
const PALETTE = ['#1d4ed8', '#0f7a4d', '#a4610a', '#7c3aed', '#b3261e', '#0e7490']

const AXIS_STYLE = { fontSize: 12, fill: '#5b6472' } as const
const GRID_COLOR = '#e6e9ee'

function colorAt(index: number): string {
  return PALETTE[index % PALETTE.length] ?? PALETTE[0] ?? '#1d4ed8'
}

export interface ChartRendererProps {
  spec: ChartSpec
  height?: number
}

export function ChartRenderer({ spec, height = 280 }: ChartRendererProps): JSX.Element {
  return (
    <figure className="chart" data-testid="chart" data-chart-type={spec.type}>
      <figcaption className="chart__title">{spec.title}</figcaption>
      <div className="chart__canvas" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          {renderChart(spec)}
        </ResponsiveContainer>
      </div>
      {spec.note !== null && spec.note !== undefined && spec.note !== '' ? (
        <p className="chart__note">{spec.note}</p>
      ) : null}
    </figure>
  )
}

function renderChart(spec: ChartSpec): JSX.Element {
  switch (spec.type) {
    case 'bar':
      return (
        <BarChart data={spec.data} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
          {commonAxes(spec)}
          {spec.series.map((series, index) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={colorAt(index)}
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      )

    case 'line':
      return (
        <LineChart data={spec.data} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
          {commonAxes(spec)}
          {spec.series.map((series, index) => (
            <Line
              key={series.key}
              type="monotone"
              dataKey={series.key}
              name={series.label}
              stroke={colorAt(index)}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      )

    case 'area':
      return (
        <AreaChart data={spec.data} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
          {commonAxes(spec)}
          {spec.series.map((series, index) => (
            <Area
              key={series.key}
              type="monotone"
              dataKey={series.key}
              name={series.label}
              stroke={colorAt(index)}
              fill={colorAt(index)}
              fillOpacity={0.18}
              strokeWidth={2}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      )

    case 'scatter':
      return (
        <ScatterChart data={spec.data} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
          {commonAxes(spec)}
          {spec.series.map((series, index) => (
            <Scatter
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={colorAt(index)}
              isAnimationActive={false}
            />
          ))}
        </ScatterChart>
      )

    case 'pie': {
      const [first] = spec.series
      return (
        <PieChart margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
          <Tooltip />
          <Legend />
          <Pie
            data={spec.data}
            dataKey={first?.key ?? 'value'}
            nameKey={spec.x_key}
            outerRadius="72%"
            label
            isAnimationActive={false}
          >
            {spec.data.map((row, index) => (
              <Cell key={`${String(row[spec.x_key])}-${index}`} fill={colorAt(index)} />
            ))}
          </Pie>
        </PieChart>
      )
    }
  }
}

/** Axes, grid, tooltip and legend shared by every cartesian chart type. */
function commonAxes(spec: ChartSpec): JSX.Element {
  return (
    <>
      <CartesianGrid stroke={GRID_COLOR} strokeDasharray="3 3" vertical={false} />
      <XAxis
        dataKey={spec.x_key}
        tick={AXIS_STYLE}
        tickLine={false}
        axisLine={{ stroke: GRID_COLOR }}
        label={axisLabel(spec.x_label, 'bottom')}
        interval="preserveStartEnd"
        minTickGap={12}
      />
      <YAxis
        tick={AXIS_STYLE}
        tickLine={false}
        axisLine={false}
        width={64}
        label={axisLabel(spec.y_label, 'left')}
      />
      <Tooltip />
      <Legend />
    </>
  )
}

interface AxisLabel {
  value: string
  position: string
  offset: number
  angle?: number
  style: Record<string, string | number>
}

/**
 * Axis labels, positioned so they cannot collide with the tick values.
 *
 * The vertical label is rotated and centred on the axis; the horizontal one
 * sits in the margin reserved for it below the ticks.
 */
function axisLabel(value: string | null | undefined, position: 'bottom' | 'left'): AxisLabel | undefined {
  if (value === null || value === undefined || value === '') return undefined
  if (position === 'left') {
    return {
      value,
      position: 'insideLeft',
      angle: -90,
      offset: 6,
      style: { ...AXIS_STYLE, textAnchor: 'middle' },
    }
  }
  return { value, position: 'insideBottom', offset: -8, style: { ...AXIS_STYLE, textAnchor: 'middle' } }
}
