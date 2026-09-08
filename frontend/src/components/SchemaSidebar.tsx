/**
 * `SchemaSidebar` — the columns of the loaded dataset.
 *
 * Present for a practical reason as much as a decorative one: the questions a
 * user can usefully ask depend on which columns exist and what kind they are,
 * and the agent is told exactly the same thing in its system prompt.
 */

import type { ColumnInfo, DatasetSchema } from '@/types'

import './SchemaSidebar.css'

const KIND_LABEL: Record<ColumnInfo['kind'], string> = {
  numeric: 'num',
  categorical: 'cat',
  datetime: 'date',
  boolean: 'bool',
}

export interface SchemaSidebarProps {
  schema: DatasetSchema | null
  loading: boolean
}

export function SchemaSidebar({ schema, loading }: SchemaSidebarProps): JSX.Element {
  return (
    <aside className="schema panel" aria-label="Schéma du jeu de données">
      <h2 className="schema__title">Colonnes</h2>

      {loading ? <p className="schema__hint">Chargement du schéma…</p> : null}

      {!loading && schema === null ? (
        <p className="schema__hint">Aucun jeu de données sélectionné.</p>
      ) : null}

      {schema !== null ? (
        <>
          <p className="schema__meta">
            {schema.dataset.row_count.toLocaleString('fr-FR')} lignes ·{' '}
            {schema.dataset.column_count} colonnes
          </p>
          <ul className="schema__list">
            {schema.columns.map((column) => (
              <li key={column.name} className="schema__item">
                <div className="schema__row">
                  <span className="schema__name" title={column.dtype}>
                    {column.name}
                  </span>
                  <span className={`schema__kind schema__kind--${column.kind}`}>
                    {KIND_LABEL[column.kind]}
                  </span>
                </div>
                <div className="schema__details">
                  {column.cardinality.toLocaleString('fr-FR')} valeurs distinctes
                  {column.null_count > 0
                    ? ` · ${column.null_count.toLocaleString('fr-FR')} nulles`
                    : ''}
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </aside>
  )
}
