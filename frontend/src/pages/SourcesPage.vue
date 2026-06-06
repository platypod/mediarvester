<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSourcesStore } from '../stores/sources'
import SourceCard from '../components/SourceCard.vue'
import AddSourceDialog from '../components/AddSourceDialog.vue'

const store = useSourcesStore()
const dialogOpen = ref(false)
const polling = ref<Record<number, boolean>>({})

onMounted(() => store.fetchAll())

async function addSource(url: string, label: string, interval: number) {
  await store.create(url, label || undefined, interval)
  dialogOpen.value = false
}

async function pollNow(id: number) {
  polling.value[id] = true
  try {
    await store.poll(id)
  } finally {
    setTimeout(() => { polling.value[id] = false }, 3000)
  }
}
</script>

<template>
  <div class="p-6 max-w-3xl">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-xl font-semibold text-white">Sources</h1>
      <button
        class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
        @click="dialogOpen = true"
      >
        + Follow source
      </button>
    </div>

    <div v-if="store.items.length === 0" class="text-center py-24 text-gray-600">
      No followed sources yet. Add a channel, playlist, or profile to start auto-harvesting.
    </div>

    <div v-else class="space-y-2">
      <SourceCard
        v-for="s in store.items"
        :key="s.id"
        :source="s"
        :polling="polling[s.id]"
        @delete="store.remove(s.id)"
        @toggle="store.patch(s.id, { enabled: !s.enabled })"
        @poll="pollNow(s.id)"
      />
    </div>

    <AddSourceDialog
      :open="dialogOpen"
      @close="dialogOpen = false"
      @submit="addSource"
    />
  </div>
</template>
