import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { AgentTrace } from '@/components/AgentTrace'
import { formatArguments, formatDuration, summariseResult } from '@/lib/traceSummary'
import type { ToolInvocation } from '@/types'

const aggregate: ToolInvocation = {
  id: 'call_1',
  name: 'aggregate',
  arguments: { group_by: ['region'], agg: 'sum', metric: 'revenue' },
  duration_ms: 3.14,
  ok: true,
  result: {
    result_id: 'res_1',
    rows: [
      { region: 'APAC', value: 5130 },
      { region: 'EMEA', value: 220 },
    ],
    total_rows: 2,
    truncated: false,
  },
  error: null,
}

const failed: ToolInvocation = {
  id: 'call_2',
  name: 'describe_column',
  arguments: { column: 'regionn' },
  duration_ms: 0,
  ok: false,
  result: null,
  error: 'La colonne « regionn » n’existe pas dans ce dataset.',
}

const running: ToolInvocation = {
  id: 'call_3',
  name: 'make_chart',
  arguments: {},
  duration_ms: 0,
  ok: true,
  result: null,
  error: null,
}

describe('AgentTrace', () => {
  it('renders nothing when there is nothing to show', () => {
    const { container } = render(<AgentTrace invocations={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('lists each tool call with its arguments, duration and summary', () => {
    render(<AgentTrace invocations={[aggregate]} />)

    expect(screen.getByText('aggregate')).toBeInTheDocument()
    expect(screen.getByText(/group_by=\["region"\]/)).toBeInTheDocument()
    expect(screen.getByText('3 ms')).toBeInTheDocument()
    expect(screen.getByText('2 ligne(s)')).toBeInTheDocument()
  })

  it('marks a failed call and shows the message the model received', () => {
    render(<AgentTrace invocations={[failed]} />)

    const step = screen.getByTestId('trace-step')
    expect(step).toHaveAttribute('data-state', 'error')
    expect(screen.getByText(/regionn/, { selector: '.trace__error' })).toBeInTheDocument()
    expect(screen.getByText('erreur')).toBeInTheDocument()
  })

  it('marks a call still in flight', () => {
    render(<AgentTrace invocations={[running]} />)

    expect(screen.getByTestId('trace-step')).toHaveAttribute('data-state', 'running')
    expect(screen.getByText('en cours')).toBeInTheDocument()
  })

  it('summarises the run in the header, failures included', () => {
    render(<AgentTrace invocations={[aggregate, failed]} />)

    expect(screen.getByRole('button', { name: /2 appels d’outils/ })).toHaveTextContent(
      '1 en erreur',
    )
  })

  it('can be collapsed and expanded', async () => {
    const user = userEvent.setup()
    render(<AgentTrace invocations={[aggregate]} />)

    const toggle = screen.getByRole('button')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('trace-step')).toBeNull()
  })

  it('exposes the raw result for inspection', () => {
    render(<AgentTrace invocations={[aggregate]} />)

    expect(screen.getByText('Résultat brut')).toBeInTheDocument()
    expect(screen.getByText(/"result_id": "res_1"/)).toBeInTheDocument()
  })

  it('shows the live status while the agent works', () => {
    render(<AgentTrace invocations={[]} status="Appel de aggregate…" />)

    expect(screen.getByRole('status')).toHaveTextContent('Appel de aggregate…')
  })
})

describe('formatArguments', () => {
  it('renders an empty object explicitly', () => {
    expect(formatArguments({})).toBe('aucun argument')
  })

  it('keeps values JSON-encoded so strings stay quoted', () => {
    expect(formatArguments({ column: 'revenue', n: 5 })).toBe('column="revenue", n=5')
  })
})

describe('summariseResult', () => {
  it('summarises row-shaped results, flagging truncation', () => {
    expect(
      summariseResult('aggregate', { rows: [{ a: 1 }], truncated: true, total_rows: 812 }),
    ).toBe('1 ligne(s) (tronqué sur 812)')
  })

  it('summarises an outlier detection', () => {
    expect(summariseResult('detect_outliers', { outlier_count: 3, column: 'unit_price' })).toBe(
      '3 valeur(s) aberrante(s) sur « unit_price »',
    )
  })

  it('summarises a correlation', () => {
    expect(summariseResult('correlation', { pearson_r: 0.42, n: 5000 })).toBe(
      'r = 0.42 sur 5000 lignes',
    )
  })

  it('summarises a filter and surfaces the reusable selection id', () => {
    expect(
      summariseResult('filter_rows', {
        matched_rows: 228,
        total_rows: 5000,
        selection_id: 'sel_abc',
      }),
    ).toBe('228 / 5000 lignes retenues → sel_abc')
  })

  it('summarises a chart', () => {
    expect(summariseResult('make_chart', { summary: { type: 'bar', points: 4 } })).toBe(
      'graphique bar · 4 points',
    )
  })

  it('falls back to the tool name for anything unrecognised', () => {
    expect(summariseResult('mystery', { whatever: true })).toBe('mystery exécuté')
  })
})

describe('formatDuration', () => {
  it.each([
    [0.4, '<1 ms'],
    [312, '312 ms'],
    [1200, '1.20 s'],
  ])('renders %s ms as %s', (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected)
  })
})
