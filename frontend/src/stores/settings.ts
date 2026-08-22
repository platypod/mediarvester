import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

interface CookiesStatus {
  has_cookies: boolean
  uploaded_at: string | null
}

interface VersionInfo {
  version: string
  github_url: string
}

export interface ServiceStatus {
  degraded: boolean
  detected_since: string | null
  recent_failures: number
}

export const useSettingsStore = defineStore('settings', () => {
  const user = ref<string>('anonymous')
  const isAdmin = ref<boolean>(false)
  const cookiesStatus = ref<CookiesStatus>({ has_cookies: false, uploaded_at: null })
  const cookiesStatusError = ref('')
  const uploading = ref(false)
  const uploadError = ref('')
  const version = ref<string>('dev')
  const githubUrl = ref<string>('https://github.com/platypod/mediarvester')
  const serviceStatus = ref<ServiceStatus>({ degraded: false, detected_since: null, recent_failures: 0 })

  async function fetchMe() {
    const data = await api.get<{ user: string; is_admin: boolean }>('/api/settings/me')
    user.value = data.user
    isAdmin.value = data.is_admin
  }

  async function fetchVersion() {
    const data = await api.get<VersionInfo>('/api/settings/version')
    version.value = data.version
    githubUrl.value = data.github_url
  }

  async function fetchCookiesStatus() {
    cookiesStatusError.value = ''
    try {
      cookiesStatus.value = await api.get<CookiesStatus>('/api/settings/cookies')
    } catch (e: unknown) {
      cookiesStatusError.value = e instanceof Error ? e.message : 'Failed to fetch cookies status.'
      throw e
    }
  }

  async function uploadCookies(file: File): Promise<void> {
    uploading.value = true
    uploadError.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/settings/cookies', {
        method: 'POST',
        credentials: 'include',
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      cookiesStatus.value = await res.json()
      cookiesStatusError.value = ''
    } catch (e: unknown) {
      uploadError.value = e instanceof Error ? e.message : 'Upload failed.'
      throw e
    } finally {
      uploading.value = false
    }
  }

  async function fetchServiceStatus() {
    try {
      serviceStatus.value = await api.get<ServiceStatus>('/api/settings/service-status')
    } catch {
      // Non-critical — leave the last-known status rather than surfacing an error banner for this.
    }
  }

  return {
    user,
    isAdmin,
    cookiesStatus,
    cookiesStatusError,
    uploading,
    uploadError,
    version,
    githubUrl,
    serviceStatus,
    fetchMe,
    fetchCookiesStatus,
    fetchVersion,
    fetchServiceStatus,
    uploadCookies,
  }
})
