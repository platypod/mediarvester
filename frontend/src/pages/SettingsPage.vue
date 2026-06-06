<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '../stores/settings'
import CookiesDropZone from '../components/CookiesDropZone.vue'

const store = useSettingsStore()

onMounted(async () => {
  await Promise.all([store.fetchMe(), store.fetchCookiesStatus()])
})

async function handleFile(file: File) {
  await store.uploadCookies(file)
}

function formatDate(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <div class="p-6 max-w-xl space-y-8">
    <h1 class="text-xl font-semibold text-white">Settings</h1>

    <!-- Identity -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-1">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider mb-3">Identity</h2>
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-sm font-bold">
          {{ store.user[0]?.toUpperCase() }}
        </div>
        <div>
          <p class="text-white font-medium">{{ store.user }}</p>
          <p class="text-xs text-gray-500">
            {{ store.user === 'anonymous' ? 'Not authenticated — running without Authelia' : 'Authenticated via Authelia' }}
          </p>
        </div>
      </div>
    </section>

    <!-- Cookies -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-4">
      <div>
        <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider">Cookies</h2>
      </div>

      <!-- Why section -->
      <div class="bg-gray-700/50 rounded-lg p-4 space-y-2 text-sm text-gray-300">
        <p class="font-medium text-gray-200">Why might I need this?</p>
        <p class="text-gray-400 text-xs leading-relaxed">
          Some content requires you to be logged in to access it — age-restricted videos,
          members-only posts, private playlists, or content that's only visible to followers.
          When mediarvester tries to download these, the platform rejects the request.
        </p>
        <p class="text-gray-400 text-xs leading-relaxed">
          Uploading your browser cookies lets mediarvester impersonate your logged-in session,
          the same way yt-dlp does when run locally.
        </p>
      </div>

      <!-- How to export -->
      <div class="bg-gray-700/50 rounded-lg p-4 space-y-3 text-sm">
        <p class="font-medium text-gray-200">How to export your cookies</p>
        <ol class="space-y-2 text-xs text-gray-400 list-decimal list-inside leading-relaxed">
          <li>
            Install a browser extension that can export cookies in Netscape format —
            <span class="text-gray-300 font-medium">Get cookies.txt LOCALLY</span>
            is a reliable option available for both
            <a href="https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
               target="_blank" class="text-blue-400 hover:underline">Chrome</a>
            and
            <a href="https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/"
               target="_blank" class="text-blue-400 hover:underline">Firefox</a>.
          </li>
          <li>Make sure you are logged in to the platforms you want to download from.</li>
          <li>Click the extension icon and export cookies for the relevant site (or all sites).</li>
          <li>Drop the downloaded <span class="text-gray-300 font-mono">cookies.txt</span> file below.</li>
        </ol>
        <p class="text-xs text-yellow-600/80">
          ⚠ Cookies grant access to your accounts. Only upload them to a mediarvester instance
          you control and trust.
        </p>
      </div>

      <!-- Status -->
      <div class="flex items-center gap-2 text-sm">
        <span
          class="w-2 h-2 rounded-full flex-shrink-0"
          :class="store.cookiesStatus.has_cookies ? 'bg-green-500' : 'bg-gray-600'"
        />
        <span v-if="store.cookiesStatus.has_cookies" class="text-gray-300">
          Cookies active — last uploaded {{ formatDate(store.cookiesStatus.uploaded_at) }}
        </span>
        <span v-else class="text-gray-500">No cookies uploaded yet</span>
      </div>

      <!-- Drop zone -->
      <CookiesDropZone @file="handleFile" />

      <!-- Feedback -->
      <div v-if="store.uploading" class="text-sm text-blue-400">Uploading…</div>
      <div v-else-if="store.uploadError" class="text-sm text-red-400">{{ store.uploadError }}</div>
    </section>
  </div>
</template>
