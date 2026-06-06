<script setup lang="ts">
import type { MediaItem } from '../stores/media'

defineProps<{ item: MediaItem }>()
defineEmits<{ delete: [] }>()

function formatDuration(s: number | null): string {
  if (!s) return ''
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}
</script>

<template>
  <div class="bg-gray-800 rounded-lg overflow-hidden group relative">
    <!-- Thumbnail -->
    <div class="aspect-video bg-gray-700 relative">
      <img
        v-if="item.thumbnail_path"
        :src="`/media-files/${item.thumbnail_path}`"
        :alt="item.title"
        class="w-full h-full object-cover"
      />
      <div v-else class="w-full h-full flex items-center justify-center text-gray-500 text-3xl">▶</div>
      <span
        v-if="item.duration_seconds"
        class="absolute bottom-1.5 right-1.5 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded"
      >
        {{ formatDuration(item.duration_seconds) }}
      </span>
    </div>

    <!-- Info -->
    <div class="p-3">
      <div class="flex items-start justify-between gap-2">
        <p class="text-gray-100 text-sm font-medium line-clamp-2 flex-1">{{ item.title }}</p>
        <button
          class="text-gray-600 hover:text-red-400 transition-colors flex-shrink-0 opacity-0 group-hover:opacity-100 text-lg leading-none"
          title="Remove from library"
          @click="$emit('delete')"
        >
          ×
        </button>
      </div>
      <span v-if="item.platform" class="mt-1 inline-block text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded">
        {{ item.platform }}
      </span>
    </div>
  </div>
</template>
