import { useLayoutEffect, useRef } from 'react'

/**
 * Keep the viewport pinned to its content when items are prepended above it —
 * upward feed pages while anchored, or live SSE prepends while scrolled down.
 *
 * `keys` is the feed's id list in display order; a prepend is "the first key
 * changed but the old first key is still present". Compensation is measured on
 * a VISIBLE element (its viewport position before vs after the commit), not on
 * document height: prepended sections start as content-visibility placeholders
 * whose estimated heights would over- or under-shoot a height-delta approach.
 * Measuring what actually moved also composes with native scroll anchoring —
 * where the browser (Chrome/Firefox, scrollY > 0) already re-pinned, the
 * element hasn't moved and this is a no-op; where it gave up (scrollY 0, or
 * Safari always), the element's shift is exactly the correction to apply.
 *
 * `pinAtTop` covers the anchored feed, where even a prepend at scrollY 0 must
 * not shove the viewport off the jump target. Without it (live feed), a
 * prepend while at the top is left alone on purpose: new arrivals should show.
 */
export function usePrependCompensation(keys: readonly string[], pinAtTop: boolean): void {
  const prevFirst = useRef<string | null>(null)
  const probe = useRef<{ el: Element; top: number; height: number } | null>(null)

  const first = keys.length > 0 ? keys[0] : null
  const prepended =
    prevFirst.current !== null &&
    first !== null &&
    first !== prevFirst.current &&
    keys.includes(prevFirst.current)
  if (prepended) {
    // Render phase: the DOM still shows the previous list — grab something in
    // the middle of the viewport and remember where it sits.
    const el = document.elementFromPoint(window.innerWidth / 2, window.innerHeight * 0.4)
    probe.current = {
      el: el ?? document.body,
      top: el?.getBoundingClientRect().top ?? 0,
      height: document.documentElement.scrollHeight,
    }
  }

  useLayoutEffect(() => {
    if (prepended && probe.current) {
      const compensate = pinAtTop || window.scrollY > 120
      // Fallback to document-height delta if the probed node was remounted —
      // coarser (content-visibility placeholders mis-estimate), but bounded.
      const delta = probe.current.el.isConnected
        ? probe.current.el.getBoundingClientRect().top - probe.current.top
        : document.documentElement.scrollHeight - probe.current.height
      if (compensate && delta !== 0) window.scrollBy(0, delta)
    }
    probe.current = null
    prevFirst.current = first
  })
}
