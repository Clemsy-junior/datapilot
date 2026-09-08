# Architecture

Ce document explique **pourquoi** DataPilot est construit ainsi. Le *quoi* est
dans le code ; ce qui suit, ce sont les décisions et ce qu'elles coûtent.

---

## 1. Le principe : le modèle orchestre, il ne calcule jamais

Tout part de là. Un LLM produit du texte plausible ; il ne produit pas des
chiffres justes. Si on lui donne un extrait de CSV et qu'on lui demande le
chiffre d'affaires par région, il rendra une réponse bien formée, souvent
proche, parfois fausse — et rien dans la réponse ne dit laquelle.

DataPilot retire donc au modèle la capacité de calculer. Il choisit quels outils
Python appeler, avec quels arguments, dans quel ordre, et rédige la synthèse à
partir de ce que les outils ont renvoyé. Les outils, eux, sont du pandas
déterministe : mêmes entrées, mêmes sorties, testables sans LLM.

Ce n'est pas une consigne dans le prompt — une consigne se contourne. C'est une
propriété structurelle : le modèle n'a jamais les données brutes en main, il n'a
que des résultats d'outils bornés, et `make_chart` (§4) ne lui laisse même pas
la possibilité d'introduire un chiffre inventé dans un graphique.

## 2. Pourquoi une boucle d'outils plutôt que du texte-vers-SQL

Le réflexe courant est de faire générer du SQL (ou du pandas) par le modèle et
de l'exécuter. C'est séduisant : une seule « fonction », une expressivité
maximale. C'est aussi, pour ce projet, le mauvais compromis.

**Surface d'attaque.** Exécuter du code généré, c'est exécuter du code arbitraire.
Il faut alors un bac à sable, une liste blanche de fonctions, une limite de
temps, une limite de mémoire — et chacune de ces défenses doit être correcte.
Ici, la surface d'attaque est un ensemble fini de neuf fonctions dont les
arguments sont validés par Pydantic. On ne se défend pas contre du code : on
n'en accepte pas.

**Débogabilité.** Quand une réponse est fausse, on veut savoir pourquoi. Une
requête SQL de trente lignes générée par un modèle est difficile à auditer.
`aggregate(group_by=["region"], agg="sum", metric="revenue")` se lit d'un coup
d'œil — et c'est exactement ce que l'`AgentTrace` affiche à l'utilisateur.

