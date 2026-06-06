<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; submit: [url: string, label: string, interval: number] }>()

const url = ref('')
const label = ref('')
const interval = ref(60)
const error = ref('')

function submit() {
  if (!url.value.trim()) {
    error.value = 'URL is required.'
    return
  }
  emit('submit', url.value.trim(), label.value.trim(), interval.value)
  url.value = ''
  label.value = ''
  interval.value = 60
  error.value = ''
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      @click.self="$emit('close')"
    >
      <div class="bg-gray-800 rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h2 class="text-lg font-semibold text-white mb-4">Follow a source</h2>

        <div class="space-y-3">
          <div>
            <label class="block text-xs text-gray-400 mb-1">URL</label>
            <input
              v-model="url"
              placeholder="Channel, playlist, or profile URL…"
              class="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
              @keydown.enter="submit"
            />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-1">Label <span class="text-gray-600">(optional)</span></label>
            <input
              v-model="label"
              placeholder="Auto-detected if left blank"
              class="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500"
            />
          </div>
          <div>
            <label class="block text-xs text-gray-400 mb-1">Poll every (minutes)</label>
            <input
              v-model.number="interval"
              type="number"
              min="5"
              class="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <p v-if="error" class="text-red-400 text-xs">{{ error }}</p>
        </div>

        <div class="flex justify-end gap-2 mt-5">
          <button
            class="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
            @click="$emit('close')"
          >
            Cancel
          </button>
          <button
            class="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded font-medium transition-colors"
            @click="submit"
          >
            Follow
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
