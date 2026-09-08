# Roadmap

La version 1 de DataPilot fonctionne, mais elle est volontairement naïve sur
plusieurs points. Ils sont listés ici, chacun avec un critère de « terminé »
vérifiable, pour servir de file d'attente de commits significatifs.

Chaque entrée porte un identifiant `Rxx` qu'on retrouve dans le code sous forme
de commentaire `TODO(Rxx): ...`, à l'endroit exact concerné :

```bash
grep -rn "TODO(R" backend frontend tests scripts Makefile .github
```

Ce qui **n'est pas** dans cette liste : les bugs. Le code livré n'en contient
pas de connu, et aucune fonctionnalité annoncée dans le README n'est absente.
Ce sont des axes d'amélioration, pas de la dette cachée.

**Difficulté** : 1 = moins d'une heure · 2 = une demi-journée · 3 = une journée.

---

## R01 — Persister les conversations (SQLite + SQLAlchemy)

- **Problème actuel** : `ConversationStore` est un dictionnaire en mémoire. Tout
  l'historique disparaît au redémarrage du processus, et deux workers uvicorn ne
  voient pas les mêmes conversations — `GET /api/conversations/{id}` renvoie donc
  404 une fois sur deux dès qu'on scale au-delà d'un worker.
- **À faire** : modèle SQLAlchemy `Conversation` / `Message` / `ToolInvocation`,
  moteur SQLite avec volume Docker, migration Alembic initiale, et remplacement
  du store par une implémentation qui respecte la même interface.
- **Fichiers** : `backend/app/conversations.py`, `backend/app/models/chat.py`,
  `backend/app/api/deps.py`, `docker-compose.yml` (volume), nouveau
  `backend/app/db/`.
- **Terminé quand** : un test redémarre l'application entre l'envoi d'un message
  et la relecture de la conversation, et l'historique est toujours là.
- **Difficulté** : 3
- **Notion CV** : ORM, migrations de schéma, séparation interface/implémentation
  d'une couche de persistance.

## R02 — Cache LRU des résultats d'outils

- **Problème actuel** : deux appels identiques (même outil, mêmes arguments, même
  dataset) recalculent tout. Sur un `aggregate` de 5 000 lignes c'est indolore ;
  sur un dataset dix fois plus gros, une conversation qui revient sur la même
  question paie deux fois.
- **À faire** : cache LRU borné dans `ToolRegistry.dispatch`, clé =
  `(dataset_id, tool, arguments normalisés en JSON trié)`, invalidé à l'upload
  d'un dataset. Exposer le taux de hit dans le résultat pour le rendre visible
  dans l'AgentTrace.
- **Fichiers** : `backend/app/tools/registry.py`, `backend/tests/`.
- **Terminé quand** : un test appelle deux fois le même outil et vérifie que le
  handler n'a été exécuté qu'une fois (compteur d'appels sur un outil factice).
- **Difficulté** : 1
- **Notion CV** : mémoïsation, conception de clé de cache, invalidation.

## R03 — Retry avec backoff exponentiel sur l'appel LLM

- **Problème actuel** : `AnthropicProvider.complete` fait un seul POST. Un 429 ou
  un 529 ponctuel fait échouer toute la requête utilisateur, alors qu'un
  fournisseur LLM renvoie ces codes de façon routinière.
- **À faire** : boucle de retry (3 tentatives) sur 429/500/502/503/529 et sur les
  erreurs de transport, délai exponentiel avec jitter, respect de l'en-tête
  `retry-after`, et abandon immédiat sur 400/401 (rejouer ne les corrigera pas).
- **Fichiers** : `backend/app/llm/anthropic.py`, `backend/tests/test_llm_providers.py`.
- **Terminé quand** : un `httpx.MockTransport` renvoie 429 puis 200, le test
  vérifie que `complete` réussit, qu'il y a eu deux requêtes, et qu'un 401 n'est
  jamais rejoué.
- **Difficulté** : 2
- **Notion CV** : résilience réseau, backoff exponentiel avec jitter,
  classification erreurs transitoires / définitives.

## R04 — Logs structurés JSON avec identifiant de corrélation

- **Problème actuel** : `logging.basicConfig` produit du texte. Impossible
  d'agréger, et rien ne relie les lignes d'une même requête entre elles.
- **À faire** : formateur JSON, middleware qui génère un `request_id`
  (ou reprend `X-Request-ID`), propagation via `contextvars` jusque dans la
  boucle agent et les outils, et renvoi de l'id dans l'enveloppe d'erreur pour
  qu'un utilisateur puisse le citer dans un rapport de bug.
- **Fichiers** : `backend/app/main.py`, nouveau `backend/app/logging.py`,
  `backend/app/agent/loop.py`, `backend/app/models/errors.py`.
- **Terminé quand** : chaque ligne de log est du JSON valide, et toutes celles
  d'une même requête partagent le même `request_id` (vérifié par un test qui
  capture les logs).
