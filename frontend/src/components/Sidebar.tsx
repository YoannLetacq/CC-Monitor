// Sidebar: Claude Code sessions grouped by state + tmux pole panes.

import type { SessionState, SessionSummary, TmuxPane } from '../api/types'
import type { SidebarData } from '../hooks/useSessions'
import type { Selection } from '../App'

const STATE_LABELS: Record<SessionState, string> = {
  active: 'Actives',
  recent: 'Récentes',
  terminated: 'Terminées',
}

const STATE_DOTS: Record<SessionState, string> = {
  active: 'bg-emerald-400',
  recent: 'bg-amber-400',
  terminated: 'bg-gray-500',
}

interface SidebarProps {
  data: SidebarData
  selection: Selection
  onSelect: (selection: Selection) => void
}

function SessionItem({
  session,
  selected,
  onClick,
}: {
  session: SessionSummary
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 rounded-md text-sm flex items-center gap-2 transition-colors ${
        selected ? 'bg-indigo-600/30 text-white' : 'hover:bg-gray-800 text-gray-300'
      }`}
    >
      <span
        className={`h-2 w-2 rounded-full shrink-0 ${STATE_DOTS[session.state]}`}
      />
      <span className="truncate">
        <span className="block truncate">
          {session.title ?? session.sessionId.slice(0, 8)}
        </span>
        <span className="block text-xs text-gray-500 truncate">
          {session.projectSlug}
        </span>
      </span>
    </button>
  )
}

function PaneItem({
  pane,
  label,
  selected,
  onClick,
}: {
  pane: TmuxPane
  label: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2 rounded-md text-sm flex items-center gap-2 transition-colors ${
        selected ? 'bg-indigo-600/30 text-white' : 'hover:bg-gray-800 text-gray-300'
      }`}
    >
      <span
        className={`h-2 w-2 rounded-full shrink-0 ${
          pane.active ? 'bg-emerald-400' : 'bg-gray-500'
        }`}
      />
      <span className="truncate">
        <span className="block truncate">{label}</span>
        <span className="block text-xs text-gray-500 truncate">
          {pane.command}
          {pane.claudeSessionId !== null &&
            ` · session ${pane.claudeSessionId.slice(0, 8)}`}
        </span>
      </span>
    </button>
  )
}

export function Sidebar({ data, selection, onSelect }: SidebarProps) {
  const groups = (['active', 'recent', 'terminated'] as SessionState[]).map(
    (state) => ({
      state,
      items: data.sessions.filter((s) => s.state === state),
    }),
  )

  return (
    <aside className="w-72 shrink-0 h-full overflow-y-auto bg-gray-900 border-r border-gray-800 p-3 space-y-4">
      <div className="px-1">
        <h1 className="text-lg font-semibold text-white">CC-monitor</h1>
        <p className="text-xs text-gray-500">Supervision live des agents</p>
      </div>

      <section>
        <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
          Sessions Claude Code
        </h2>
        {data.error !== null && (
          <p className="px-1 text-xs text-red-400">
            Erreur de chargement des sessions
          </p>
        )}
        {groups.map(
          ({ state, items }) =>
            items.length > 0 && (
              <div key={state} className="mb-2">
                <h3 className="px-1 text-xs text-gray-500 mb-1">
                  {STATE_LABELS[state]} ({items.length})
                </h3>
                {items.map((session) => (
                  <SessionItem
                    key={session.sessionId}
                    session={session}
                    selected={
                      selection?.kind === 'claude' &&
                      selection.session.sessionId === session.sessionId
                    }
                    onClick={() => onSelect({ kind: 'claude', session })}
                  />
                ))}
              </div>
            ),
        )}
        {data.sessions.length === 0 && data.error === null && (
          <p className="px-1 text-xs text-gray-500">Aucune session observée</p>
        )}
      </section>

      <section>
        <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
          Pôles tmux
        </h2>
        {data.tmux.map((session) => (
          <div key={session.name} className="mb-2">
            <h3 className="px-1 text-xs text-gray-500 mb-1">{session.name}</h3>
            {session.windows.flatMap((window) =>
              window.panes.map((pane) => {
                const label = `${window.name} · #${pane.index}${
                  pane.title !== '' ? ` ${pane.title}` : ''
                }`
                return (
                  <PaneItem
                    key={pane.id}
                    pane={pane}
                    label={label}
                    selected={
                      selection?.kind === 'pane' &&
                      selection.pane.id === pane.id
                    }
                    onClick={() => onSelect({ kind: 'pane', pane, label })}
                  />
                )
              }),
            )}
          </div>
        ))}
        {data.tmux.length === 0 && (
          <p className="px-1 text-xs text-gray-500">Aucun pôle tmux détecté</p>
        )}
      </section>
    </aside>
  )
}
