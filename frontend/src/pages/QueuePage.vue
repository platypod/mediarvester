<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useDownloadsStore } from '../stores/downloads'
import DownloadCard from '../components/DownloadCard.vue'

const store = useDownloadsStore()
const url = ref('')
const submitting = ref(false)
const formError = ref('')

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

onMounted(() => store.startPolling())
onUnmounted(() => store.stopPolling())
</script>

<template>
  <div class="p-6 space-y-6 max-w-3xl">
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

    <div v-if="store.items.length === 0" class="text-center py-16 text-gray-600">
      No downloads yet. Paste a URL above to get started.
    </div>

    <div v-else class="space-y-2">
      <DownloadCard
        v-for="dl in store.items"
        :key="dl.id"
        :download="dl"
        @delete="store.remove(dl.id)"
      />
    </div>
  </div>
</template>
