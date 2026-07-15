// Live terminal view of a tmux pane: monospace buffer with autoscroll and
// scroll-lock, fed by the pane SSE stream (snapshot then deltas).

import { useRef } from 'react'

import type { TmuxPane } from '../api/types'
import { usePaneFeed } from '../hooks/usePaneFeed'
import { useStickToBottom } from '../hooks/useStickToBottom'
import type { SseStatus } from '../lib/sse'

const STATUS_LABELS: Record<SseStatus, string> = {
  connecting: 'Connexion au flux SSE…',
  open: 'En direct',
  retrying: 'Flux interrompu — reconnexion…',
  ended: 'Flux terminé',
}

export function PaneView({ pane, label }: { pane: TmuxPane; label: string }) {
  const feed = usePaneFeed(pane.id)
  const terminalRef = useRef<HTMLDivElement | null>(null)
  const scroll = useStickToBottom(terminalRef, feed.lines.length)

  return (
    <div className="flex flex-col h-full min-w-0">
      <header className="px-6 py-4 border-b border-gray-800 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-white truncate">{label}</h2>
          <p className="text-xs text-gray-500 truncate">
            {pane.command} · {pane.width}×{pane.height}
            {pane.claudeSessionId !== null &&
              ` · session Claude ${pane.claudeSessionId.slice(0, 8)}`}
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs text-gray-300 shrink-0">
          <span
            className={`h-2 w-2 rounded-full ${
              feed.closed
                ? 'bg-red-400'
                : feed.status === 'open'
                  ? 'bg-emerald-400 animate-pulse'
                  : 'bg-amber-400'
            }`}
          />
          {feed.closed ? 'Panneau fermé' : STATUS_LABELS[feed.status]}
        </span>
      </header>

      <div className="flex-1 min-h-0 relative p-4">
        <div
          ref={terminalRef}
          onScroll={scroll.onScroll}
          className="h-full overflow-auto bg-black rounded-lg border border-gray-800 p-3 font-mono text-xs leading-5 text-gray-200 whitespace-pre"
        >
          {feed.lines.length === 0 ? (
            <span className="text-gray-500">En attente du contenu du panneau…</span>
          ) : (
            feed.lines.join('\n')
          )}
        </div>
        {scroll.locked && (
          <button
            onClick={scroll.resume}
            className="absolute bottom-8 right-8 text-xs bg-gray-800/90 hover:bg-gray-700 text-gray-200 rounded px-3 py-1.5 shadow"
          >
            ▼ Reprendre le défilement
          </button>
        )}
        {feed.closed && (
          <div className="absolute inset-x-4 bottom-4 bg-red-950/80 border border-red-800 text-red-200 text-sm rounded-lg px-4 py-2">
            Ce panneau tmux a été fermé — le contenu affiché est figé.
          </div>
        )}
      </div>
    </div>
  )
}
