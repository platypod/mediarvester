<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDownloadsStore } from '../stores/downloads'

type Status = 'processing' | 'done' | 'error'

const router = useRouter()
const store = useDownloadsStore()
const status = ref<Status>('processing')
const url = ref('')
const errorMessage = ref('')

function extractUrl(raw: string): string {
  const trimmed = raw.trim()
  // Already a URL
  try { new URL(trimmed); return trimmed } catch {}
  // URL embedded in text (e.g. Instagram shares "Check this out https://…")
  const match = trimmed.match(/https?:\/\/\S+/)
  return match ? match[0] : ''
}

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const raw = params.get('url') || params.get('text') || params.get('title') || ''
  url.value = extractUrl(raw)

  if (!url.value) {
    status.value = 'error'
    errorMessage.value = 'No URL found in the shared content.'
    return
  }

  try {
    await store.create(url.value)
    status.value = 'done'
    setTimeout(() => router.push('/queue'), 1800)
  } catch {
    status.value = 'error'
    errorMessage.value = 'Failed to add to queue — the server may be unreachable.'
  }
})
</script>

<template>
  <div class="flex items-center justify-center h-full">
    <div class="bg-gray-800 rounded-xl p-8 max-w-sm w-full mx-4 text-center space-y-4">

      <!-- Processing -->
      <template v-if="status === 'processing'">
        <div class="text-4xl animate-pulse">↓</div>
        <p class="text-gray-300 font-medium">Adding to queue…</p>
        <p class="text-gray-500 text-sm truncate">{{ url }}</p>
      </template>

      <!-- Done -->
      <template v-else-if="status === 'done'">
        <div class="text-4xl">✓</div>
        <p class="text-green-400 font-medium">Added to queue</p>
        <p class="text-gray-500 text-sm truncate">{{ url }}</p>
        <p class="text-gray-600 text-xs">Taking you to the queue…</p>
      </template>

      <!-- Error -->
      <template v-else>
        <div class="text-4xl">✕</div>
        <p class="text-red-400 font-medium">{{ errorMessage }}</p>
        <p v-if="url" class="text-gray-500 text-sm truncate">{{ url }}</p>
        <button
          class="mt-2 text-sm text-blue-400 hover:underline"
          @click="router.push('/')"
        >
          Go to Library
        </button>
      </template>

    </div>
  </div>
</template>
