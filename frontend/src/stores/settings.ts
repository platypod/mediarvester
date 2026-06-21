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

export const useSettingsStore = defineStore('settings', () => {
  const user = ref<string>('anonymous')
  const cookiesStatus = ref<CookiesStatus>({ has_cookies: false, uploaded_at: null })
  const uploading = ref(false)
  const uploadError = ref('')
  const version = ref<string>('dev')
  const githubUrl = ref<string>('https://github.com/platypod/mediarvester')

  async function fetchMe() {
    const data = await api.get<{ user: string }>('/api/settings/me')
    user.value = data.user
  }

  async function fetchVersion() {
    const data = await api.get<VersionInfo>('/api/settings/version')
    version.value = data.version
    githubUrl.value = data.github_url
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

  return { user, cookiesStatus, uploading, uploadError, version, githubUrl, fetchMe, fetchCookiesStatus, fetchVersion, uploadCookies }
})
