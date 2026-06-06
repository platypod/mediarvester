<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMediaStore } from '../stores/media'
import MediaCard from '../components/MediaCard.vue'

const store = useMediaStore()
const filter = ref('')

onMounted(() => store.fetchAll())
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-xl font-semibold text-white">Library</h1>
      <input
        v-model="filter"
        placeholder="Filter by platform…"
        class="bg-gray-800 text-gray-100 rounded px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-gray-500 w-48"
        @change="store.fetchAll(filter || undefined)"
      />
    </div>

    <div v-if="store.items.length === 0" class="text-center py-24 text-gray-600">
      Your library is empty. Downloads will appear here once complete.
    </div>

    <div v-else class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
      <MediaCard
        v-for="item in store.items"
        :key="item.id"
        :item="item"
        @delete="store.remove(item.id)"
      />
    </div>
  </div>
</template>
