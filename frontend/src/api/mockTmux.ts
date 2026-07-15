// Mock implementation of the tmux API CONTRACT, used until the backend-tmux
// pole lands (enable with VITE_TMUX_MOCK=1). Shapes mirror the contract
// exactly so swapping to the real endpoints is a flag flip.

import type { PaneDeltaEvent, PaneSnapshotEvent, TmuxSession } from './types'
import type { SseHandlers, SseStatus } from '../lib/sse'

export const MOCK_TMUX_SESSIONS: TmuxSession[] = [
  {
    name: 'projet-builder',
    windows: [
      {
        index: 0,
        name: 'conductor',
        panes: [
          {
            id: '0',
            index: 0,
            title: 'conductor',
            active: true,
            width: 190,
            height: 45,
            command: 'claude',
            claude_session_id: null,
          },
        ],
      },
      {
        index: 1,
        name: 'poles',
        panes: [
          {
            id: '1',
            index: 0,
            title: 'backend-tmux',
            active: true,
            width: 95,
            height: 45,
            command: 'claude',
            claude_session_id: 'mock-backend-session',
          },
          {
            id: '2',
            index: 1,
            title: 'frontend-mvp',
            active: false,
            width: 95,
            height: 45,
            command: 'claude',
            claude_session_id: null,
          },
        ],
      },
    ],
  },
]

const MOCK_LINES = [
  '$ omc team api read-worker-status --json',
  '{"ok": true, "workers": [{"name": "worker-1", "alive": true}]}',
  '⏺ Running quality gates…',
  'pytest ......................... 219 passed',
  'pylint: Your code has been rated at 10.00/10',
  '⏺ Pushing branch feature/tmux-poles',
]

export function mockPaneStream(
  paneId: string,
  handlers: SseHandlers,
  onStatus?: (status: SseStatus) => void,
): () => void {
  let tick = 0
  onStatus?.('open')
  const snapshot: PaneSnapshotEvent = {
    lines: [`[mock] tmux pane %${paneId} — flux simulé`, ...MOCK_LINES],
    cursor: 0,
  }
  handlers.snapshot?.(snapshot)
  const timer = setInterval(() => {
    tick += 1
    const delta: PaneDeltaEvent = {
      lines: [`${MOCK_LINES[tick % MOCK_LINES.length]}`],
    }
    handlers.delta?.(delta)
    if (tick % 5 === 0) handlers.heartbeat?.({ ts: '' })
  }, 1500)
  return () => clearInterval(timer)
}
