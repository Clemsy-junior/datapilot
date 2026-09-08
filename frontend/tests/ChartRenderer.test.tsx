import { cloneElement, isValidElement, type ReactElement } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChartRenderer } from '@/components/ChartRenderer'
import type { ChartSpec, ChartType } from '@/types'

/**
 * Recharts sizes itself from the DOM, which jsdom does not lay out. Replacing
 * `ResponsiveContainer` with a fixed-size clone is the standard way to make its
 * SVG output assertable in tests.
 */
type Sizeable = { width?: number; height?: number }

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<Sizeable> }) =>
      isValidElement<Sizeable>(children)
        ? cloneElement(children, { width: 640, height: 320 })
        : children,
  }
})

function spec(type: ChartType): ChartSpec {
  return {
    type,
    title: `Chiffre d'affaires (${type})`,
    x_key: 'region',
    series: [{ key: 'value', label: "Chiffre d'affaires" }],
    data: [
      { region: 'APAC', value: 5130 },
      { region: 'EMEA', value: 220 },
    ],
    x_label: 'Région',
    y_label: 'Euros',
    note: null,
  }
}

describe('ChartRenderer', () => {
  const types: ChartType[] = ['bar', 'line', 'area', 'scatter', 'pie']

  it.each(types)('renders a %s chart from its spec', (type) => {
    const { container } = render(<ChartRenderer spec={spec(type)} />)

    const figure = screen.getByTestId('chart')
    expect(figure).toHaveAttribute('data-chart-type', type)
    expect(screen.getByText(`Chiffre d'affaires (${type})`)).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('draws one element per data point for a bar chart', () => {
    const { container } = render(<ChartRenderer spec={spec('bar')} />)

    expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(2)
  })

  it('draws one slice per data point for a pie chart', () => {
    const { container } = render(<ChartRenderer spec={spec('pie')} />)

    expect(container.querySelectorAll('.recharts-pie .recharts-sector')).toHaveLength(2)
  })

  it('renders every series of a multi-series chart', () => {
    const multi: ChartSpec = {
      type: 'line',
      title: 'CA par région et par mois',
      x_key: 'period',
      series: [
        { key: 'EMEA', label: 'EMEA' },
        { key: 'APAC', label: 'APAC' },
      ],
      data: [
        { period: '2024-01', EMEA: 120, APAC: null },
        { period: '2024-02', EMEA: 0, APAC: 5030 },
      ],
    }

    const { container } = render(<ChartRenderer spec={multi} />)

    expect(container.querySelectorAll('.recharts-line')).toHaveLength(2)
    expect(screen.getByText('EMEA')).toBeInTheDocument()
    expect(screen.getByText('APAC')).toBeInTheDocument()
  })

  it('shows the truncation note when the server sent one', () => {
    render(
      <ChartRenderer spec={{ ...spec('bar'), note: 'Graphique construit sur 1 des 2 lignes.' }} />,
    )

    expect(screen.getByText(/1 des 2 lignes/)).toBeInTheDocument()
  })

  it('does not render a note element when there is nothing to say', () => {
    const { container } = render(<ChartRenderer spec={spec('bar')} />)

    expect(container.querySelector('.chart__note')).toBeNull()
  })
})