- **Difficulté** : 2
- **Notion CV** : observabilité, logs structurés, `contextvars`, traçabilité.

## R05 — Métriques Prometheus et endpoint `/metrics`

- **Problème actuel** : aucune métrique. On ne sait pas combien d'itérations
  consomme une question moyenne, quel outil est le plus lent, ni combien de
  requêtes s'arrêtent sur `max_iterations`.
- **À faire** : `prometheus-client`, compteurs (requêtes par endpoint et statut,
  appels d'outils par nom et par issue, arrêts par raison) et histogrammes
  (latence LLM, durée par outil, itérations par tour), exposés sur `/metrics`.
- **Fichiers** : `backend/app/api/health.py` (voisinage), nouveau
  `backend/app/metrics.py`, `backend/app/agent/loop.py`, `backend/app/tools/registry.py`.
- **Terminé quand** : `GET /metrics` renvoie du format Prometheus et un test
  vérifie qu'un tour d'agent incrémente bien le compteur d'appels d'outils.
- **Difficulté** : 2
- **Notion CV** : instrumentation applicative, métriques RED, Prometheus.

## R06 — Rate limiting

- **Problème actuel** : rien n'empêche un client d'ouvrir cent tours d'agent en
  parallèle. Chacun coûte des tokens et occupe un thread pandas.
- **À faire** : limitation par IP (fenêtre glissante) sur `/api/chat` et
  `/api/chat/stream`, plus une limite globale de tours d'agent simultanés via un
  sémaphore. Réponse 429 dans l'enveloppe d'erreur habituelle, avec `Retry-After`.
- **Fichiers** : nouveau `backend/app/middleware/ratelimit.py`,
  `backend/app/main.py`, `backend/tests/test_api.py`.
- **Terminé quand** : un test envoie N+1 requêtes et obtient un 429 avec le code
  `rate_limited` sur la dernière.
- **Difficulté** : 2
- **Notion CV** : protection d'API, fenêtre glissante, contrôle de concurrence.

## R07 — Streaming réel du texte, token par token

- **Problème actuel** : `text_delta` découpe *a posteriori* une réponse déjà
  complète. L'utilisateur attend la fin de la génération avant de voir le premier
  mot ; l'effet de streaming est cosmétique.
- **À faire** : utiliser l'endpoint SSE du fournisseur (`stream: true`), ajouter
  une méthode `stream()` à `LLMProvider`, la scripter dans `FakeLLMProvider`, et
  relayer les deltas au fil de l'eau. Le contrat d'événements ne bouge pas, donc
  le front n'a pas une ligne à changer — c'est le test que l'abstraction était
  correcte.
- **Fichiers** : `backend/app/llm/base.py`, `backend/app/llm/anthropic.py`,
  `backend/app/llm/fake.py`, `backend/app/agent/loop.py`, `backend/app/agent/events.py`.
- **Terminé quand** : le premier `text_delta` part avant la fin de la génération
  (test avec un fake qui émet des deltas espacés dans le temps), et les tests SSE
  existants passent sans modification.
- **Difficulté** : 3
- **Notion CV** : streaming HTTP de bout en bout, conception d'abstraction
  vérifiée par le fait que le changement ne fuit pas jusqu'au client.

## R08 — Virtualisation de la liste de messages

- **Problème actuel** : `ChatPanel` monte tous les messages, avec pour chacun sa
  trace dépliée et ses graphiques Recharts. Au bout de quelques dizaines de
  tours, chaque nouveau `text_delta` re-rend l'ensemble et l'interface rame.
- **À faire** : fenêtre virtuelle sur la liste (hauteurs variables), en gardant le
  défilement automatique vers le bas et l'accessibilité au clavier.
- **Fichiers** : `frontend/src/components/ChatPanel.tsx`, `frontend/tests/ChatPanel.test.tsx`.
- **Terminé quand** : avec 500 messages en props, le nombre de nœuds
  `[data-testid^="message-"]` montés reste borné, et le test de défilement passe
  toujours.
- **Difficulté** : 2
- **Notion CV** : optimisation de rendu React, virtualisation de liste,
  mesure avant/après.

## R09 — Mode sombre

- **Problème actuel** : une seule palette. Les couleurs sont déjà des variables
  CSS, mais il n'existe aucun thème alternatif ni préférence utilisateur.
- **À faire** : bloc `@media (prefers-color-scheme: dark)` redéfinissant les
  tokens, bascule manuelle persistée, adaptation de la palette des graphiques et
  vérification des contrastes (AA) dans les deux thèmes.
- **Fichiers** : `frontend/src/styles/base.css`, les CSS de composants,
  `frontend/src/components/ChartRenderer.tsx`.
- **Terminé quand** : la bascule change le thème sans rechargement, le choix
  survit à un rafraîchissement, et aucun couple texte/fond ne descend sous 4,5:1.
- **Difficulté** : 1
- **Notion CV** : design tokens, thématisation, accessibilité (contrastes WCAG).

## R10 — Export d'une conversation en Markdown

- **Problème actuel** : impossible de sortir une analyse de l'application. Or
  c'est exactement ce qu'on veut coller dans un ticket ou un compte rendu.
- **À faire** : `GET /api/conversations/{id}/export.md` produisant questions,
  réponses, appels d'outils avec leurs arguments et les données des graphiques en
  tableaux ; bouton de téléchargement côté front.
- **Fichiers** : `backend/app/api/chat.py`, nouveau `backend/app/export/markdown.py`,
  `frontend/src/components/ChatPanel.tsx`.
- **Terminé quand** : un test vérifie que le Markdown exporté contient chaque
  question, chaque nom d'outil appelé et les valeurs de chaque graphique.
- **Difficulté** : 1
- **Notion CV** : génération de documents, négociation de contenu HTTP.

## R11 — Tests end-to-end Playwright dans un vrai navigateur

- **Problème actuel** : les tests d'intégration parlent HTTP. Personne ne vérifie
  que l'`EventSource` du navigateur consomme réellement le flux, que la trace
  s'affiche, ni que les graphiques se dessinent.
- **À faire** : projet Playwright lancé contre la stack Compose, scénarios
  « poser une question suggérée », « interrompre un tour », « uploader un CSV »,
  captures d'écran en artefact CI.
- **Fichiers** : nouveau `tests/e2e/`, `docker-compose.test.yml`, `.github/workflows/ci.yml`.
- **Terminé quand** : un scénario clique une question suggérée et attend que le
  `<svg>` du graphique apparaisse, en CI, sans flakiness sur trois exécutions.
- **Difficulté** : 3
- **Notion CV** : tests end-to-end navigateur, stabilisation de tests asynchrones.

## R12 — Réduire la taille des images Docker

- **Problème actuel** : l'image backend pèse environ 250 Mo, dominée par pandas
  et NumPy. Aucune mesure n'a été faite, aucun objectif n'est fixé.
- **À faire** : mesurer l'existant (`docker image ls`, `docker history`), puis
  tester : `--no-compile-bytecode`, suppression des tests embarqués dans les
  wheels, base `python:3.12-alpine` (attention aux wheels manylinux), image
  distroless pour le runtime. **Noter le avant/après dans ce fichier.**
- **Fichiers** : `backend/Dockerfile`, `frontend/Dockerfile`.
- **Terminé quand** : la taille de l'image backend a baissé d'au moins 25 %, la
  mesure avant/après est écrite ici, et les tests e2e passent toujours.
- **Difficulté** : 2
- **Notion CV** : optimisation d'images conteneur, builds multi-étapes,
  démarche de mesure.

## R13 — Hooks pre-commit

- **Problème actuel** : rien n'empêche de committer du code non formaté ; on ne
  s'en aperçoit qu'en CI, deux minutes plus tard.
- **À faire** : `.pre-commit-config.yaml` avec ruff (check + format), eslint sur
  les fichiers modifiés, et les hooks d'hygiène habituels (fin de fichier,
  espaces en fin de ligne, YAML valide, gros fichiers). Documenter
  `pre-commit install` dans le README et le `postCreateCommand`.
