# CC-monitor

Outil interne de **monitoring en pseudo temps réel** des sous-agents oh-my-claudecode
d'une session Claude Code active. Permet de découvrir et d'observer n'importe quelle
session de n'importe quel projet sur la machine — une session observée à la fois,
sélectionnable à la volée.

> **Lecture seule.** L'outil ne lit que des fichiers d'état ; il n'écrit jamais dans
> `~/.claude/**` ni dans le `.omc/**` des projets observés.

---

## Fonctionnement

- **Rafraîchissement pseudo temps réel** : latence ~0,5 – 3 s (SSE backend → frontend React).
- **Pas de suivi token-par-token** — observation de l'état des sous-agents OMC.
- **Mono-session observée** : une session à la fois, sélectionnable dans l'interface.
- **Multi-projets** : découverte automatique de toutes les sessions actives de la machine.

**Stack :**

| Couche | Technologie |
|--------|-------------|
| Backend | FastAPI (Python 3.13), uvicorn, SSE |
| Frontend | React 19 + Vite + Tailwind (TypeScript) |
| Gestion dépendances Python | [uv](https://docs.astral.sh/uv/) |

---

## Prérequis

- **Python 3.13+** (géré via `uv`)
- **[uv](https://docs.astral.sh/uv/)** — gestionnaire de paquets / environnements Python
- **Node.js LTS** (v20 ou supérieur recommandé)
- **npm** (inclus avec Node.js)

---

## Installation

```bash
# Cloner / accéder au dépôt
cd CC-monitor

# Installer toutes les dépendances (backend + frontend)
make install
```

Équivalent manuel :

```bash
cd backend && uv sync
cd frontend && npm install
```

---

## Lancement

### Développement (hot-reload, deux processus)

```bash
make dev
```

Lance en parallèle :
- **Backend** sur `http://localhost:8787` (API + SSE, rechargement automatique)
- **Frontend** sur `http://localhost:5173` (Vite dev server)

> Ctrl-C arrête les deux processus.

Équivalents manuels :

```bash
# Terminal 1
cd backend && uv run uvicorn app.main:app --reload --port 8787

# Terminal 2
cd frontend && npm run dev
```

### Production (mono-port 8787)

```bash
make build   # compile le frontend et le copie dans backend/static/
make start   # sert API + UI sur http://localhost:8787
```

Ou en une commande :

```bash
make start   # implique make build en dépendance
```

Équivalent manuel :

```bash
cd frontend && npm run build
rm -rf backend/static && cp -r frontend/dist backend/static
cd backend && uv run uvicorn app.main:app --port 8787
```

---

## Variables d'environnement

Copier `.env.example` en `.env` et renseigner les valeurs :

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `API_PORT` | Port d'écoute du backend (défaut : `8787`) |
| `CORS_ORIGINS` | Origines autorisées pour CORS — liste CSV (`http://a,http://b`) ou tableau JSON |
| `CLAUDE_PROJECTS_ROOT` | Chemin racine des projets Claude (`~/.claude/projects`) |

Voir [`backend/.env.example`](backend/.env.example) (si présent) ou `.env.example` racine
pour le format complet avec commentaires.

> **Important :** ne jamais committer le fichier `.env` (ignoré par `.gitignore`).

---

## Tests & quality gates

Ces commandes doivent toutes passer avant de merger sur `develop` ou `master`.

```bash
# Backend — linting (score cible : 10.00/10)
cd backend && uv run pylint app/

# Backend — tests
cd backend && uv run pytest

# Frontend — linting
cd frontend && npm run lint

# Frontend — build de validation
cd frontend && npm run build

# Vérification complète via le skill OMC
/preflight
```

### Cibles Makefile de test

```bash
make test   # exécute pytest + lint frontend + build frontend
```

---

## Commandes Makefile — référence rapide

| Commande | Description |
|----------|-------------|
| `make install` | Installe les dépendances backend (`uv sync`) et frontend (`npm install`) |
| `make dev` | Lance backend + frontend en mode développement (hot-reload, 2 processus) |
| `make build` | Compile le frontend et copie le build dans `backend/static/` |
| `make start` | Build puis lance le backend seul (mono-port 8787, API + UI) |
| `make test` | Exécute les tests backend et le lint + build frontend |

---

## Structure du dépôt

```
CC-monitor/
├── backend/          # FastAPI app (Python 3.13, uv)
│   ├── app/          # Code applicatif
│   ├── tests/        # Tests pytest
│   ├── static/       # Build frontend (généré par make build — gitignored)
│   └── pyproject.toml
├── frontend/         # React 19 + Vite + Tailwind (TypeScript)
│   ├── src/
│   ├── public/
│   └── package.json
├── docs/             # Design brief et documentation
├── proto/            # Prototypes HTML / wireframes
├── .env.example      # Variables d'environnement (sans valeurs secrètes)
├── Makefile          # Commandes de lancement et build
└── README.md
```

---

## Périmètre Git

- **Dépôt local uniquement** — pas de remote, pas de CI.
- **Gitflow allégé** :
  - `master` — production stable
  - `develop` — intégration courante
  - `feature/*`, `fix/*`, `chore/*`, `refactor/*` — branches de travail
- **Conventional Commits** : le type du commit correspond au préfixe de branche.
  Exemples : `feat(api): add SSE endpoint`, `fix(ui): correct session selector`.

---

## Vie privée & sécurité

- L'outil accède en **lecture seule** à `~/.claude/projects/**` et
  `<projet>/.omc/state`, `<projet>/.omc/reports`.
- **Aucune écriture** dans ces répertoires, aucune modification de l'état des agents observés.
- Aucune donnée n'est transmise à l'extérieur de la machine.
