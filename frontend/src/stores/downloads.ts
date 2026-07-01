import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../api/client'

export interface Download {
  id: number
  url: string
  title: string | null
  platform: string | null
  status: 'queued' | 'downloading' | 'done' | 'error'
  progress: number
  error: string | null
  source_id: number | null
  created_at: string
  finished_at: string | null
}

export const useDownloadsStore = defineStore('downloads', () => {
  const items = ref<Download[]>([])
  let timer: ReturnType<typeof setInterval> | null = null

  async function fetchAll(status?: string) {
    const url = status ? `/api/downloads?status=${status}` : '/api/downloads'
    items.value = await api.get<Download[]>(url)
  }

  async function create(url: string): Promise<Download> {
    const dl = await api.post<Download>('/api/downloads', { url })
    const idx = items.value.findIndex(d => d.id === dl.id)
    if (idx >= 0) {
      items.value.splice(idx, 1)
    }
    items.value.unshift(dl)
    return dl
  }

  async function remove(id: number) {
    await api.delete(`/api/downloads/${id}`)
    items.value = items.value.filter(d => d.id !== id)
  }

  function startPolling() {
    fetchAll()
    timer = setInterval(fetchAll, 2000)
  }

  function stopPolling() {
    if (timer !== null) clearInterval(timer)
    timer = null
  }

  const active = computed(() =>
    items.value.filter(d => d.status === 'queued' || d.status === 'downloading'),
  )

  return { items, active, fetchAll, create, remove, startPolling, stopPolling }
})