**Messages d'erreur exploitables.** Une erreur SQL (`column "regio" does not
exist`) laisse le modèle deviner. Ici, une colonne inconnue renvoie le nom
proposé le plus proche et la liste des colonnes disponibles : le modèle corrige
au tour suivant, et le test `test_unknown_column_is_a_tool_error_not_a_crash`
verrouille ce comportement.

**Le prix à payer.** Un outil ne fait que ce pour quoi il a été écrit. Une
question du type « quel est le panier moyen des clients ayant commandé plus de
trois fois » ne se répond pas avec neuf outils fixes. C'est assumé : la limite
est visible et documentée (R17), au lieu d'être masquée par une expressivité
qu'on ne saurait pas sécuriser.

## 3. Pourquoi borner les résultats d'outils

Chaque outil renvoie au maximum `DATAPILOT_MAX_TOOL_ROWS` lignes (50 par
défaut), et signale la troncature avec `truncated: true` et le total réel.

Trois raisons, dans cet ordre d'importance :

1. **La fenêtre de contexte est une ressource partagée.** Un résultat d'outil
   repart dans le prompt au tour suivant, et y reste jusqu'à la fin du tour. Un
   `group by` sur une colonne à forte cardinalité produirait des milliers de
   lignes, qu'on paierait à chaque itération suivante.
2. **Le coût est proportionnel.** Les tokens d'entrée se paient. Un résultat non
   borné, multiplié par huit itérations, transforme une question en facture.
3. **Un modèle noyé raisonne moins bien.** Cinquante lignes ordonnées par
   pertinence donnent de meilleures réponses que trois mille lignes brutes.

La troncature est *déclarée*, jamais silencieuse : le modèle sait qu'il n'a vu
qu'une partie, et le prompt système lui demande de le dire à l'utilisateur.
Un résultat tronqué qui se ferait passer pour complet serait pire que pas de
résultat du tout.

## 4. Pourquoi `make_chart` ne prend aucune donnée

C'est la décision dont je suis le plus content, et elle tient en une ligne :
`make_chart` accepte un `result_id`, pas des points de données.

Si l'outil acceptait un tableau de valeurs, le modèle pourrait le remplir avec
des chiffres recopiés — donc potentiellement altérés — et le graphique
mentirait, avec l'autorité visuelle d'un graphique. En lui imposant de désigner
un résultat déjà calculé dans le même tour, on rend cette classe d'erreur
*impossible*, pas seulement improbable. Le modèle garde le choix de quoi
tracer et comment ; il n'a aucune prise sur les valeurs.

Effet secondaire utile : les données ne traversent le réseau qu'une fois. Le
`ChartSpec` complet part vers le navigateur via l'événement SSE `chart`, tandis
que le modèle ne reçoit qu'un résumé (type, titre, nombre de points).

## 5. Pourquoi un fournisseur LLM factice, et pourquoi il est le défaut

`FakeLLMProvider` a deux usages et c'est délibéré.

**En test, il est scripté.** On lui donne la séquence exacte de réponses et on
vérifie ce que la boucle en fait. C'est la seule façon de tester une boucle
agentique : contre un vrai modèle, un test n'affirme rien de reproductible. Les
garde-fous — limite d'itérations, timeout global, erreur d'outil renvoyée au
modèle — sont tous testés ainsi.

**En démo, il improvise.** Sans script, il lit le schéma réel du dataset chargé,
choisit un plan d'analyse à partir de la question, appelle de vrais outils et
rédige à partir de leurs vrais résultats. Les chiffres affichés sont donc exacts
même hors ligne ; seule la rédaction est scriptée, et l'interface le dit.

Conséquence pratique : **toute la suite de tests et toute la CI tournent sans la
moindre clé API**. Aucun secret dans les jobs `backend`, `frontend` et `e2e`.
Un contributeur clone, lance `make test`, et tout passe.

## 6. Pourquoi nginx proxifie `/api`

En production, le navigateur ne parle qu'à nginx. `/` sert le bundle statique,
`/api` est relayé vers `http://backend:8000`.

- **Une seule origine, donc pas de CORS.** Le préflight, les en-têtes autorisés,
  les cookies inter-origines : autant de problèmes qui n'existent tout
  simplement pas. En développement, le proxy de Vite reproduit exactement le
  même montage, pour que les deux environnements se comportent pareil.
- **Le backend n'est pas exposé.** Il n'a pas de `ports:` dans le
  `docker-compose.yml`, seulement un `expose:`. La seule porte d'entrée est le
  proxy — c'est là que viendraient TLS, rate limiting et logs d'accès.
- **`backend` est un nom d'hôte.** Compose crée un réseau et un enregistrement
  DNS interne par service. Comprendre ça, c'est comprendre pourquoi
  `http://backend:8000` fonctionne dans le conteneur et pas depuis la machine
  hôte.

Un piège réel s'est révélé ici : nginx bufferise les réponses par défaut. Sans
`proxy_buffering off` sur `/api/chat/stream`, la trace n'apparaît qu'une fois le
tour terminé — le streaming ne sert alors plus à rien. Le backend envoie aussi
`X-Accel-Buffering: no`, ceinture et bretelles, et le test d'intégration vérifie
l'absence de `Content-Length` sur la réponse SSE.

## 7. Pourquoi deux dispositifs de conteneurisation

Ils répondent à deux questions différentes.

`.devcontainer/` répond à « comment un développeur travaille ». Le code source y
est **monté** : une modification sur l'hôte est immédiatement visible dans le
conteneur, uvicorn recharge, Vite fait du HMR. L'image contient Python *et*
Node, git, make, ruff.

