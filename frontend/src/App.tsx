import { useState } from 'react'

import type { SessionSummary, TmuxPane } from './api/types'
import { PaneView } from './components/PaneView'
import { SessionView } from './components/SessionView'
import { Sidebar } from './components/Sidebar'
import { useSessions } from './hooks/useSessions'

export type Selection =
  | { kind: 'claude'; session: SessionSummary }
  | { kind: 'pane'; pane: TmuxPane; label: string }
  | null

function App() {
  const data = useSessions()
  const [selection, setSelection] = useState<Selection>(null)

  return (
    <div className="h-screen flex bg-gray-950 text-gray-100">
      <Sidebar data={data} selection={selection} onSelect={setSelection} />
      <main className="flex-1 min-w-0">
        {selection === null ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center space-y-2 px-6">
              <h2 className="text-xl font-semibold text-white">
                Aucune session sélectionnée
              </h2>
              <p className="text-sm text-gray-400 max-w-md">
                Choisissez une session Claude Code ou un panneau tmux dans la
                barre latérale pour suivre son activité en direct.
              </p>
            </div>
          </div>
        ) : selection.kind === 'claude' ? (
          <SessionView
            key={selection.session.sessionId}
            session={selection.session}
          />
        ) : (
          <PaneView
            key={selection.pane.id}
            pane={selection.pane}
            label={selection.label}
          />
        )}
      </main>
    </div>
  )
}

export default App
