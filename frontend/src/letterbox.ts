import type { MouseEvent as ReactMouseEvent } from 'react'

/**
 * For a contain-fitted <img> whose box fills its stage (small mesh thumbs
 * upscale to fit): was this click on the letterbox bands rather than the
 * visible image content? Letterbox clicks mean "backdrop" to a lightbox.
 */
export function inLetterbox(e: ReactMouseEvent<HTMLImageElement>): boolean {
  const el = e.currentTarget
  if (!el.naturalWidth || !el.naturalHeight) return false
  const box = el.getBoundingClientRect()
  const scale = Math.min(box.width / el.naturalWidth, box.height / el.naturalHeight)
  const w = el.naturalWidth * scale
  const h = el.naturalHeight * scale
  const x0 = box.left + (box.width - w) / 2
  const y0 = box.top + (box.height - h) / 2
  return e.clientX < x0 || e.clientX > x0 + w || e.clientY < y0 || e.clientY > y0 + h
}
