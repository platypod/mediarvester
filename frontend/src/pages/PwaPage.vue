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

    <!-- Already installed badge -->
    <div v-if="isInstalled" class="flex items-center gap-2 text-sm text-green-400 bg-green-400/10 rounded-lg px-4 py-3">
      <span>✓</span>
      <span>mediarvester is installed as an app on this device.</span>
    </div>

    <!-- What is this -->
    <section class="bg-gray-800 rounded-lg p-5 space-y-2">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider mb-3">What is this?</h2>
      <p class="text-gray-400 text-sm leading-relaxed">
        Install mediarvester as an app on your phone and it will appear in the
        <strong class="text-gray-300">Share</strong> menu of YouTube, Instagram, TikTok, and any
        other app. Tap Share → mediarvester and the video is instantly added to your download
        queue — no copy-pasting URLs.
      </p>
    </section>

    <!-- Android instructions -->
    <section v-if="isAndroid || (!isIos && !isAndroid)" class="bg-gray-800 rounded-lg p-5 space-y-3">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider flex items-center gap-2">
        <span>🤖</span> Android — Chrome
      </h2>
      <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
        <li>Open this page in <strong class="text-gray-300">Chrome</strong> on your Android device.</li>
        <li>Tap the <strong class="text-gray-300">⋮ menu</strong> (top right) → <strong class="text-gray-300">Add to Home screen</strong>.</li>
        <li>Confirm — mediarvester is now installed.</li>
        <li>Go to any app, find a video, tap <strong class="text-gray-300">Share</strong> → <strong class="text-gray-300">mediarvester</strong>.</li>
      </ol>
    </section>

    <!-- iOS instructions -->
    <section v-if="isIos || (!isIos && !isAndroid)" class="bg-gray-800 rounded-lg p-5 space-y-3">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider flex items-center gap-2">
        <span>🍎</span> iPhone / iPad — Safari
      </h2>
      <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
        <li>Open this page in <strong class="text-gray-300">Safari</strong> — it must be Safari, not Chrome.</li>
        <li>Tap the <strong class="text-gray-300">Share button</strong> (square with arrow) at the bottom.</li>
        <li>Scroll down and tap <strong class="text-gray-300">Add to Home Screen</strong>.</li>
        <li>Go to any app, find a video, tap <strong class="text-gray-300">Share</strong> → <strong class="text-gray-300">mediarvester</strong>.</li>
      </ol>
      <p class="text-xs text-gray-500">Requires iOS 16.4 or later.</p>
    </section>
  </div>
</template>
