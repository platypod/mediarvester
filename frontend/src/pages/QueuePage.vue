<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useDownloadsStore } from '../stores/downloads'
import type { Download } from '../stores/downloads'
import { useSettingsStore } from '../stores/settings'
import DownloadCard from '../components/DownloadCard.vue'

const store = useDownloadsStore()
const settings = useSettingsStore()
const url = ref('')
const submitting = ref(false)
const formError = ref('')
let statusTimer: ReturnType<typeof setInterval> | null = null

const STATUSES = ['queued', 'downloading', 'done', 'error', 'retried'] as const

const search = ref('')
const statusFilter = ref<Set<(typeof STATUSES)[number]>>(new Set())
const platformFilter = ref('')
const creatorFilter = ref('')

function toggleStatus(status: (typeof STATUSES)[number]) {
  const next = new Set(statusFilter.value)
  if (next.has(status)) next.delete(status)
  else next.add(status)
  statusFilter.value = next
}

// Built from everything currently loaded, not just what's visible after
// filtering -- otherwise picking one platform would collapse the creator
// dropdown down to only creators seen under that platform, hiding the rest.
const knownPlatforms = computed(() =>
  [...new Set(store.items.map(d => d.platform).filter((p): p is string => !!p))].sort(),
)
const knownCreators = computed(() =>
  [...new Set(store.items.map(d => d.creator).filter((c): c is string => !!c))].sort(),
)

const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase()
  return store.items.filter((d: Download) => {
    if (statusFilter.value.size > 0 && !statusFilter.value.has(d.status)) return false
    if (platformFilter.value && d.platform !== platformFilter.value) return false
    if (creatorFilter.value && d.creator !== creatorFilter.value) return false
    if (q && !(d.title?.toLowerCase().includes(q) || d.url.toLowerCase().includes(q))) return false
    return true
  })
})

const hasActiveFilters = computed(
  () => statusFilter.value.size > 0 || !!platformFilter.value || !!creatorFilter.value || !!search.value,
)

function clearFilters() {
  statusFilter.value = new Set()
  platformFilter.value = ''
  creatorFilter.value = ''
  search.value = ''
}

async function submit() {
  if (!url.value.trim()) return
  submitting.value = true
  formError.value = ''
  try {
    await store.create(url.value.trim())
    url.value = ''
  } catch {
    formError.value = 'Failed to enqueue — check the URL and try again.'
  } finally {
    submitting.value = false
  }
}

function formatSince(iso: string): string {
  return new Date(iso + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
  store.startPolling()
  settings.fetchServiceStatus()
  statusTimer = setInterval(() => settings.fetchServiceStatus(), 30000)
})
onUnmounted(() => {
  store.stopPolling()
  if (statusTimer !== null) clearInterval(statusTimer)
})
</script>

<template>
  <div class="p-6 space-y-6 max-w-3xl">
    <div
      v-if="settings.serviceStatus.degraded"
      class="bg-yellow-900/40 border border-yellow-700/50 rounded-lg px-4 py-3 text-sm text-yellow-200"
    >
      <strong>Downloads may be degraded.</strong>
      {{ settings.serviceStatus.recent_failures }} recent failure(s) look like a site-side issue
      (rate limiting or a blocked format), not a problem with a specific video —
      first noticed around {{ formatSince(settings.serviceStatus.detected_since!) }}.
      This is usually temporary; try again later, or delete and re-add the failed download.
    </div>

    <div>
      <h1 class="text-xl font-semibold text-white mb-4">Queue</h1>
      <div class="bg-gray-800 rounded-lg p-4">
        <form class="flex gap-3" @submit.prevent="submit">
          <input
            v-model="url"
            placeholder="Paste any URL to download…"
            class="flex-1 bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
          />
          <button
            type="submit"
            :disabled="submitting"
            class="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
          >
            Download
          </button>
        </form>
        <p v-if="formError" class="mt-2 text-red-400 text-xs">{{ formError }}</p>
      </div>
    </div>

    <div v-if="store.items.length > 0" class="bg-gray-800 rounded-lg p-4 space-y-3">
      <input
        v-model="search"
        placeholder="Search title or URL…"
        class="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
      />
      <div class="flex flex-wrap items-center gap-2">
        <button
          v-for="status in STATUSES"
          :key="status"
          class="text-xs px-2 py-1 rounded font-medium transition-colors"
          :class="
            statusFilter.has(status)
              ? 'bg-blue-600 text-white'
              : 'bg-gray-700 text-gray-400 hover:text-gray-200'
          "
          @click="toggleStatus(status)"
        >
          {{ status }}
        </button>
        <select
          v-model="platformFilter"
          class="bg-gray-700 text-gray-300 text-xs rounded px-2 py-1 outline-none"
        >
          <option value="">All platforms</option>
          <option v-for="p in knownPlatforms" :key="p" :value="p">{{ p }}</option>
        </select>
        <select
          v-model="creatorFilter"
          class="bg-gray-700 text-gray-300 text-xs rounded px-2 py-1 outline-none"
        >
          <option value="">All creators</option>
          <option v-for="c in knownCreators" :key="c" :value="c">{{ c }}</option>
        </select>
        <button
          v-if="hasActiveFilters"
          class="text-xs text-gray-500 hover:text-gray-300 transition-colors ml-auto"
          @click="clearFilters"
        >
          Clear filters
        </button>
      </div>
    </div>

    <div v-if="store.items.length === 0" class="text-center py-16 text-gray-600">
      No downloads yet. Paste a URL above to get started.
    </div>

    <div v-else-if="filteredItems.length === 0" class="text-center py-16 text-gray-600">
      No downloads match the current filters.
    </div>

    <div v-else class="space-y-2">
      <DownloadCard
        v-for="dl in filteredItems"
        :key="dl.id"
        :download="dl"
        :show-owner="settings.isAdmin"
        @delete="store.remove(dl.id)"
      />
    </div>
  </div>
</template>
