// Live terminal feed for a tmux pane: both snapshot and delta REPLACE the
// buffer (the backend sends the full current screen each time, since a
// terminal is screen-oriented, not a log), capped to bound memory.
// pane_closed flips a terminal-closed flag.

import { useEffect, useState } from 'react'

import { openPaneStream } from '../api/client'
import type { PaneDeltaEvent, PaneSnapshotEvent } from '../api/types'
import { stripAnsi } from '../lib/ansi'
import type { SseStatus } from '../lib/sse'

const MAX_LINES = 2000

export interface PaneFeed {
  status: SseStatus
  lines: string[]
  closed: boolean
}

export function usePaneFeed(paneId: string | null): PaneFeed {
  const [status, setStatus] = useState<SseStatus>('connecting')
  const [lines, setLines] = useState<string[]>([])
  const [closed, setClosed] = useState(false)

  // Consumers remount this hook via a React key when the pane changes,
  // so state starts fresh on every subscription — no reset needed here.
  useEffect(() => {
    if (paneId === null) return

    const close = openPaneStream(
      paneId,
      {
        snapshot: (data) => {
          const event = data as PaneSnapshotEvent
          setLines(event.lines.map(stripAnsi).slice(-MAX_LINES))
        },
        delta: (data) => {
          const event = data as PaneDeltaEvent
          setLines(event.lines.map(stripAnsi).slice(-MAX_LINES))
        },
        pane_closed: () => setClosed(true),
        heartbeat: () => undefined,
      },
      setStatus,
    )
    return close
  }, [paneId])

  return { status, lines, closed }
}
