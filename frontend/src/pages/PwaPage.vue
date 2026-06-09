<script setup lang="ts">
import { onMounted, ref } from 'vue'

const isIos = ref(false)
const isAndroid = ref(false)
const isInstalled = ref(false)

onMounted(() => {
  isIos.value = /iphone|ipad|ipod/i.test(navigator.userAgent)
  isAndroid.value = /android/i.test(navigator.userAgent)
  isInstalled.value = window.matchMedia('(display-mode: standalone)').matches
})
</script>

<template>
  <div class="p-6 max-w-xl space-y-6">
    <h1 class="text-xl font-semibold text-white">Share from your phone</h1>

    <!-- Android: already installed -->
    <div v-if="isInstalled && isAndroid"
         class="flex items-center gap-2 text-sm text-green-400 bg-green-400/10 rounded-lg px-4 py-3">
      <span>✓</span>
      <span>mediarvester is installed as an app — share any video directly to it.</span>
    </div>

    <!-- ── ANDROID ─────────────────────────────────────────────────────────── -->
    <section v-if="isAndroid || (!isIos && !isAndroid)" class="bg-gray-800 rounded-lg p-5 space-y-3">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider flex items-center gap-2">
        <span>🤖</span> Android — Chrome
      </h2>
      <p class="text-gray-400 text-sm leading-relaxed">
        Install mediarvester as an app and it appears in the OS
        <strong class="text-gray-300">Share</strong> sheet — tap Share in YouTube, Instagram, or
        TikTok and choose mediarvester. The URL is queued instantly, no copy-pasting.
      </p>
      <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
        <li>Open this page in <strong class="text-gray-300">Chrome</strong> on your Android device.</li>
        <li>Tap <strong class="text-gray-300">⋮ menu → Add to Home screen</strong> and confirm.</li>
        <li>In any app tap <strong class="text-gray-300">Share → mediarvester</strong>.</li>
      </ol>
    </section>

    <!-- ── iOS ────────────────────────────────────────────────────────────── -->
    <section v-if="isIos || (!isIos && !isAndroid)" class="bg-gray-800 rounded-lg p-5 space-y-4">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider flex items-center gap-2">
        <span>🍎</span> iPhone / iPad — iOS Shortcut
      </h2>

      <!-- Limitation notice -->
      <div class="bg-yellow-900/30 border border-yellow-700/50 rounded-lg px-4 py-3 text-xs text-yellow-300/80 leading-relaxed">
        Apple's Share sheet does not support web apps directly. The workaround is a
        <strong class="text-yellow-200">native iOS Shortcut</strong> that acts as a bridge —
        it appears in the Share sheet and sends the URL to mediarvester via Safari.
      </div>

      <!-- How it works -->
      <p class="text-gray-400 text-sm leading-relaxed">
        Download the Shortcut below, open it in the Shortcuts app, and tap
        <strong class="text-gray-300">Add Shortcut</strong>. It will then appear
        in the Share sheet of any app.
      </p>

      <!-- Download button -->
      <a
        href="/api/shortcuts/mediarvester.shortcut"
        class="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2.5 rounded-lg transition-colors"
      >
        <span>↓</span>
        Download mediarvester Shortcut
      </a>

      <!-- Step by step -->
      <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
        <li>Tap the button above — Safari opens the Shortcuts app automatically.</li>
        <li>Tap <strong class="text-gray-300">Add Shortcut</strong>.</li>
        <li>Go to YouTube, Instagram, or any other app.</li>
        <li>Find a video and tap <strong class="text-gray-300">Share → mediarvester</strong>.</li>
        <li>
          Safari opens briefly, queues the download, then redirects to the queue.
          <span class="text-gray-500">(You must be logged in to mediarvester in Safari.)</span>
        </li>
      </ol>

      <p class="text-xs text-gray-600">Requires iOS 16.4 or later.</p>
    </section>
  </div>
</template>
