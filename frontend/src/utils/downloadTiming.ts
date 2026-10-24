// Naive UTC ISO strings from the API (no "Z"), same convention as DownloadCard.

function parse(iso: string): Date {
  return new Date(iso + 'Z')
}

export function formatStart(iso: string, now: Date = new Date()): string {
  const d = parse(iso)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return time
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

interface Timed {
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

// "Queued 14:30" | "Started 14:32 · running 1m 05s" | "Started 14:32 · took 3m 12s".
// A row from before started_at existed has no start, so it shows nothing
// rather than a duration inflated by time spent queued.
export function timingLine(d: Timed, now: Date = new Date()): string {
  if (d.status === 'queued' && !d.started_at) return `Queued ${formatStart(d.created_at, now)}`
  if (!d.started_at) return ''
  const start = `Started ${formatStart(d.started_at, now)}`
  const startMs = parse(d.started_at).getTime()
  if (d.status === 'downloading' || d.status === 'queued') {
    return `${start} · running ${formatDuration((now.getTime() - startMs) / 1000)}`
  }
  if (d.finished_at) {
    return `${start} · took ${formatDuration((parse(d.finished_at).getTime() - startMs) / 1000)}`
  }
  return start
}
