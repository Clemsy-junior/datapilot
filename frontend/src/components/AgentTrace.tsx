/**
 * `AgentTrace` — the timeline of what the agent actually did.
 *
 * This is the component the whole project exists to make possible. An agent
 * that answers "APAC leads with 5 130 €" is indistinguishable from one that
 * made the number up; an agent that shows `aggregate({group_by:["region"],
 * metric:"revenue"}) → 2 rows in 3.1 ms` is not. Everything here is therefore
 * optimised for scrutiny: the exact arguments, the real duration, the raw
 * result one click away, and failures shown rather than hidden.
 */

import { useState } from 'react'

import { formatArguments, formatDuration, summariseResult } from '@/lib/traceSummary'
import type { ToolInvocation } from '@/types'

import './AgentTrace.css'

export interface AgentTraceProps {
  invocations: ToolInvocation[]
  /** Shown while the agent is still working, e.g. "Appel de aggregate…". */
  status?: string | undefined
}

export function AgentTrace({ invocations, status }: AgentTraceProps): JSX.Element | null {
  const [open, setOpen] = useState(true)

  if (invocations.length === 0 && status === undefined) return null

  const failures = invocations.filter((invocation) => !invocation.ok).length
  const total = invocations.reduce((sum, invocation) => sum + invocation.duration_ms, 0)

  return (
    <section className="trace" aria-label="Détail des appels d'outils">
      <button
        type="button"
        className="trace__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="trace__chevron" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span className="trace__summary">
          {invocations.length} appel{invocations.length > 1 ? 's' : ''} d’outil
          {invocations.length > 1 ? 's' : ''}
          {total > 0 ? ` · ${formatDuration(total)}` : ''}
          {failures > 0 ? ` · ${failures} en erreur` : ''}
        </span>
      </button>

      {open ? (
        <ol className="trace__list">
          {invocations.map((invocation) => (
            <TraceStep key={invocation.id} invocation={invocation} />
          ))}
        </ol>
      ) : null}

      {status !== undefined ? (
        <p className="trace__status" role="status">
          <span className="trace__spinner" aria-hidden="true" />
          {status}
        </p>
      ) : null}
    </section>
  )
}

function TraceStep({ invocation }: { invocation: ToolInvocation }): JSX.Element {
  const running = invocation.result === null && invocation.error === null
  const state = running ? 'running' : invocation.ok ? 'ok' : 'error'

  return (
    <li className="trace__step" data-state={state} data-testid="trace-step">
      <div className="trace__head">
        <code className="trace__name">{invocation.name}</code>
        <span className={`trace__pill trace__pill--${state}`}>
          {state === 'running' ? 'en cours' : state === 'ok' ? 'ok' : 'erreur'}
        </span>
        {!running ? (
          <span className="trace__duration">{formatDuration(invocation.duration_ms)}</span>
        ) : null}
      </div>

      <p className="trace__args">{formatArguments(invocation.arguments)}</p>

      {invocation.error !== null && invocation.error !== undefined ? (
        <p className="trace__error">{invocation.error}</p>
      ) : null}

      {invocation.result !== null && invocation.result !== undefined ? (
        <>
          <p className="trace__result">{summariseResult(invocation.name, invocation.result)}</p>
          <details className="trace__raw">
            <summary>Résultat brut</summary>
            <pre>{JSON.stringify(invocation.result, null, 2)}</pre>
          </details>
        </>
      ) : null}
    </li>
  )
}
