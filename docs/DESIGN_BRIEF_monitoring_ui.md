# Design brief — Interface de monitoring **CC-monitor**

> **Destinataire :** claude design (production de la maquette).
> **Auteur :** worker writer, gouvernance Groupe Treuil.
> **Nature du document :** cadrage de **besoin** (le *quoi* et le *pourquoi*). Tous les
> choix visuels, UX et de mise en page sont **délibérément laissés** à claude design
> (voir §7).
> **Autosuffisance :** claude design ne lit pas le dépôt. Tout le contexte nécessaire
> est dans ce document.

---

## 1. Objectif & contexte produit

**CC-monitor** est un outil interne de **monitoring en pseudo temps réel** de
l'activité des **sous-agents (subagents) d'oh-my-claudecode (OMC)** pendant **une**
session Claude Code.

Quand une orchestration multi-agents tourne, un **agent principal** délègue à des
**leads** (rôle management), qui décomposent le travail en **workers**. Les workers
sont exécutés par l'agent principal **par lots de 5** (au plus cinq workers en
parallèle par lot ; le reste est mis en file pour le lot suivant). Pendant que cette
mécanique tourne, l'utilisateur veut **voir en direct** :

- **qui travaille** (topologie et statut des agents) ;
- **ce que chaque agent « pense »** (son raisonnement, tel qu'affiché dans Claude Code) ;
- **quels outils il appelle** (Bash, Read, Edit, dispatch d'agents…) et avec quel résultat ;
- **les rapports finaux structurés** que chaque agent écrit en fin de tâche.

**Pourquoi cet outil.** Aujourd'hui cette activité est noyée dans un flux texte
linéaire. L'utilisateur perd le fil de « qui fait quoi maintenant », ne voit pas
les agents parallèles d'un coup d'œil, et doit ouvrir des fichiers à la main pour
lire les rapports et repérer les échecs. CC-monitor donne une **vue d'observation
dédiée**, en direct, qui rend lisibles la topologie, le raisonnement, l'activité
outils et les verdicts de fin.

**Posture du produit.** C'est un outil d'**observation** (lecture seule). Il
n'agit pas sur la session, ne la pilote pas, ne modifie aucun agent. Il **affiche**.

---

## 2. Utilisateur & usage

- **Utilisateur unique :** Yoann (développeur). Pas de multi-utilisateur, pas de
  rôles, pas d'authentification à concevoir côté UI.
- **Mono-session :** on observe **une seule session à la fois**, mais cette session
  est **sélectionnable parmi toutes les sessions disponibles sur la machine**, **tous
  projets confondus**. « Mono-session » désigne donc le *focus* (une session observée
  à un instant donné), **pas** un figeage sur un projet ou une session prédéfinie.
  L'outil n'est pas câblé sur un projet : il **énumère** les sessions de n'importe
  quel projet présent sur la machine et l'utilisateur en **choisit une** à observer
  (voir §3.0 et §4 F0). Pas de tableau de bord agrégeant **plusieurs sessions
  simultanément**.
- **Plateforme :** **desktop d'abord**. Usage sur grand écran, en parallèle du
  travail. Le mobile n'est pas une cible.
- **Mode d'usage :** **live**. L'utilisateur garde la vue ouverte pendant que
  l'orchestration tourne et la consulte par coups d'œil. La lecture en
  post-mortem (relire après coup) est secondaire mais ne doit pas être bloquée.
- **Tempo :** **pseudo temps réel**, latence **~0,5 à 3 s**. La granularité de
  rafraîchissement est le **message / bloc**, **pas le token**. Voir §6 pour
  l'implication directe sur la maquette.

---

## 3. Sources & contrats de données (section essentielle)

L'UI consomme **quatre sources réelles**. Leur forme **dicte ce qui est
affichable** : la maquette ne doit montrer que des informations dérivables de ces
contrats. Les échantillons ci-dessous sont **réels et anonymisés**.

> **Important :** ne pas inventer de champs. Si une information n'apparaît dans
> aucune source ci-dessous, elle n'est pas affichable et ne doit pas figurer dans
> la maquette.

### 3.0 — Résolution session ↔ projet

Avant de lire la moindre donnée live, l'outil doit **retrouver quel projet
correspond à une session donnée**. Les sources ne vivent pas dans un emplacement
global : elles sont **réparties par projet**, et le rattachement passe par un
**slug** réversible.

- **Transcripts de session :** `~/.claude/projects/<slug>/<sessionId>.jsonl`. Le
  `<slug>` est le **chemin absolu du projet encodé** : chaque `/` du chemin devient
  un `-`. Exemple : le projet `/home/yoann/treuil-service-projet` donne le slug
  `-home-yoann-treuil-service-projet`. Le mapping est **réversible** : on retrouve
  le dossier projet à partir du slug (slug ⇄ dossier projet), et inversement.
- **Rapports et état d'orchestration :** `.omc/reports/*.md` et `.omc/state/`
  (dont `subagent-tracking.json`, `agent-replay-<id>.jsonl`, et un sous-dossier
  `sessions/<sessionId>/` **par session**) vivent **dans le dossier du projet
  concerné**, **pas** dans un emplacement global partagé.
- **Conséquence directe :** partir d'une session choisie → **résoudre le projet**
  via le slug → lire les `reports/` et l'`état` **DE CE projet** + le transcript
  **DE CETTE session**. Plusieurs projets et plusieurs sessions **coexistent** sur
  la machine ; l'outil doit pouvoir **tous les énumérer** pour proposer le choix
  (voir §4 F0), tout en n'**observant qu'une session à la fois** (voir §2).

### 3.1 — Cycle de vie des agents (`subagent-tracking.json`)

Un objet JSON unique, liste d'agents. Donne le **statut** et la **durée** de chaque
agent. Source d'autorité pour la topologie et les compteurs.

```json
{
  "agents": [
    {
      "agent_id": "af725408feebd8e1a",
      "agent_type": "bootstrap-lead",
      "started_at": "2026-06-17T13:54:14.282Z",
      "parent_mode": "none",
      "status": "completed",
      "completed_at": "2026-06-17T13:58:18.078Z",
      "duration_ms": 243796
    },
    {
      "agent_id": "ad190d6937242f9fb",
      "agent_type": "W1-backend",
      "started_at": "2026-06-17T13:59:03.344Z",
      "parent_mode": "none",
      "status": "running"
    }
  ]
}
```

- `status` ∈ `running` | `completed` | (prévoir aussi `failed` / `blocked`).
- `duration_ms` : durée en **millisecondes** (présente quand l'agent est terminé).
- `agent_type` **encode le rôle** : ex. `bootstrap-lead`, `W1-backend`,
  `W2-frontend`, `W3-docker`, `verifier`, `security-review`. Le préfixe `W<n>-`
  désigne un worker et son numéro de slot dans le lot.
- `completed_at` absent tant que l'agent tourne.

### 3.2 — Flux d'événements (`agent-replay-<id>.jsonl`)

Fichier **append-only**, **un événement JSON par ligne**. Donne la **chronologie**
des démarrages/arrêts d'agents.

```json
{"t":0,"agent":"af72540","agent_type":"bootstrap-lead","event":"agent_start","parent_mode":"none"}
{"t":0,"agent":"af72540","agent_type":"bootstrap-lead","event":"agent_stop","success":true,"duration_ms":179879}
{"t":0,"agent":"addb3e6","agent_type":"unknown","event":"agent_stop","success":true}
```

- `event` ∈ `{ agent_start, agent_stop / agent_complete, … }`.
- `t` : offset temporel de l'événement.
- `success` : booléen présent sur les arrêts (distingue terminé OK d'échec).
- `agent_type` peut valoir `unknown` quand le type n'a pas été résolu — à gérer
  comme un agent au rôle indéterminé, pas comme une erreur.
- L'`agent` ici est une forme **courte** de l'identifiant (préfixe), à rapprocher
  de l'`agent_id` complet de 3.1 par correspondance de préfixe.

### 3.3 — Raisonnement live + activité outils (transcript de session)

Fichier **JSONL append-only**, écrit **incrémentalement, message par message** par
Claude Code. **Source du contenu live** (raisonnement, réponses, appels d'outils,
résultats d'outils).

Chaque ligne est un événement typé : `assistant`, `user`, `system`, `mode`,
`permission-mode`, `ai-title`, `last-prompt`, `file-history-snapshot`,
`queue-operation`, `attachment`. **Pour l'UI**, l'essentiel se trouve dans deux
types :

**Événement `assistant`** → `message.content` est une **liste de blocs typés** :

```json
{ "type": "thinking",  "thinking": "<le raisonnement affiché dans Claude Code>" }
{ "type": "text",      "text": "<réponse visible>" }
{ "type": "tool_use",  "name": "Bash", "input": { "command": "pytest -q" } }
```

`name` du `tool_use` ∈ `Bash`, `Read`, `Edit`, `Write`, `Agent`, … (le nom de
l'outil appelé).

**Événement `user`** → contient les **résultats d'outils** :

```json
{ "type": "tool_result", "tool_use_id": "…", "content": "<sortie de l'outil>" }
```

Un `tool_result` se rattache à son `tool_use` via `tool_use_id`.

**Ordre de grandeur réel observé** sur une session : `thinking` ≈ 32 blocs,
`text` ≈ 44, `tool_use` ≈ 78. **L'activité outils domine** : la maquette doit
traiter le flux d'outils comme le contenu le plus volumineux.

**Limites à prendre en compte dans la maquette :**

- Un bloc `thinking` **apparaît quand il est flushé** (fin de bloc), **pas lettre
  par lettre**. Le panneau de raisonnement se remplit **par bloc entier**.
- Certains blocs peuvent être `redacted_thinking` (raisonnement **chiffré**) →
  à présenter comme **« raisonnement masqué »**, sans contenu lisible.
- Les blocs peuvent être **très longs** (raisonnement dense, grosses sorties
  d'outils) → enjeu de lecture et de performance (§5, §6).

### 3.4 — Rapports finaux structurés (`reports/*.md`)

Fichier **markdown** écrit par **chaque agent en fin de tâche** (**post-hoc**, pas
live). Nommage : `<branch-or-task>__<role>.md` (ex. `auth-sessions__api.md`,
`auth-sessions__security-review.md`).

**Forme typique** : un titre, un bloc de métadonnées (branche, worker/rôle, statut
commit), des sections détaillées (fichiers créés/modifiés, notes de sécurité…), une
**section de preuves de gates**, et un **verdict** en fin de document.

La section de preuves de gates se présente sous deux variantes observées — la
maquette doit savoir rendre les deux :

1. **Table de gates** (forme cible attendue) :

```
| Gate     | Command                          | Result          | Evidence   |
| -------- | -------------------------------- | --------------- | ---------- |
| Pylint   | pylint app/ alembic/...          | 10.00/10        | PASS       |
| Pytest   | pytest                           | 79 passed       | PASS       |
| Semgrep  | semgrep ...                      | 0 findings      | PASS       |
```

2. **Liste de preuves** (forme également rencontrée dans les rapports réels) :

```
- pylint app/ … → Your code has been rated at 10.00/10
- pytest tests/… → 14 passed in 0.78s
- ruff check app/ → All checks passed!
```

Dans les deux cas, l'UI doit faire ressortir **PASS / FAIL par gate** et le
**verdict une ligne** de fin. Le corps est du **markdown standard** (titres,
listes, blocs de code) à rendre lisiblement.

---

## 4. Besoins fonctionnels (F0–F6)

Chaque besoin est décrit par : **objectif utilisateur**, **données utilisées**, et
**ce que l'utilisateur doit pouvoir voir / faire**. La **mise en forme n'est pas
imposée** (voir §7).

### F0 — Découverte & sélection de session / projet

- **Objectif :** choisir **QUELLE session observer**, **sans configuration figée** —
  parmi toutes les sessions disponibles sur la machine, tous projets confondus.
- **Données :** §3.0 (résolution session ↔ projet). Énumération des transcripts
  `~/.claude/projects/*/*.jsonl` et des sous-dossiers `<projet>/.omc/state/sessions/`.
  Le slug du dossier `projects/<slug>/` résout le **projet** (chemin / nom) de
  chaque session ; l'event `ai-title` du transcript (§3.3) peut fournir un **titre
  de session** quand il est présent.
- **Voir / faire :**
  - **découvrir** les sessions disponibles et, pour chacune, **le projet associé** ;
  - **choisir** une session à observer, OU laisser l'outil **auto-détecter** la
    session **active la plus récente** ;
  - pour chaque session listée : **projet** (chemin / nom), **`sessionId`**, **état**
    (*active* / *récente* / *terminée*), **horodatage de dernière activité**, et
    **éventuellement le titre de session** (event `ai-title`) ;
  - une fois la session choisie, **tous les panneaux F1–F6 se cadrent sur CE projet
    + CETTE session** ; changer de session **rebascule** l'ensemble des panneaux.

### F1 — Topologie / arbre des agents

- **Objectif :** comprendre d'un coup d'œil *qui travaille et comment c'est
  organisé*.
- **Données :** §3.1 (`subagent-tracking.json`) pour statut et durée ; §3.2
  (replay) pour les démarrages/arrêts.
- **Voir / faire :**
  - la **hiérarchie** agent principal → leads → workers ;
  - le **statut live** par agent : *en cours* / *terminé* / *échec* ;
  - la **durée** (en cours = temps écoulé ; terminé = `duration_ms`) ;
  - la **notion de lots de 5** (regroupement des workers exécutés ensemble) ;
  - **sélectionner un agent** (action transverse, voir fin de §4).

### F2 — Timeline chronologique

- **Objectif :** voir *l'enchaînement dans le temps* et les **agents parallèles**
  simultanément.
- **Données :** §3.2 (replay) pour les événements ; `started_at` / `completed_at`
  de §3.1 pour le positionnement temporel.
- **Voir / faire :**
  - les **démarrages et arrêts** d'agents placés sur un axe temporel ;
  - les **événements clés** de la session ;
  - plusieurs agents **actifs en même temps** lisibles simultanément (le
    parallélisme par lots de 5 doit être visible).

### F3 — Panneau de raisonnement live

- **Objectif :** suivre *ce que pense l'agent sélectionné* au fil de l'eau.
- **Données :** §3.3, blocs `thinking` et `text` des événements `assistant`.
- **Voir / faire :**
  - le **flux des blocs** `thinking` / `text` de l'agent sélectionné, **ajouté
    par bloc** (pas token par token) ;
  - distinguer **raisonnement** (`thinking`) et **réponse visible** (`text`) ;
  - gérer le **raisonnement masqué** (`redacted_thinking`) : indiquer sa présence
    sans contenu ;
  - gérer les **blocs très longs** (lecture confortable, repli/déroulé éventuel).

### F4 — Activité outils

- **Objectif :** savoir *ce que l'agent fait concrètement* (ses appels d'outils).
- **Données :** §3.3, blocs `tool_use` (appel) et `tool_result` (résultat),
  appariés par `tool_use_id`.
- **Voir / faire :**
  - la **liste des appels d'outils** de l'agent sélectionné : **nom de l'outil**
    (`Bash`, `Read`, `Edit`, `Agent`…) ;
  - une **entrée résumée** (ex. la commande pour `Bash`, le chemin pour `Read`) ;
  - le **résultat** associé (sortie de l'outil), avec gestion des sorties
    volumineuses ;
  - rappel : c'est le **contenu le plus volumineux** de la session (≈ 78 appels
    observés) → prévoir la densité et la performance.

### F5 — Lecture des rapports

- **Objectif :** lire *le verdict final* d'un agent rapidement.
- **Données :** §3.4 (`reports/*.md`).
- **Voir / faire :**
  - le **rendu markdown** du rapport de l'agent sélectionné ;
  - une **lecture rapide des gates** (PASS / FAIL par gate) ;
  - le **verdict une ligne** mis en évidence ;
  - gérer le cas **rapport pas encore écrit** (l'agent n'a pas fini → pas de
    rapport) vs **rapport disponible**.

### F6 — Vue d'ensemble session

- **Objectif :** garder *le pouls global* de la session observée.
- **Données :** §3.1 (compteurs d'agents, statuts), §3.4 (synthèse des gates),
  horodatages pour le temps écoulé.
- **Voir / faire :**
  - la **session observée** (identité de la session en cours) ;
  - le **nombre d'agents actifs** (et terminés / en échec) ;
  - le **temps écoulé** de session ;
  - une **synthèse des gates** (combien PASS / FAIL à travers les rapports).

### Besoins transverses

- **Sélection d'un agent** : sélectionner un agent (depuis F1 ou F2) **filtre** les
  panneaux F3, F4, F5 sur cet agent. C'est le geste central de navigation.
- **Distinction d'état** : *en cours* vs *terminé* vs *échec* doivent être
  **nettement** distinguables partout où un agent apparaît.
- **Remontée des échecs** : les **échecs / blocages** (status `failed`/`blocked`,
  `success:false`, gate `FAIL`) doivent **remonter visiblement** — l'utilisateur
  ne doit pas avoir à les chercher.

---

## 5. États à concevoir

La maquette doit couvrir **tous** les états suivants (le rendu de chacun est libre,
mais aucun ne doit être oublié) :

| État | Description |
| --- | --- |
| Aucune session sélectionnée | Aucune session choisie pour l'instant. Invite à découvrir / sélectionner (F0). |
| Liste de sessions à choisir | Énumération des sessions disponibles (multi-projets) proposée au choix (F0). |
| Bascule de session en cours | Changement de la session observée → recadrage des panneaux F1–F6 sur le nouveau projet / session. |
| Session inactive | Aucune session observée. État d'accueil / vide. |
| Connexion en cours | L'UI tente de se connecter au flux. |
| Live / streaming | Réception active des événements (état nominal). |
| Agent en cours | Un agent sélectionné est `running`. |
| Agent terminé | Un agent sélectionné est `completed`. |
| Agent en échec / bloqué | `failed` / `blocked` / `success:false`. |
| Rapport disponible | Le `reports/*.md` de l'agent existe et est rendu. |
| Rapport pas encore écrit | L'agent n'a pas fini → pas de rapport. |
| Connexion perdue / reconnexion | Flux SSE interrompu, tentative de reconnexion. |
| Raisonnement masqué | `redacted_thinking` → contenu non lisible signalé. |
| Contenu très long | Flux / blocs volumineux → enjeu de performance (virtualisation). |

---

## 6. Contraintes non-fonctionnelles

À **énoncer** dans la maquette comme cadre, pas à résoudre visuellement :

- **Langue de l'UI : français.** Tous les libellés visibles sont en français
  (ex. « En cours », « Terminé », « Échec », « Raisonnement masqué »,
  « Rapport non disponible »). Les **noms de champs techniques / identifiants**
  (`agent_id`, `agent_type`, `tool_use`, `duration_ms`, `PASS`/`FAIL`…) **restent
  en anglais** : ce sont les vrais noms des données.
- **Pseudo temps réel, pas token par token.** Le rafraîchissement se fait **par
  message / bloc** (latence ~0,5–3 s). **La maquette ne doit pas évoquer un effet
  « machine à écrire » au niveau du token** : le raisonnement et le texte
  apparaissent **par blocs entiers**, pas caractère par caractère.
- **Mono-session.** Une seule session à la fois. Pas de multi-session.
- **Stack imposée (contrainte, pas à concevoir) :** frontend **React 19 + Vite +
  Tailwind** ; backend **FastAPI** exposant un **flux SSE** (live) + endpoints
  REST. Cette stack est **déjà décidée** ; la maquette s'y conforme mais ne la
  redéfinit pas.
- **Desktop d'abord.** Conçu pour grand écran.
- **Performance sur longs flux.** Les flux (outils, raisonnement) peuvent être
  longs → prévoir la **virtualisation** / le chargement progressif.
- **Accessibilité de base.** Contraste lisible, états ne reposant pas uniquement
  sur la couleur, navigation clavier raisonnable.

---

## 7. Périmètre laissé à claude design

Ce brief décrit **QUOI** montrer et **POURQUOI**, **jamais à quoi ça ressemble**.
Les décisions suivantes sont **entièrement** du ressort de claude design :

- **identité visuelle** et ton ;
- **palette / couleurs** ;
- **typographie** ;
- **composition / layout** (disposition des panneaux, grille, colonnes…) ;
- **hiérarchie visuelle** de l'information ;
- **style des composants** (cartes, listes, badges, panneaux…) ;
- **iconographie** ;
- **motion / transitions** ;
- **pattern de navigation** entre vues et agents ;
- **densité** d'information.

> **Pistes d'inspiration — optionnel, NON imposé.** Si elles aident, des
> références d'observabilité live (consoles de traces, vues d'orchestration) ou de
> visualisation d'arbres/timelines peuvent inspirer. **Aucune de ces pistes n'est
> contraignante** ; claude design tranche librement.

---

## 8. Livrable attendu de claude design

Une **maquette desktop** qui :

- couvre **F0** : une **zone / écran de découverte-sélection de session** (liste des
  sessions disponibles, multi-projets, avec projet associé, état et dernière
  activité ; choix manuel ou auto-détection de la session active la plus récente) ;
- couvre les **six vues F1–F6** (topologie, timeline, raisonnement live, activité
  outils, lecture des rapports, vue d'ensemble) ;
- couvre **tous les états de §5** ;
- reflète un usage de **monitoring live mono-session** : sélection d'une session à
  observer parmi toutes les sessions de la machine, puis sélection d'un agent qui
  filtre les panneaux, distinction nette en cours / terminé / échec, remontée
  visible des échecs ;
- respecte les **contraintes non-fonctionnelles de §6** (UI française, pseudo
  temps réel par bloc, desktop, performance sur longs flux, accessibilité de base).

---

## 9. Glossaire / dictionnaire de données

Correspondance **champ brut → concept UI**. Source d'autorité pour nommer et
afficher.

| Champ / valeur brut(e) | Source | Concept UI |
| --- | --- | --- |
| `agent_id` | 3.1 | Identifiant complet d'un agent. |
| `agent` (préfixe court) | 3.2 | Même agent, forme courte (à rapprocher de `agent_id`). |
| `agent_type` | 3.1 / 3.2 / 3.3 | **Rôle** de l'agent (`bootstrap-lead`, `W1-backend`, `verifier`…). |
| Préfixe `W<n>-` | 3.1 | Worker n° `n` dans un lot. |
| `status` = `running` | 3.1 | **En cours**. |
| `status` = `completed` | 3.1 | **Terminé**. |
| `status` = `failed` / `blocked` | 3.1 | **Échec / Bloqué** (à faire remonter). |
| `started_at` / `completed_at` | 3.1 | Bornes temporelles (timeline, durée). |
| `duration_ms` | 3.1 / 3.2 | Durée d'exécution (millisecondes). |
| `event` = `agent_start` | 3.2 | Démarrage d'agent (timeline). |
| `event` = `agent_stop` / `agent_complete` | 3.2 | Arrêt d'agent (timeline). |
| `success` = `true` / `false` | 3.2 | Issue de l'arrêt (OK / échec). |
| `t` | 3.2 | Offset temporel de l'événement. |
| `agent_type` = `unknown` | 3.2 | Rôle indéterminé (pas une erreur). |
| bloc `thinking` | 3.3 | **Raisonnement** de l'agent (panneau F3). |
| bloc `redacted_thinking` | 3.3 | **Raisonnement masqué** (chiffré, non lisible). |
| bloc `text` | 3.3 | **Réponse visible** de l'agent. |
| bloc `tool_use` (+ `name`, `input`) | 3.3 | **Appel d'outil** (F4) : nom + entrée résumée. |
| `tool_result` (+ `tool_use_id`, `content`) | 3.3 | **Résultat d'outil** (F4), apparié par `tool_use_id`. |
| Table / liste de gates (`PASS` / `FAIL`) | 3.4 | **Gates** d'un rapport (F5). |
| Verdict une ligne | 3.4 | **Verdict final** d'un agent (F5). |
| `<branch-or-task>__<role>.md` | 3.4 | Nom de fichier d'un rapport (branche + rôle). |

---

*Fin du brief. Toute information non couverte par §3 / §9 n'est pas affichable et
ne doit pas apparaître dans la maquette.*
