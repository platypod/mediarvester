<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useDownloadsStore } from './stores/downloads'
import { useSettingsStore } from './stores/settings'

const route = useRoute()
const downloads = useDownloadsStore()
const settings = useSettingsStore()

const nav = [
  { label: 'Library',  path: '/',        icon: '▤' },
  { label: 'Queue',    path: '/queue',   icon: '↓' },
  { label: 'Sources',  path: '/sources', icon: '★' },
  { label: 'Phone',    path: '/pwa',     icon: '⬆' },
  { label: 'Cookies',  path: '/cookies', icon: '🍪' },
]

const activeCount = computed(() => downloads.active.length)

onMounted(() => {
  settings.fetchMe()
  settings.fetchVersion()
})
</script>

<template>
  <div class="flex h-full text-gray-100">

    <!-- ── Sidebar (md+) ──────────────────────────────────────────────── -->
    <aside class="hidden md:flex w-48 flex-shrink-0 bg-gray-950 flex-col border-r border-gray-800">
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

      <!-- Identity chip -->
      <div class="border-t border-gray-800 px-3 py-3 flex items-center gap-2">
        <div class="w-6 h-6 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
          {{ settings.user[0]?.toUpperCase() }}
        </div>
        <span class="text-xs text-gray-500 truncate">{{ settings.user }}</span>
      </div>

      <!-- Version / GitHub -->
      <div class="border-t border-gray-800 px-3 py-2 flex items-center justify-between">
        <span class="text-xs text-gray-600">{{ settings.version }}</span>
        <a
          :href="settings.githubUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="text-gray-600 hover:text-gray-400 transition-colors"
          title="GitHub repository"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
          </svg>
        </a>
      </div>
    </aside>

    <!-- ── Main content ───────────────────────────────────────────────── -->
    <main class="flex-1 overflow-y-auto bg-gray-900 pb-16 md:pb-0">
      <router-view />
    </main>

    <!-- ── Bottom nav (mobile only) ──────────────────────────────────── -->
    <nav class="md:hidden fixed bottom-0 inset-x-0 bg-gray-950 border-t border-gray-800 flex z-50">
      <router-link
        v-for="item in nav"
        :key="item.path"
        :to="item.path"
        class="flex-1 flex flex-col items-center justify-center py-2 gap-0.5 text-xs transition-colors relative"
        :class="route.path === item.path
          ? 'text-white'
          : 'text-gray-500 hover:text-gray-300'"
      >
        <span class="text-lg leading-none">{{ item.icon }}</span>
        <span class="leading-none">{{ item.label }}</span>
        <!-- Active indicator -->
        <span
          v-if="item.path === '/queue' && activeCount > 0"
          class="absolute top-1.5 right-1/4 translate-x-2 w-4 h-4 bg-blue-600 text-white text-[10px] font-bold rounded-full flex items-center justify-center"
        >
          {{ activeCount }}
        </span>
      </router-link>
    </nav>

  </div>
</template>
