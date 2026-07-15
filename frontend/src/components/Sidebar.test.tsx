// Collapse behavior + section ordering of the sidebar (F2): terminated
// sessions are collapsed by default and the tmux poles sit above them.
// Rendered with react-dom/server — no DOM environment needed.

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { Sidebar, TerminatedSection } from './Sidebar'
import type { SessionState, SessionSummary, TmuxSession } from '../api/types'
import type { SidebarData } from '../hooks/useSessions'

function session(id: string, state: SessionState): SessionSummary {
  return {
    sessionId: id,
    projectPath: `/proj/${id}`,
    projectSlug: `proj-${id}`,
    resolved: true,
    title: `title-${id}`,
    state,
    lastActivity: null,
    startedAt: null,
  }
}

const tmux: TmuxSession[] = [
  {
    name: 'team-run',
    windows: [
      {
        index: 0,
        name: 'workers',
        panes: [
          {
            id: '%1',
            index: 0,
            title: 'pole-a',
            active: true,
            width: 80,
            height: 24,
            command: 'claude',
            claudeSessionId: null,
          },
        ],
      },
    ],
  },
]

const data: SidebarData = {
  sessions: [
    session('act-1', 'active'),
    session('rec-1', 'recent'),
    session('ter-1', 'terminated'),
    session('ter-2', 'terminated'),
  ],
  tmux,
  error: null,
  refresh: () => {},
}

describe('Sidebar terminated collapse (F2)', () => {
  it('collapses terminated sessions by default, header keeps the count', () => {
    const html = renderToStaticMarkup(
      <Sidebar data={data} selection={null} onSelect={() => {}} />,
    )
    expect(html).toContain('Terminées (2)')
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('title-ter-1')
    expect(html).not.toContain('title-ter-2')
    // Active and recent stay expanded.
    expect(html).toContain('title-act-1')
    expect(html).toContain('title-rec-1')
  })

  it('orders sections: Actives, Récentes, Pôles tmux, Terminées', () => {
    const html = renderToStaticMarkup(
      <Sidebar data={data} selection={null} onSelect={() => {}} />,
    )
    const order = [
      html.indexOf('Actives'),
      html.indexOf('Récentes'),
      html.indexOf('Pôles tmux'),
      html.indexOf('Terminées'),
    ]
    expect(order.every((i) => i >= 0)).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
  })

  it('shows terminated sessions when expanded', () => {
    const html = renderToStaticMarkup(
      <TerminatedSection
        sessions={[session('ter-1', 'terminated')]}
        open={true}
        onToggle={() => {}}
        selection={null}
        onSelect={() => {}}
      />,
    )
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('title-ter-1')
  })

  it('renders nothing when there are no terminated sessions', () => {
    const html = renderToStaticMarkup(
      <TerminatedSection
        sessions={[]}
        open={false}
        onToggle={() => {}}
        selection={null}
        onSelect={() => {}}
      />,
    )
    expect(html).toBe('')
  })
})
