// Live feed state for a Claude Code session: reasoning blocks, tool calls
// (upserted by toolUseId as results arrive), agent ids seen, heartbeat.

import { useEffect, useRef, useState } from 'react'

import { openSessionStream } from '../api/client'
import type {
  AgentBlocksEvent,
  HeartbeatEvent,
  ReasoningBlock,
  ToolActivityEvent,
  ToolCall,
} from '../api/types'
import type { SseStatus } from '../lib/sse'

const MAX_BLOCKS = 300
const MAX_CALLS = 200

export interface SessionFeed {
  status: SseStatus
  blocks: ReasoningBlock[]
  calls: ToolCall[]
  agents: string[]
  lastHeartbeat: string | null
}

export function useSessionFeed(sessionId: string | null): SessionFeed {
  const [status, setStatus] = useState<SseStatus>('connecting')
  const [blocks, setBlocks] = useState<ReasoningBlock[]>([])
  const [calls, setCalls] = useState<ToolCall[]>([])
  const [agents, setAgents] = useState<string[]>([])
  const [lastHeartbeat, setLastHeartbeat] = useState<string | null>(null)
  const agentsSeen = useRef<Set<string>>(new Set())

  // Consumers remount this hook via a React key when the session changes,
  // so state starts fresh on every subscription — no reset needed here.
  useEffect(() => {
    if (sessionId === null) return

    const noteAgent = (agentId: string | null) => {
      if (agentId !== null && !agentsSeen.current.has(agentId)) {
        agentsSeen.current.add(agentId)
        setAgents([...agentsSeen.current])
      }
    }

    const close = openSessionStream(
      sessionId,
      {
        agent_blocks: (data) => {
          const event = data as AgentBlocksEvent
          // The live parser emits empty-text thinking blocks while a block is
          // still streaming; only redacted blocks are meaningful without text.
          const blocks = event.blocks.filter(
            (block) =>
              block.kind === 'redacted_thinking' ||
              (block.text !== null && block.text !== ''),
          )
          blocks.forEach((block) => noteAgent(block.agentId))
          if (blocks.length > 0) {
            setBlocks((prev) => [...prev, ...blocks].slice(-MAX_BLOCKS))
          }
        },
        tool_activity: (data) => {
          const event = data as ToolActivityEvent
          event.calls.forEach((call) => noteAgent(call.agentId))
          setCalls((prev) => {
            const next = [...prev]
            for (const call of event.calls) {
              const index = next.findIndex(
                (c) => c.toolUseId === call.toolUseId,
              )
              if (index >= 0) next[index] = call
              else next.push(call)
            }
            return next.slice(-MAX_CALLS)
          })
        },
        heartbeat: (data) => {
          setLastHeartbeat((data as HeartbeatEvent).ts)
        },
      },
      setStatus,
    )
    return close
  }, [sessionId])

  return { status, blocks, calls, agents, lastHeartbeat }
}
