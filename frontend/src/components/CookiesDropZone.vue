<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{ file: [f: File] }>()

const dragging = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

function onDrop(e: DragEvent) {
  dragging.value = false
  const file = e.dataTransfer?.files[0]
  if (file) emit('file', file)
}

function onInput(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) emit('file', file)
}
</script>

<template>
  <div
    class="border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors"
    :class="dragging
      ? 'border-blue-500 bg-blue-900/20'
      : 'border-gray-600 hover:border-gray-400 hover:bg-gray-800/50'"
    @dragover.prevent="dragging = true"
    @dragleave="dragging = false"
    @drop.prevent="onDrop"
    @click="inputRef?.click()"
  >
    <p class="text-3xl mb-2">🍪</p>
    <p class="text-gray-300 text-sm font-medium">Drop your cookies.txt here</p>
    <p class="text-gray-500 text-xs mt-1">or click to browse</p>
    <input ref="inputRef" type="file" accept=".txt" class="hidden" @change="onInput" />
  </div>
</template>
