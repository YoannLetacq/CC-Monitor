// Framework-free SSE client with auto-reconnect.
//
// The native EventSource retries transparently while the connection is in
// CONNECTING state; when the server refuses the stream (readyState CLOSED —
// e.g. HTTP 409 on a session that is no longer active, 404 on a closed pane)
// this client asks `shouldRetry` whether the target is still streamable and
// either recreates the source after `retryDelayMs` or ends the stream.
//
// The EventSource implementation is injectable so the logic is unit-testable
// without a browser.

export type SseStatus = 'connecting' | 'open' | 'retrying' | 'ended'

export type SseHandlers = Record<string, (data: unknown) => void>

export interface SseOptions {
  url: string
  handlers: SseHandlers
  onStatus?: (status: SseStatus) => void
  /** Probe called before reconnecting after a server refusal; false → ended. */
  shouldRetry?: () => Promise<boolean>
  retryDelayMs?: number
  eventSourceImpl?: typeof EventSource
}

export function connectSse(options: SseOptions): () => void {
  const {
    url,
    handlers,
    onStatus,
    shouldRetry,
    retryDelayMs = 3000,
    eventSourceImpl = EventSource,
  } = options

  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let closed = false

  const setStatus = (status: SseStatus) => {
    if (!closed) onStatus?.(status)
  }

  const open = () => {
    if (closed) return
    setStatus('connecting')
    source = new eventSourceImpl(url)
    for (const [event, handler] of Object.entries(handlers)) {
      source.addEventListener(event, (message) => {
        if (closed) return
        try {
          handler(JSON.parse((message as MessageEvent).data as string))
        } catch {
          // Malformed payloads are dropped; the stream itself stays healthy.
        }
      })
    }
    source.onopen = () => setStatus('open')
    source.onerror = () => {
      if (closed || !source) return
      if (source.readyState !== eventSourceImpl.CLOSED) {
        // Native retry in progress (readyState CONNECTING).
        setStatus('retrying')
        return
      }
      source.close()
      source = null
      setStatus('retrying')
      const probe = shouldRetry ? shouldRetry() : Promise.resolve(true)
      probe
        .catch(() => true)
        .then((retry) => {
          if (closed) return
          if (!retry) {
            setStatus('ended')
            return
          }
          timer = setTimeout(open, retryDelayMs)
        })
    }
  }

  open()

  return () => {
    closed = true
    if (timer !== null) clearTimeout(timer)
    source?.close()
    source = null
  }
}
