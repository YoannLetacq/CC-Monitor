// Poll the session and tmux listings so the sidebar stays fresh.

import { useCallback, useEffect, useState } from 'react'

import { fetchSessions, fetchTmuxSessions } from '../api/client'
import type { SessionSummary, TmuxSession } from '../api/types'

const POLL_MS = 10_000

export interface SidebarData {
  sessions: SessionSummary[]
  tmux: TmuxSession[]
  error: string | null
  refresh: () => void
}

export function useSessions(): SidebarData {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [tmux, setTmux] = useState<TmuxSession[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    fetchSessions()
      .then((data) => {
        setSessions(data)
        setError(null)
      })
      .catch((err: unknown) => setError(String(err)))
    fetchTmuxSessions()
      .then(setTmux)
      .catch(() => setTmux([]))
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh])

  return { sessions, tmux, error, refresh }
}
