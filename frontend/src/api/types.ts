// Wire types for the CC-monitor API.
// All payloads are camelCase (backend pydantic alias_generator=to_camel),
// tmux included (see backend/app/schemas/tmux.py).

export type SessionState = 'active' | 'recent' | 'terminated'

export interface SessionSummary {
  sessionId: string
  projectPath: string
  projectSlug: string
  resolved: boolean
  title: string | null
  state: SessionState
  lastActivity: string | null
  startedAt: string | null
}

// SSE payloads for GET /api/sessions/{id}/events
export type ToolStatus = 'running' | 'ok' | 'error'
export type BlockKind = 'thinking' | 'text' | 'redacted_thinking'

export interface ReasoningBlock {
  kind: BlockKind
  text: string | null
  agentId: string | null
  ts: string | null
}

export interface ToolCall {
  toolUseId: string
  name: string
  inputSummary: string | null
  status: ToolStatus
  resultPreview: string | null
  agentId: string | null
  ts: string | null
}

export interface AgentBlocksEvent {
  sessionId: string
  agentId: string | null
  blocks: ReasoningBlock[]
}

export interface ToolActivityEvent {
  sessionId: string
  agentId: string | null
  calls: ToolCall[]
}

export interface HeartbeatEvent {
  sessionId: string
  ts: string
}

// tmux API CONTRACT — GET /api/tmux/sessions
export interface TmuxPane {
  id: string
  index: number
  title: string
  active: boolean
  width: number
  height: number
  command: string
  claudeSessionId: string | null
}

export interface TmuxWindow {
  index: number
  name: string
  panes: TmuxPane[]
}

export interface TmuxSession {
  name: string
  windows: TmuxWindow[]
}

// SSE payloads for GET /api/tmux/panes/{id}/events
export interface PaneSnapshotEvent {
  lines: string[]
  // Line count of the snapshot (one past the last line).
  cursor: number
}

// The backend sends the FULL current screen on every delta (replace, not
// append) — a terminal is screen-oriented, not a log.
export interface PaneDeltaEvent {
  lines: string[]
}
