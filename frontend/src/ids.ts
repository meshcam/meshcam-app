// User-facing ids are UUIDs. URLs generously accept refs the way docker/git
// do: a full UUID (dashes optional, case-insensitive) or a unique hex prefix
// of at least MIN_PREFIX hex chars — the backend resolves them the same way.

const MIN_PREFIX = 8

/** Canonical lowercase dashless hex for a ref, or null when it isn't one. */
export function normalizeIdRef(ref: string): string | null {
  const hex = ref.replace(/-/g, '').toLowerCase()
  return hex.length >= MIN_PREFIX && hex.length <= 32 && /^[0-9a-f]+$/.test(hex)
    ? hex
    : null
}

/** Does a canonical UUID match a (possibly prefix) ref? */
export function idMatchesRef(id: string, ref: string): boolean {
  const norm = normalizeIdRef(ref)
  return norm !== null && id.replace(/-/g, '').toLowerCase().startsWith(norm)
}
