<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useDownloadsStore } from './stores/downloads'
import { useSettingsStore } from './stores/settings'

const route = useRoute()
const downloads = useDownloadsStore()
const settings = useSettingsStore()

const nav = [
  { label: 'Library', path: '/', icon: '▤' },
  { label: 'Queue', path: '/queue', icon: '↓' },
  { label: 'Sources', path: '/sources', icon: '★' },
]

const activeCount = computed(() => downloads.active.length)

onMounted(() => settings.fetchMe())
</script>

<template>
  <div class="flex h-full text-gray-100">
    <!-- Sidebar -->
    <aside class="w-48 flex-shrink-0 bg-gray-950 flex flex-col border-r border-gray-800">
      <div class="px-4 py-5 border-b border-gray-800">
        <span class="font-bold text-white tracking-wide text-sm">mediarvester</span>
      </div>

      <nav class="flex-1 px-2 py-3 space-y-0.5">
        <router-link
          v-for="item in nav"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors"
          :class="route.path === item.path
            ? 'bg-gray-800 text-white'
            : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800/50'"
        >
          <span class="text-base leading-none">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
          <span
            v-if="item.path === '/queue' && activeCount > 0"
            class="ml-auto bg-blue-600 text-white text-xs font-bold px-1.5 py-0.5 rounded-full"
          >
            {{ activeCount }}
          </span>
        </router-link>
      </nav>

      <!-- User + Settings at the bottom -->
      <div class="border-t border-gray-800">
        <router-link
          to="/settings"
          class="flex items-center gap-3 px-3 py-3 text-sm transition-colors mx-2 my-1 rounded-md"
          :class="route.path === '/settings'
            ? 'bg-gray-800 text-white'
            : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800/50'"
        >
          <div class="w-5 h-5 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
            {{ settings.user[0]?.toUpperCase() }}
          </div>
          <span class="truncate">{{ settings.user }}</span>
        </router-link>
      </div>
    </aside>

    <!-- Main content -->
    <main class="flex-1 overflow-y-auto bg-gray-900">
      <router-view />
    </main>
  </div>
</template>
