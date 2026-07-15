// Unit checks for the SSE client (injected fake EventSource) and the ANSI
// stripper — the two pure pieces the live views depend on.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { stripAnsi } from './ansi'
import { connectSse } from './sse'
import type { SseStatus } from './sse'

type Listener = (event: { data: string }) => void

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readyState = FakeEventSource.CONNECTING
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private listeners: Record<string, Listener[]> = {}

  url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, listener: Listener): void {
    ;(this.listeners[name] ??= []).push(listener)
  }

  emit(name: string, payload: unknown): void {
    this.listeners[name]?.forEach((listener) =>
      listener({ data: JSON.stringify(payload) }),
    )
  }

  failFatally(): void {
    this.readyState = FakeEventSource.CLOSED
    this.onerror?.()
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED
  }
}

const asImpl = FakeEventSource as unknown as typeof EventSource

describe('connectSse', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('dispatches parsed event payloads to the matching handler', () => {
    const received: unknown[] = []
    const close = connectSse({
      url: '/api/sessions/s1/events',
      handlers: { heartbeat: (data) => received.push(data) },
      eventSourceImpl: asImpl,
    })
    const source = FakeEventSource.instances[0]
    source.onopen?.()
    source.emit('heartbeat', { sessionId: 's1', ts: 'T' })
    expect(received).toEqual([{ sessionId: 's1', ts: 'T' }])
    close()
  })

  it('reconnects after a fatal error when shouldRetry allows it', async () => {
    const statuses: SseStatus[] = []
    const close = connectSse({
      url: '/x',
      handlers: {},
      onStatus: (status) => statuses.push(status),
      shouldRetry: () => Promise.resolve(true),
      retryDelayMs: 100,
      eventSourceImpl: asImpl,
    })
    FakeEventSource.instances[0].failFatally()
    await vi.advanceTimersByTimeAsync(100)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(statuses).toContain('retrying')
    close()
  })

  it('ends the stream when shouldRetry reports the target is gone', async () => {
    const statuses: SseStatus[] = []
    const close = connectSse({
      url: '/x',
      handlers: {},
      onStatus: (status) => statuses.push(status),
      shouldRetry: () => Promise.resolve(false),
      retryDelayMs: 100,
      eventSourceImpl: asImpl,
    })
    FakeEventSource.instances[0].failFatally()
    await vi.advanceTimersByTimeAsync(200)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(statuses.at(-1)).toBe('ended')
    close()
  })

  it('stops reconnecting once closed by the caller', async () => {
    const close = connectSse({
      url: '/x',
      handlers: {},
      shouldRetry: () => Promise.resolve(true),
      retryDelayMs: 100,
      eventSourceImpl: asImpl,
    })
    close()
    FakeEventSource.instances[0].failFatally()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeEventSource.instances).toHaveLength(1)
  })
})

describe('stripAnsi', () => {
  it('removes CSI color codes and OSC titles', () => {
    const esc = String.fromCharCode(27)
    const colored = `${esc}[32mok${esc}[0m plain ${esc}]0;title${String.fromCharCode(7)}end`
    expect(stripAnsi(colored)).toBe('ok plain end')
  })
})
