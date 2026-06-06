<script setup lang="ts">
import type { Download } from '../stores/downloads'

defineProps<{ download: Download }>()
defineEmits<{ delete: [] }>()

const statusClass: Record<string, string> = {
  queued: 'bg-yellow-900/60 text-yellow-300',
  downloading: 'bg-blue-900/60 text-blue-300',
  done: 'bg-green-900/60 text-green-300',
  error: 'bg-red-900/60 text-red-300',
}
</script>

<template>
  <div class="bg-gray-800 rounded-lg p-4">
    <div class="flex items-start gap-3">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap mb-1">
          <span v-if="download.platform" class="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
            {{ download.platform }}
          </span>
          <span class="text-xs px-2 py-0.5 rounded font-medium" :class="statusClass[download.status]">
            {{ download.status }}
          </span>
        </div>
        <p class="text-gray-100 text-sm truncate font-medium">
          {{ download.title ?? download.url }}
        </p>
        <p v-if="download.title" class="text-xs text-gray-500 truncate mt-0.5">
          {{ download.url }}
        </p>
        <p v-if="download.error" class="text-xs text-red-400 mt-1">{{ download.error }}</p>
      </div>
      <button
        class="text-gray-600 hover:text-red-400 transition-colors flex-shrink-0 text-lg leading-none"
        title="Remove"
        @click="$emit('delete')"
      >
        ×
      </button>
    </div>

    <div v-if="download.status === 'downloading'" class="mt-3">
      <div class="h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          class="h-full bg-blue-500 rounded-full transition-all duration-300"
          :style="{ width: `${download.progress}%` }"
        />
      </div>
      <p class="mt-1 text-xs text-gray-500">{{ download.progress.toFixed(0) }}%</p>
    </div>
  </div>
</template>
