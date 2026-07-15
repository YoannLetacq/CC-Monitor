// Live view of a Claude Code session: reasoning feed, tool activity,
// agent summary and heartbeat indicator, fed by the session SSE stream.

import { useEffect, useRef, useState } from 'react'

import type { ReasoningBlock, SessionSummary, ToolCall } from '../api/types'
import { useSessionFeed } from '../hooks/useSessionFeed'
import { useStickToBottom } from '../hooks/useStickToBottom'
import type { SseStatus } from '../lib/sse'

const STATUS_LABELS: Record<SseStatus, string> = {
  connecting: 'Connexion au flux SSE…',
  open: 'En direct',
  retrying: 'Flux interrompu — reconnexion…',
  ended: 'Session non active — flux terminé',
}

const STATUS_DOTS: Record<SseStatus, string> = {
  connecting: 'bg-amber-400',
  open: 'bg-emerald-400 animate-pulse',
  retrying: 'bg-amber-400 animate-pulse',
  ended: 'bg-gray-500',
}

const TOOL_DOTS: Record<ToolCall['status'], string> = {
  running: 'bg-amber-400 animate-pulse',
  ok: 'bg-emerald-400',
  error: 'bg-red-400',
}

const BLOCK_LABELS: Record<ReasoningBlock['kind'], string> = {
  thinking: 'réflexion',
  text: 'texte',
  redacted_thinking: 'réflexion masquée',
}

function agentLabel(agentId: string | null): string {
  return agentId === null ? 'orchestrateur' : `agent ${agentId.slice(0, 8)}`
}

function HeartbeatIndicator({ ts }: { ts: string | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  if (ts === null) {
    return <span className="text-xs text-gray-500">En attente de battement…</span>
  }
  const seconds = Math.max(0, Math.round((now - Date.parse(ts)) / 1000))
  return (
    <span className="text-xs text-gray-400">
      ♥ dernier battement il y a {seconds} s
    </span>
  )
}

export function SessionView({ session }: { session: SessionSummary }) {
  const live = session.state === 'active'
  const feed = useSessionFeed(live ? session.sessionId : null)
  const reasoningRef = useRef<HTMLDivElement | null>(null)
  const reasoningScroll = useStickToBottom(reasoningRef, feed.blocks.length)

  return (
    <div className="flex flex-col h-full min-w-0">
      <header className="px-6 py-4 border-b border-gray-800 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-white truncate">
            {session.title ?? session.sessionId}
          </h2>
          <p className="text-xs text-gray-500 truncate">
            {session.projectPath}
          </p>
        </div>
        {live && (
          <div className="flex items-center gap-3 shrink-0">
            <HeartbeatIndicator ts={feed.lastHeartbeat} />
            <span className="flex items-center gap-2 text-xs text-gray-300">
              <span className={`h-2 w-2 rounded-full ${STATUS_DOTS[feed.status]}`} />
              {STATUS_LABELS[feed.status]}
            </span>
          </div>
        )}
      </header>

      {!live ? (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-gray-400 text-sm max-w-md text-center px-6">
            Session {session.state === 'recent' ? 'récente' : 'terminée'} — le
            flux live n'est disponible que pour une session active (HTTP 409).
          </p>
        </div>
      ) : (
        <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
          <section className="flex flex-col min-h-0 bg-gray-900 rounded-lg border border-gray-800">
            <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between">
              <h3 className="text-sm font-medium text-gray-200">Raisonnement</h3>
              {feed.agents.length > 0 && (
                <span className="text-xs text-gray-500">
                  {feed.agents.length} sous-agent
                  {feed.agents.length > 1 ? 's' : ''}
                </span>
              )}
            </div>
            <div
              ref={reasoningRef}
              onScroll={reasoningScroll.onScroll}
              className="flex-1 overflow-y-auto px-4 py-3 space-y-3"
            >
              {feed.blocks.length === 0 && (
                <p className="text-xs text-gray-500">
                  En attente d'activité de l'agent…
                </p>
              )}
              {feed.blocks.map((block, index) => (
                <div key={index} className="text-sm">
                  <span className="text-xs text-gray-500">
                    {BLOCK_LABELS[block.kind]} · {agentLabel(block.agentId)}
                  </span>
                  <p
                    className={`whitespace-pre-wrap break-words ${
                      block.kind === 'thinking'
                        ? 'text-gray-400 italic'
                        : 'text-gray-200'
                    }`}
                  >
                    {block.kind === 'redacted_thinking'
                      ? '[contenu masqué]'
                      : block.text}
                  </p>
                </div>
              ))}
            </div>
            {reasoningScroll.locked && (
              <button
                onClick={reasoningScroll.resume}
                className="mx-4 mb-3 text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 rounded px-3 py-1.5"
              >
                ▼ Reprendre le défilement
              </button>
            )}
          </section>

          <section className="flex flex-col min-h-0 bg-gray-900 rounded-lg border border-gray-800">
            <div className="px-4 py-2 border-b border-gray-800">
              <h3 className="text-sm font-medium text-gray-200">
                Activité outils
              </h3>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
              {feed.calls.length === 0 && (
                <p className="text-xs text-gray-500">Aucun appel outil.</p>
              )}
              {feed.calls
                .slice()
                .reverse()
                .map((call) => (
                  <div
                    key={call.toolUseId}
                    className="flex items-start gap-2 text-sm"
                  >
                    <span
                      className={`h-2 w-2 mt-1.5 rounded-full shrink-0 ${TOOL_DOTS[call.status]}`}
                    />
                    <div className="min-w-0">
                      <span className="text-gray-200 font-medium">
                        {call.name}
                      </span>
                      <span className="text-xs text-gray-500 ml-2">
                        {agentLabel(call.agentId)}
                      </span>
                      {call.inputSummary !== null && (
                        <p className="text-xs text-gray-400 truncate">
                          {call.inputSummary}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