`docker-compose.yml` répond à « comment ça tourne en production ». Le code y est
**copié** dans des images multi-étapes : les compilateurs et `npm` restent dans
l'étape de build, le runtime backend n'a que le virtualenv et l'application, le
runtime frontend n'est que nginx et des fichiers statiques. L'artefact est
immuable et le conteneur tourne en utilisateur non-root.

Monter le code en production supprimerait la reproductibilité ; copier le code
en développement supprimerait le rechargement à chaud. Ce sont deux objectifs
opposés, donc deux fichiers.

## 8. Choix plus petits, mais assumés

**`GET` pour le flux SSE.** `EventSource`, dans le navigateur, ne sait faire que
des GET sans corps. La question passe donc dans l'URL, ce qui la fait apparaître
dans les logs d'accès et limite sa longueur (4 000 caractères ici). L'alternative
— `fetch` + `ReadableStream` en POST — offre plus de liberté mais oblige à
réimplémenter le parsing SSE et la reconnexion. Pour ce projet, la simplicité de
`EventSource` gagne ; si la confidentialité des questions devenait un sujet, ce
serait le premier choix à revoir.

**`POST /api/chat` en plus du flux.** Le même générateur d'événements sert les
deux endpoints. La version non streamée existe pour que les tests d'intégration
tiennent en cinq lignes, sans parser un flux. Il n'y a pas deux implémentations
qui pourraient diverger : il y en a une, et le navigateur exerce la même.

**Pas de SDK fournisseur.** `httpx` et le format HTTP brut. Le format de
tool-use *est* ce que ce projet enseigne, et une dépendance de moins est une
mise à jour de moins à suivre. Le coût : quelques dizaines de lignes de
sérialisation dans `llm/anthropic.py`.

**Reconnexion SSE volontairement bridée côté client.** `EventSource` se
reconnecte tout seul, ce qui serait catastrophique ici : une reconnexion
**rejoue tout le tour d'agent**, donc paie une seconde fois et duplique la
réponse. `useAgentStream` ne réessaie qu'une fois, et seulement si le flux est
mort avant d'avoir livré le moindre événement.

**Le typage est dupliqué, à la main.** Les types TypeScript de
`frontend/src/types/` reflètent les modèles Pydantic, avec un commentaire
pointant le fichier Python correspondant. Générer les types depuis l'OpenAPI
serait plus sûr, mais ajouterait une étape de build et un générateur à
maintenir. À cette taille, la duplication commentée est le meilleur compromis ;
au-delà, elle ne le serait plus.

## 9. Ce qui casserait à plus grande échelle

Honnêtement, dans l'ordre où les choses lâcheraient.

**Le stockage en mémoire, d'abord.** `ConversationStore` et `DatasetStore` sont
des dictionnaires. Dès deux workers uvicorn, une requête sur deux ne trouve pas
la conversation. C'est le premier mur, et c'est R01.

**Les DataFrames en mémoire ensuite.** Chaque dataset est intégralement chargé,
et chaque réplique en garde sa copie. À 5 000 lignes c'est gratuit ; à 50
millions, il faut un moteur qui reste sur disque (DuckDB) ou un entrepôt
distant, et les outils deviennent des générateurs de requêtes plutôt que des
appels pandas. L'interface des outils, elle, ne changerait pas — c'est bien
l'intérêt de l'avoir isolée.

**Le coût des tokens.** Sans cache (R02), sans limite de débit (R06) et sans
métriques (R05), rien ne détecte une boucle coûteuse avant la facture.

**L'absence d'observabilité.** Sans logs corrélés (R04), diagnostiquer « une
réponse fausse hier vers 15 h » est une enquête, pas une requête.

**La sécurité.** Aucune authentification (R15), aucune limite de débit, et un
endpoint d'upload qui accepte 25 Mo de n'importe qui. Acceptable pour un projet
de démonstration exécuté localement ; à corriger avant toute exposition.
