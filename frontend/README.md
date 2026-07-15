# CC-monitor frontend

React 19 + Vite + Tailwind 4 UI: session/pole selector sidebar with two live
views — Claude Code session feed (reasoning blocks, tool activity, heartbeat)
and tmux pane terminal view — both fed by SSE with auto-reconnect.

## Development

```sh
npm ci
npm run dev     # Vite on :5173, /api proxied to the backend on :8787
npm run lint    # ESLint
npm run test    # vitest (SSE client + ANSI stripper unit checks)
npm run build   # tsc -b && vite build
```

Run the backend alongside (`make dev-backend` at the repo root) for live data.

## tmux mock mode

The tmux endpoints (`/api/tmux/sessions`, `/api/tmux/panes/{id}/events`) are
defined by the API CONTRACT and built by the backend in parallel. Until
integration, enable the built-in mock:

```sh
VITE_TMUX_MOCK=1 npm run dev
```

Without the flag the typed client calls the real endpoints.
