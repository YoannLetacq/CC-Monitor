// Typed client for the CC-monitor backend. Same-origin in production
// (frontend served by the backend); the Vite dev server proxies /api.

import { MOCK_TMUX_SESSIONS, mockPaneStream } from './mockTmux'
import type { SessionState, SessionSummary, TmuxSession } from './types'
import { connectSse } from '../lib/sse'
import type { SseHandlers, SseStatus } from '../lib/sse'

export const TMUX_MOCK = import.meta.env.VITE_TMUX_MOCK === '1'

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`GET ${path} -> HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export function fetchSessions(): Promise<SessionSummary[]> {
  return fetchJson<SessionSummary[]>('/api/sessions')
}

export function fetchTmuxSessions(): Promise<TmuxSession[]> {
  if (TMUX_MOCK) return Promise.resolve(MOCK_TMUX_SESSIONS)
  return fetchJson<TmuxSession[]>('/api/tmux/sessions')
}

/** Probe whether a Claude session is still streamable (state === active). */
async function sessionStillActive(sessionId: string): Promise<boolean> {
  try {
    const detail = await fetchJson<{ state: SessionState }>(
      `/api/sessions/${encodeURIComponent(sessionId)}`,
    )
    return detail.state === 'active'
  } catch {
    return false
  }
}

export function openSessionStream(
  sessionId: string,
  handlers: SseHandlers,
  onStatus?: (status: SseStatus) => void,
): () => void {
  return connectSse({
    url: `/api/sessions/${encodeURIComponent(sessionId)}/events`,
    handlers,
    onStatus,
    shouldRetry: () => sessionStillActive(sessionId),
  })
}

export function openPaneStream(
  paneId: string,
  handlers: SseHandlers,
  onStatus?: (status: SseStatus) => void,
): () => void {
  if (TMUX_MOCK) return mockPaneStream(paneId, handlers, onStatus)
  return connectSse({
    url: `/api/tmux/panes/${encodeURIComponent(paneId)}/events`,
    handlers,
    onStatus,
  })
}
