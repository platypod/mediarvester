<script setup lang="ts">
import { ref } from 'vue'
import type { Download } from '../stores/downloads'

defineProps<{ download: Download; showOwner?: boolean }>()
defineEmits<{ delete: [] }>()

const showCompleted = ref(false)

const statusClass: Record<string, string> = {
  queued: 'bg-yellow-900/60 text-yellow-300',
  downloading: 'bg-blue-900/60 text-blue-300',
  done: 'bg-green-900/60 text-green-300',
  error: 'bg-red-900/60 text-red-300',
}

function formatTime(iso: string): string {
  return new Date(iso + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="bg-gray-800 rounded-lg p-4">
    <div class="flex items-start gap-3">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap mb-1">
          <span
            v-if="showOwner"
            class="text-xs bg-purple-900/60 text-purple-300 px-2 py-0.5 rounded"
            title="Owner (visible to admins only)"
          >
            {{ download.owner }}
          </span>
          <span v-if="download.platform" class="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">
            {{ download.platform }}
          </span>
          <span class="text-xs px-2 py-0.5 rounded font-medium" :class="statusClass[download.status]">
            {{ download.status }}
          </span>
          <span
            v-if="download.retry_count > 0"
            class="text-xs bg-orange-900/60 text-orange-300 px-2 py-0.5 rounded"
            title="Auto-retried after a previous failure"
          >
            retry {{ download.retry_count }}
          </span>
        </div>
        <p class="text-gray-100 text-sm truncate font-medium">
          {{ download.title ?? download.url }}
        </p>
        <p v-if="download.title" class="text-xs text-gray-500 truncate mt-0.5">
          {{ download.url }}
        </p>
        <p v-if="download.error" class="text-xs text-red-400 mt-1">{{ download.error }}</p>
        <p v-if="download.status === 'error'" class="text-xs mt-0.5">
          <span v-if="download.retry_at" class="text-gray-500">
            Will retry automatically around {{ formatTime(download.retry_at) }} — nothing to do.
          </span>
          <span v-else class="text-orange-400">
            Gave up after {{ download.retry_count + 1 }} attempt{{ download.retry_count > 0 ? 's' : '' }} —
            resubmit the URL to try again.
          </span>
        </p>
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
      <div class="mt-1 flex items-center gap-2 text-xs text-gray-500">
        <span>{{ download.progress.toFixed(0) }}%</span>
        <span v-if="download.total_entries">
          · {{ download.current_index ?? '?' }} of {{ download.total_entries }}
        </span>
        <span v-if="download.current_title" class="truncate">· {{ download.current_title }}</span>
      </div>
    </div>

    <div v-if="download.completed_items?.length" class="mt-2">
      <button
        class="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        @click="showCompleted = !showCompleted"
      >
        {{ showCompleted ? '▾' : '▸' }} {{ download.completed_items.length }} downloaded so far
      </button>
      <ul v-if="showCompleted" class="mt-1 space-y-0.5 max-h-40 overflow-y-auto">
        <li
          v-for="(item, i) in download.completed_items"
          :key="i"
          class="text-xs text-gray-500 truncate"
        >
          {{ item }}
        </li>
      </ul>
    </div>
  </div>
</template>
