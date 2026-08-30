interface HasProfile {
  width: number | null
  height: number | null
  vcodec: string | null
  acodec: string | null
  abr: number | null
}

export function formatResolution(item: HasProfile): string {
  if (item.height) return `${item.height}p`
  if (item.width && item.height) return `${item.width}x${item.height}`
  return ''
}

export function formatAudio(item: HasProfile): string {
  if (!item.acodec) return ''
  const codec = item.acodec.split('.')[0] // e.g. "mp4a.40.2" -> "mp4a"
  if (item.abr) return `${codec} ${Math.round(item.abr)}k`
  return codec
}
