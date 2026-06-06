import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

export interface Source {
  id: number
  url: string
  label: string | null
  platform: string | null
  enabled: boolean
  poll_interval_minutes: number
  last_polled_at: string | null
  created_at: string
}

export const useSourcesStore = defineStore('sources', () => {
  const items = ref<Source[]>([])

  async function fetchAll() {
    items.value = await api.get<Source[]>('/api/sources')
  }

  async function create(url: string, label?: string, poll_interval_minutes = 60): Promise<Source> {
    const s = await api.post<Source>('/api/sources', { url, label, poll_interval_minutes })
    items.value.unshift(s)
    return s
  }

  async function patch(id: number, data: Partial<Pick<Source, 'label' | 'enabled' | 'poll_interval_minutes'>>) {
    const s = await api.patch<Source>(`/api/sources/${id}`, data)
    const idx = items.value.findIndex(x => x.id === id)
    if (idx >= 0) items.value[idx] = s
    return s
  }

  async function remove(id: number) {
    await api.delete(`/api/sources/${id}`)
    items.value = items.value.filter(s => s.id !== id)
  }

  async function poll(id: number) {
    await api.post(`/api/sources/${id}/poll`, {})
  }

  return { items, fetchAll, create, patch, remove, poll }
})
