<script setup lang="ts">
import type { Source } from '../stores/sources'

const props = defineProps<{ source: Source; polling?: boolean }>()
defineEmits<{ delete: []; toggle: []; 'toggle-shorts': []; poll: [] }>()

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}
</script>

<template>
  <div class="bg-gray-800 rounded-lg p-4 flex items-start gap-4">
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2 flex-wrap mb-1">
        <span v-if="source.platform" class="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
          {{ source.platform }}
        </span>
        <span class="text-xs text-gray-500">every {{ source.poll_interval_minutes }}m</span>
        <span class="text-xs text-gray-500">· polled {{ timeAgo(source.last_polled_at) }}</span>
      </div>
      <p class="text-gray-100 text-sm font-medium truncate">{{ source.label ?? source.url }}</p>
      <p v-if="source.label" class="text-xs text-gray-500 truncate mt-0.5">{{ source.url }}</p>
      <label class="flex items-center gap-1.5 mt-1.5 text-xs text-gray-500 select-none">
        <input
          :checked="source.include_shorts"
          type="checkbox"
          class="rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-blue-500 w-3.5 h-3.5"
          @change="$emit('toggle-shorts')"
        />
        Include YouTube Shorts
      </label>
    </div>

    <div class="flex items-center gap-2 flex-shrink-0">
      <button
        class="text-xs px-2.5 py-1.5 rounded border transition-colors"
        :class="polling
          ? 'border-blue-600 text-blue-400 cursor-not-allowed opacity-60'
          : 'border-gray-600 text-gray-400 hover:border-gray-400 hover:text-gray-200'"
        :disabled="polling"
        title="Poll now"
        @click="$emit('poll')"
      >
        {{ polling ? '…' : '↻' }}
      </button>

      <!-- Enable toggle -->
      <button
        class="w-10 h-5 rounded-full transition-colors relative flex-shrink-0"
        :class="source.enabled ? 'bg-blue-600' : 'bg-gray-700'"
        @click="$emit('toggle')"
      >
        <span
          class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform"
          :class="source.enabled ? 'translate-x-5' : 'translate-x-0.5'"
        />
      </button>

      <button
        class="text-gray-600 hover:text-red-400 transition-colors text-lg leading-none"
        title="Remove source"
        @click="$emit('delete')"
      >
        ×
      </button>
    </div>
  </div>
</template>
