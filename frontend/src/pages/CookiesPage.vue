<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '../stores/settings'
import CookiesDropZone from '../components/CookiesDropZone.vue'

const store = useSettingsStore()

onMounted(() => store.fetchCookiesStatus())

async function handleFile(file: File) {
  await store.uploadCookies(file)
}

function formatDate(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleString()
}
</script>

<template>
  <div class="p-6 max-w-xl space-y-6">
    <h1 class="text-xl font-semibold text-white">Cookies</h1>

    <!-- Why section -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-3">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider mb-1">Why might I need this?</h2>
      <p class="text-gray-400 text-sm leading-relaxed">
        Some content requires you to be logged in — age-restricted videos, members-only posts,
        private playlists, or content only visible to followers. When mediarvester tries to download
        these, the platform rejects the request.
      </p>
      <p class="text-gray-400 text-sm leading-relaxed">
        Uploading your browser cookies lets mediarvester impersonate your logged-in session,
        the same way yt-dlp does when run locally.
      </p>
    </section>

    <!-- How to export -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-3">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider mb-1">How to export your cookies</h2>
      <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
        <li>
          Install a browser extension that exports cookies in Netscape format —
          <span class="text-gray-300 font-medium">Get cookies.txt LOCALLY</span>
          is a reliable option for
          <a href="https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
             target="_blank" class="text-blue-400 hover:underline">Chrome</a>
          and
          <a href="https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/"
             target="_blank" class="text-blue-400 hover:underline">Firefox</a>.
        </li>
        <li>Make sure you are logged in to the platforms you want to download from.</li>
        <li>Click the extension icon and export cookies for the relevant site (or all sites).</li>
        <li>Drop the downloaded <span class="text-gray-300 font-mono text-xs">cookies.txt</span> file below.</li>
      </ol>
      <p class="text-xs text-yellow-600/80 pt-1">
        ⚠ Cookies grant access to your accounts. Only upload them to a mediarvester instance
        you control and trust.
      </p>
    </section>

    <!-- Upload -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-4">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider">Upload</h2>

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

      <CookiesDropZone @file="handleFile" />

      <div v-if="store.uploading" class="text-sm text-blue-400">Uploading…</div>
      <div v-else-if="store.uploadError" class="text-sm text-red-400">{{ store.uploadError }}</div>
    </section>
  </div>
</template>
