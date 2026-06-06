import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

export interface MediaItem {
  id: number
  title: string
  platform: string | null
  source_url: string
  local_path: string
  thumbnail_path: string | null
  duration_seconds: number | null
  download_id: number
}

export const useMediaStore = defineStore('media', () => {
  const items = ref<MediaItem[]>([])

  async function fetchAll(platform?: string) {
    const url = platform ? `/api/media?platform=${platform}` : '/api/media'
    items.value = await api.get<MediaItem[]>(url)
  }

  async function remove(id: number) {
    await api.delete(`/api/media/${id}`)
    items.value = items.value.filter(m => m.id !== id)
  }

  return { items, fetchAll, remove }
})