- **Fichiers** : nouveau `.pre-commit-config.yaml`, `README.md`,
  `.devcontainer/devcontainer.json`.
- **Terminé quand** : un commit contenant un fichier mal formaté est refusé
  localement.
- **Difficulté** : 1
- **Notion CV** : automatisation de la qualité, déplacement du feedback vers la
  gauche du cycle.

## R14 — Dependabot

- **Problème actuel** : les dépendances ne bougent que si on y pense.
- **À faire** : `.github/dependabot.yml` pour pip, npm, docker et
  github-actions, avec regroupement des mises à jour mineures pour ne pas noyer
  la liste de PR.
- **Fichiers** : nouveau `.github/dependabot.yml`.
- **Terminé quand** : Dependabot ouvre au moins une PR et la CI la valide sans
  intervention.
- **Difficulté** : 1
- **Notion CV** : gestion du cycle de vie des dépendances, sécurité de la chaîne
  d'approvisionnement logicielle.

## R15 — Authentification par clé API et durcissement CORS

- **Problème actuel** : l'API est ouverte. Le CORS de développement autorise
  toutes les en-têtes, et l'endpoint d'upload accepte n'importe quel fichier de
  n'importe qui.
- **À faire** : en-tête `X-API-Key` comparé en temps constant, dépendance FastAPI
  appliquée à `/api/chat*` et `/api/datasets` (POST), `/api/health` laissé
  public pour le healthcheck, liste d'en-têtes CORS explicite.
