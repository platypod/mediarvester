<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'

const props = defineProps<{ mediaId: number }>()

const state = ref<'idle' | 'loading' | 'missing'>('idle')

async function open() {
  // Opened synchronously: a window opened after the await is treated as an unrequested popup.
  const tab = window.open('', '_blank')
  state.value = 'loading'
  try {
    const { url } = await api.get<{ url: string | null }>(`/api/media/${props.mediaId}/jellyfin`)
    if (url && tab) {
      tab.location.href = url
      state.value = 'idle'
    } else {
      tab?.close()
      state.value = 'missing'
    }
  } catch {
    tab?.close()
    state.value = 'missing'
  }
}
</script>

<template>
  <button class="text-xs text-sky-400 hover:text-sky-300 disabled:opacity-50" :disabled="state === 'loading'" @click="open">
    {{ state === 'missing' ? 'Not in Jellyfin yet — scan requested, try again shortly' : 'Open in Jellyfin ↗' }}
  </button>
</template>
