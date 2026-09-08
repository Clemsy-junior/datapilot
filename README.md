# DataPilot

**Un agent IA qui répond en langage naturel à des questions sur un jeu de
données tabulaire — sans jamais calculer lui-même : tous les chiffres affichés
proviennent d'outils Python déterministes.**

<!-- Remplacez VOTRE-COMPTE par votre identifiant GitHub une fois le dépôt poussé. -->
[![CI](https://github.com/VOTRE-COMPTE/datapilot/actions/workflows/ci.yml/badge.svg)](https://github.com/VOTRE-COMPTE/datapilot/actions/workflows/ci.yml)

<!-- Enregistrez une capture animée de l'interface (question → trace d'outils →
     graphique) et placez-la dans docs/demo.gif pour qu'elle s'affiche ici. -->
![demo](docs/demo.gif)

---

## Le principe

> Le LLM orchestre, il ne calcule jamais.

Le modèle choisit quels outils appeler, dans quel ordre, et rédige l'explication
finale. Les calculs sont faits par des fonctions pandas déterministes, dont les
résultats sont bornés avant d'être renvoyés au modèle. L'interface affiche la
liste des appels d'outils, leurs arguments, leur durée et leur résultat brut :
n'importe quel chiffre de la réponse peut être remonté jusqu'au calcul qui l'a
produit.

Un graphique ne peut même pas mentir : `make_chart` n'accepte aucune donnée en
argument, seulement l'identifiant d'un résultat d'outil déjà calculé.

## Démarrage

Trois commandes, sans clé API — le fournisseur LLM par défaut est un fournisseur
factice qui appelle de vrais outils sur de vraies données :

```bash
git clone https://github.com/VOTRE-COMPTE/datapilot.git && cd datapilot
make install      # virtualenv Python + dépendances npm
make dev          # API sur :8000, interface sur :5173
```

Ou, tout en conteneurs, avec nginx devant :

```bash
make up           # interface sur http://localhost:8080
```

Pour brancher un vrai modèle :

```bash
cp .env.example .env
# renseigner DATAPILOT_LLM_PROVIDER=anthropic et DATAPILOT_ANTHROPIC_API_KEY
```

## Ce que ça sait faire

Neuf outils sont exposés à l'agent :

| Outil | Rôle |
|---|---|
| `list_columns` | nom, type, valeurs nulles et cardinalité de chaque colonne |
| `describe_column` | statistiques descriptives, adaptées au type de la colonne |
| `aggregate` | regroupement sur une ou deux colonnes + agrégation |
| `top_n` | N premières ou dernières valeurs, en lignes ou en groupes |
| `filter_rows` | conditions → résumé + identifiant de sélection réutilisable |
| `correlation` | corrélation de Pearson entre deux colonnes numériques |
| `detect_outliers` | valeurs aberrantes (IQR par défaut, ou z-score) |
| `time_series` | agrégation par jour, semaine ou mois, éventuellement par groupe |
| `make_chart` | spécification de graphique rendue par Recharts côté navigateur |

Le jeu de données livré (`backend/data/sales.csv`, 5 000 lignes de ventes
e-commerce synthétiques) contient volontairement une saisonnalité de fin
d'année, une région dominante, des prix aberrants et 2 % de délais de livraison
manquants — de quoi avoir quelque chose à trouver. Il est régénérable à
l'identique avec `make data`. On peut aussi importer son propre CSV.

## La stack, et pourquoi

| | Choix | Pourquoi |
|---|---|---|
| API | **FastAPI + Pydantic v2** | Les schémas JSON envoyés au modèle sont *dérivés* des modèles Pydantic des arguments d'outils : impossible que la description vue par le LLM diverge du code exécuté. |
| Calcul | **pandas** | Le standard de l'analyse tabulaire en Python. Synchrone et pur, donc testable ligne à ligne. |
| Client LLM | **httpx**, sans SDK | Le format tool-use brut est ce que ce projet donne à voir ; et c'est une dépendance de moins à suivre. |
| Front | **React 18 + TypeScript strict + Vite** | Les types des événements SSE et de `ChartSpec` reflètent les modèles Pydantic, ce qui rend l'ajout d'un événement côté serveur détectable à la compilation. |
| Graphiques | **Recharts** | Le serveur envoie une spécification, pas une image : le graphique reste interactif, redimensionnable et inspectable. |
| Proxy | **nginx** | Une seule origine en production, donc pas de CORS ; et le backend n'est jamais exposé directement. |
| Qualité | **ruff, ESLint, pytest, Vitest** | Un seul `ruff.toml` à la racine pour tout le Python, y compris `scripts/`. |

Aucune bibliothèque hors de cette liste n'a été ajoutée, à deux exceptions
près, toutes deux nécessaires : `pydantic-settings` (lecture des variables
d'environnement, séparé de Pydantic depuis la v2) et `python-multipart` (requis
par FastAPI pour lire un upload de fichier).

## Architecture

```
                    ┌──────────────────────────────────────────┐
   navigateur ─────►│ nginx :80                                │
                    │   /        → bundle React statique       │
                    │   /api     → proxy vers backend:8000     │
                    └───────────────┬──────────────────────────┘
                                    │  (réseau Docker interne)
                    ┌───────────────▼──────────────────────────┐
                    │ FastAPI :8000                            │
                    │                                          │
                    │   /api/chat/stream ──► AgentRunner       │
                    │                          │               │
                    │        ┌─────────────────▼────────────┐  │
                    │        │  boucle agentique            │  │
                    │        │                              │  │
                    │        │  prompt + schémas d'outils   │  │
                    │        │            │                 │  │
                    │        │            ▼                 │  │
                    │        │      fournisseur LLM ────────┼──┼──► API Anthropic
                    │        │            │                 │  │    (ou fake, hors ligne)
                    │        │   demande d'outil ?          │  │
                    │        │       │           │          │  │
                    │        │      oui         non         │  │
                    │        │       ▼           ▼          │  │
                    │        │   registre    texte final    │  │
                    │        │   d'outils        │          │  │
                    │        │       │           │          │  │
                    │        │  ┌────▼─────┐     │          │  │
                    │        │  │  pandas  │     │          │  │
                    │        │  │ (borné)  │     │          │  │
                    │        │  └────┬─────┘     │          │  │
                    │        │       └───────────┘          │  │
                    │        └──────────────┬───────────────┘  │
                    │                       │                  │
                    └───────────────────────┼──────────────────┘
                                            │
        événements SSE : status, tool_call, tool_result,
                         text_delta, chart, error, done
                                            │
                                            ▼
                                      navigateur
```

Les décisions techniques et leurs contreparties sont détaillées dans
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Sécurité du bac à sable d'outils

Le modèle ne peut pas exécuter de code. Il émet un nom d'outil et un objet JSON,
et cinq garde-fous s'appliquent avant, pendant et après :

1. **Surface finie.** Neuf outils, pas d'évaluation de code, pas de SQL généré.
   Un nom d'outil inconnu renvoie une erreur listant ceux qui existent.
2. **Arguments validés.** Chaque outil a un modèle Pydantic ; les types, les
   bornes (`n` entre 1 et 100) et les énumérations sont vérifiés avant que la
   moindre ligne de pandas ne s'exécute.
3. **Échecs traités comme des valeurs.** Une colonne inexistante, un type
   incompatible, une division par zéro, une sélection vide : chacun produit un
   `ToolError` avec un message rédigé pour être compris et corrigé *par le
   modèle*, jamais une exception non gérée ni un 500.
4. **Résultats bornés.** Au plus 50 lignes par résultat, troncature signalée
   avec le total réel. Ni la fenêtre de contexte ni la facture ne peuvent
   exploser.
5. **Isolation du processus.** Le conteneur backend tourne en utilisateur
   non-root, n'est pas exposé sur l'hôte, et ne lit que le répertoire de données.

Ce que ça ne couvre pas, et c'est écrit : il n'y a **ni authentification, ni
limitation de débit** (voir R06 et R15 dans la roadmap). Le projet est prévu
pour tourner localement.

## Tests

```bash
make test           # backend (pytest, couverture ≥ 75 %) + frontend (Vitest)
make lint           # ruff check + ruff format --check + ESLint + tsc --noEmit
make e2e            # stack Docker complète + tests HTTP réels à travers nginx
```

Les niveaux de test, et ce que chacun démontre :

- **Unitaires** — chaque outil isolément, sur un jeu de six lignes dont tous les
  agrégats se calculent de tête, cas d'erreur inclus.
- **Boucle agent** — avec un `FakeLLMProvider` scripté : séquence d'appels
  attendue, limite d'itérations respectée, timeout global, et vérification qu'une
  erreur d'outil repart bien vers le modèle au lieu de faire échouer la requête.
- **API** — `TestClient` sur tous les endpoints, dont un test qui consomme
  réellement le flux SSE et vérifie l'ordre et le type des événements.
- **Frontend** — `ChartRenderer` pour les cinq types de graphiques, `AgentTrace`
  avec des données factices, et `useAgentStream` avec un `EventSource` simulé
  (y compris la reconnexion et l'annulation).
- **Intégration** — depuis un conteneur, contre `http://frontend/api/...`, donc à
  travers nginx : c'est ce qui prouve que la chaîne navigateur → nginx → backend
  fonctionne, et que le flux SSE n'est pas bufferisé au passage.

Aucun de ces niveaux ne nécessite de clé API.

## Développement

Le dépôt fournit un **dev container** VS Code : « Reopen in Container » suffit,
rien à installer sur la machine hôte. Python 3.12 et Node 20 sont dans la même
image, le code source est monté (pas copié), et les ports 8000 et 5173 sont
redirigés. La différence entre ce dispositif et le `docker-compose.yml` de
production est expliquée dans [ARCHITECTURE.md](ARCHITECTURE.md#7--pourquoi-deux-dispositifs-de-conteneurisation).

`make help` liste toutes les cibles disponibles.

## Suite

Cette version 1 est volontairement naïve sur une quinzaine de points précis —
persistance, cache, retry, observabilité, sécurité, performance. Chacun est
décrit dans **[ROADMAP.md](ROADMAP.md)** avec un critère de « terminé »
vérifiable, et repéré dans le code par un commentaire `TODO(Rxx)` :

```bash
grep -rn "TODO(R" backend frontend tests scripts Makefile .github
```

## Licence

MIT.
