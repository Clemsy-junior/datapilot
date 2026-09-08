/**
 * Formatting helpers for the agent trace.
 *
 * They live outside `AgentTrace.tsx` so that file exports a component and
 * nothing else — which keeps React Fast Refresh working during development,
 * and makes these two pure functions testable on their own.
 */

import type { JsonObject } from '@/types'

/** `{"group_by": ["region"], "agg": "sum"}` -> `group_by=["region"], agg="sum"`. */
export function formatArguments(args: JsonObject): string {
  const entries = Object.entries(args)
  if (entries.length === 0) return 'aucun argument'
  return entries.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(', ')
}

/** One readable line per tool result, so the trace is skimmable. */
export function summariseResult(name: string, result: JsonObject): string {
  if (typeof result['outlier_count'] === 'number') {
    return `${result['outlier_count']} valeur(s) aberrante(s) sur « ${String(result['column'])} »`
  }
  if (typeof result['pearson_r'] === 'number') {
    return `r = ${result['pearson_r']} sur ${String(result['n'])} lignes`
  }
  if (typeof result['matched_rows'] === 'number') {
    return `${result['matched_rows']} / ${String(result['total_rows'])} lignes retenues → ${String(
      result['selection_id'],
    )}`
  }
  if (Array.isArray(result['columns'])) {
    return `${result['columns'].length} colonnes décrites`
  }
  if (Array.isArray(result['rows'])) {
    const truncated =
      result['truncated'] === true ? ` (tronqué sur ${String(result['total_rows'])})` : ''
    return `${result['rows'].length} ligne(s)${truncated}`
  }
  if (typeof result['summary'] === 'object' && result['summary'] !== null) {
    const summary = result['summary'] as JsonObject
    return `graphique ${String(summary['type'])} · ${String(summary['points'])} points`
  }
  if (typeof result['statistics'] === 'object' && result['statistics'] !== null) {
    return `statistiques de « ${String(result['column'])} »`
  }
  return `${name} exécuté`
}

/** Human-readable duration: `<1 ms`, `312 ms`, `1.20 s`. */
export function formatDuration(ms: number): string {
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}
