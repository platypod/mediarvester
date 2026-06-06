import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

interface CookiesStatus {
  has_cookies: boolean
  uploaded_at: string | null
}

export const useSettingsStore = defineStore('settings', () => {
  const user = ref<string>('anonymous')
  const cookiesStatus = ref<CookiesStatus>({ has_cookies: false, uploaded_at: null })
  const uploading = ref(false)
  const uploadError = ref('')

  async function fetchMe() {
    const data = await api.get<{ user: string }>('/api/settings/me')
    user.value = data.user
  }

  async function fetchCookiesStatus() {
    cookiesStatus.value = await api.get<CookiesStatus>('/api/settings/cookies')
  }

  async function uploadCookies(file: File): Promise<void> {
    uploading.value = true
    uploadError.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/settings/cookies', { method: 'POST', body: form })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      cookiesStatus.value = await res.json()
    } catch (e: unknown) {
      uploadError.value = e instanceof Error ? e.message : 'Upload failed.'
      throw e
    } finally {
      uploading.value = false
    }
  }

  return { user, cookiesStatus, uploading, uploadError, fetchMe, fetchCookiesStatus, uploadCookies }
})
