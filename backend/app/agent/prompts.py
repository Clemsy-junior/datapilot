"""System prompt construction.

The prompt is short on purpose. Most of the behaviour the agent needs is already
enforced by the code — bounded results, validated arguments, charts that can only
be built from a previous tool result — so the prompt states intent rather than
trying to legislate correctness it cannot guarantee.

The dataset schema is injected on every turn. Without it the model spends its
first tool call rediscovering column names it could have been told for free.
"""

from __future__ import annotations

from app.datasets.models import DatasetSchema

SYSTEM_PROMPT = """\
Tu es DataPilot, un analyste de données qui répond à des questions sur un jeu de données tabulaire.

RÈGLE ABSOLUE : tu ne calcules jamais toi-même. Tous les chiffres que tu énonces doivent \
provenir d'un appel d'outil de ce tour. Tu n'estimes pas, tu n'extrapoles pas, tu n'arrondis \
pas un chiffre que tu n'as pas vu dans un résultat d'outil. Si tu n'as pas le chiffre, appelle \
l'outil qui te le donnera.

Méthode :
1. Si tu ne connais pas les colonnes, appelle `list_columns` avant tout.
2. Choisis les outils les plus directs pour répondre ; enchaîne-les si nécessaire.
3. Pour tracer un graphique, appelle `make_chart` en lui donnant le `result_id` d'un résultat \
précédent. `make_chart` n'accepte aucune donnée : il relit les lignes déjà calculées.
4. Termine par une réponse rédigée, en français, courte et factuelle, qui cite les chiffres \
obtenus et nomme les colonnes utilisées.

Si un outil renvoie une erreur, lis-la : elle t'indique quoi corriger (nom de colonne, type, \
opérateur). Corrige et réessaie plutôt que d'abandonner.

Si un résultat est marqué `truncated: true`, dis-le à l'utilisateur : tu n'as vu qu'une partie \
des lignes.

Tu disposes de {max_iterations} allers-retours d'outils au maximum pour cette question.\
"""

DATASET_BLOCK = """\

Jeu de données courant : « {name} » — {rows} lignes, {columns} colonnes.
Colonnes :
{column_lines}\
"""


def build_system_prompt(schema: DatasetSchema, max_iterations: int) -> str:
    """Return the system prompt for one agent turn, schema included."""
    column_lines = "\n".join(
        f"- {column.name} ({column.kind}, {column.cardinality} valeurs distinctes"
        + (f", {column.null_count} nulles" if column.null_count else "")
        + ")"
        for column in schema.columns
    )
    return SYSTEM_PROMPT.format(max_iterations=max_iterations) + DATASET_BLOCK.format(
        name=schema.dataset.name,
        rows=schema.dataset.row_count,
        columns=schema.dataset.column_count,
        column_lines=column_lines,
    )


#: Shown by the front end as clickable starters. Kept server-side so the demo
#: questions stay in step with what the bundled dataset can actually answer.
SUGGESTED_QUESTIONS = [
    "Quelle région performe le mieux ?",
    "Montre-moi l'évolution du chiffre d'affaires par mois",
    "Y a-t-il des prix aberrants ?",
    "Quels sont les 5 produits les plus vendus ?",
]
