// Keep a scrollable container pinned to its bottom while new content arrives,
// unless the user scrolled up (scroll-lock). The caller owns the ref; this
// hook returns the lock state, the scroll listener and a resume action.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

const BOTTOM_EPSILON_PX = 40

export interface StickToBottom {
  locked: boolean
  onScroll: () => void
  resume: () => void
}

export function useStickToBottom(
  containerRef: RefObject<HTMLDivElement | null>,
  contentKey: unknown,
): StickToBottom {
  const [locked, setLocked] = useState(false)
  const lockedRef = useRef(false)

  const onScroll = useCallback(() => {
    const el = containerRef.current
    if (el === null) return
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_EPSILON_PX
    lockedRef.current = !atBottom
    setLocked(!atBottom)
  }, [containerRef])

  const resume = useCallback(() => {
    lockedRef.current = false
    setLocked(false)
    const el = containerRef.current
    if (el !== null) el.scrollTop = el.scrollHeight
  }, [containerRef])

  useEffect(() => {
    const el = containerRef.current
    if (el !== null && !lockedRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [containerRef, contentKey])

  return { locked, onScroll, resume }
}