- **Fichiers** : nouveau `backend/app/security.py`, `backend/app/main.py`,
  `backend/app/api/`, `.env.example`, `docker-compose.yml`.
- **Terminé quand** : sans clé, `/api/chat` renvoie 401 dans l'enveloppe
  d'erreur ; avec la bonne clé, 200 ; `/api/health` répond dans les deux cas.
- **Difficulté** : 2
- **Notion CV** : authentification d'API, comparaison en temps constant,
  politique CORS.

## R16 — Internationalisation FR/EN

- **Problème actuel** : toutes les chaînes sont écrites en français en dur, dans
  les composants React comme dans les messages d'erreur des outils Python — que
  le modèle lit aussi.
- **À faire** : extraction des chaînes derrière une fonction de traduction,
  catalogues fr/en, détection depuis `navigator.language`, sélecteur manuel, et
  côté backend une langue de réponse transmise dans le prompt système.
- **Fichiers** : nouveau `frontend/src/i18n/`, tous les composants,
  `backend/app/agent/prompts.py`, `backend/app/tools/`.
- **Terminé quand** : basculer la langue change l'interface *et* la langue de
  rédaction de l'agent, sans rechargement.
- **Difficulté** : 3
- **Notion CV** : internationalisation, externalisation des chaînes.

## R17 — Plusieurs datasets simultanés et jointures

- **Problème actuel** : une conversation est liée à un seul dataset. Impossible
  de croiser deux fichiers, ce qui est pourtant la question la plus fréquente
  dès qu'on a deux exports.
- **À faire** : contexte d'outils multi-datasets, argument `dataset_id` sur les
  outils, nouvel outil `join` (clé, type de jointure, contrôle de cardinalité et
  refus explicite des explosions de lignes), et sélection multiple côté front.
- **Fichiers** : `backend/app/tools/registry.py`, `backend/app/tools/`,
  `backend/app/agent/prompts.py`, `frontend/src/components/DatasetPicker.tsx`.
- **Terminé quand** : une question portant sur deux datasets produit un résultat
  joint correct, et une jointure qui multiplierait les lignes au-delà d'un seuil
  est refusée avec un message exploitable par le modèle.
- **Difficulté** : 3
- **Notion CV** : modélisation de données, jointures, conception d'API d'outils.

## R18 — Découpage du bundle frontend

- **Problème actuel** : `vite build` produit un chunk unique d'environ 590 ko
  (167 ko gzippés), dominé par Recharts, chargé même par un utilisateur qui
  n'affichera jamais de graphique.
- **À faire** : `React.lazy` sur `ChartRenderer`, `manualChunks` séparant les
  dépendances lourdes, mesure avant/après notée ici, et budget de taille vérifié
  en CI.
- **Fichiers** : `frontend/vite.config.ts`, `frontend/src/components/ChatPanel.tsx`,
  `.github/workflows/ci.yml`.
- **Terminé quand** : le chunk d'entrée passe sous 200 ko non gzippés et la CI
  échoue si le budget est dépassé.
- **Difficulté** : 2
- **Notion CV** : optimisation de bundle, code splitting, budgets de performance.
