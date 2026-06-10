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

const shareUrl = `${window.location.origin}/share?url=`
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
    <section v-if="isIos || (!isIos && !isAndroid)" class="bg-gray-800 rounded-lg p-5 space-y-5">
      <h2 class="text-sm font-medium text-gray-400 uppercase tracking-wider flex items-center gap-2">
        <span>🍎</span> iPhone / iPad — iOS Shortcut
      </h2>

      <div class="bg-gray-700/50 rounded-lg px-4 py-3 text-sm text-gray-400 leading-relaxed">
        Apple's Share sheet does not support web apps directly, and iOS no longer allows
        importing shortcut files from third-party servers. The workaround is to
        <strong class="text-gray-200">create a shortcut manually</strong> — it takes about
        a minute and only needs to be done once.
      </div>

      <!-- URL display -->
      <div class="space-y-1">
        <p class="text-xs text-gray-500 uppercase tracking-wider">Your mediarvester share URL</p>
        <div class="flex items-center gap-2 bg-gray-900 rounded-lg px-3 py-2">
          <code class="text-xs text-blue-300 break-all flex-1">{{ shareUrl }}</code>
        </div>
        <p class="text-xs text-gray-600">You will paste this into the shortcut below.</p>
      </div>

      <!-- Steps -->
      <div class="space-y-4">
        <div class="space-y-2">
          <p class="text-sm font-medium text-gray-300">Create the shortcut</p>
          <ol class="space-y-3 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
            <li>Open the <strong class="text-gray-300">Shortcuts</strong> app and tap <strong class="text-gray-300">+</strong> to create a new shortcut.</li>
            <li>Tap <strong class="text-gray-300">Add Action</strong>, search for <strong class="text-gray-300">Text</strong> and pick the <strong class="text-gray-300">Text</strong> action.</li>
            <li>
              In the text field, paste your share URL:<br>
              <code class="text-xs text-blue-300 break-all">{{ shareUrl }}</code><br>
              Then, without adding a space, tap the
              <strong class="text-gray-300">variable button</strong> (looks like
              <strong class="text-gray-300">{x}</strong> or a small token icon) and choose
              <strong class="text-gray-300">Shortcut Input</strong>. The field should read
              <code class="text-xs text-blue-300">…share?url=</code><em class="text-gray-500">[Shortcut Input]</em>.
            </li>
            <li>Tap <strong class="text-gray-300">+</strong> to add another action, search for <strong class="text-gray-300">Open URLs</strong> and add it. It will use the Text from the previous step automatically.</li>
            <li>Tap the shortcut name at the top, rename it to <strong class="text-gray-300">mediarvester</strong>, and tap <strong class="text-gray-300">Done</strong>.</li>
            <li>
              Tap the <strong class="text-gray-300">ⓘ</strong> (details) button, enable
              <strong class="text-gray-300">Show in Share Sheet</strong>, and set
              <strong class="text-gray-300">Receive</strong> to
              <strong class="text-gray-300">URLs</strong> and <strong class="text-gray-300">Text</strong>
              — both are needed because some apps (e.g. Instagram) share as text rather than a plain URL.
            </li>
          </ol>
        </div>

        <div class="space-y-2">
          <p class="text-sm font-medium text-gray-300">Use it</p>
          <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside leading-relaxed">
            <li>In YouTube, Instagram, or any other app find a video and tap <strong class="text-gray-300">Share</strong>.</li>
            <li>Choose <strong class="text-gray-300">mediarvester</strong> from the share sheet.</li>
            <li>Safari opens and the download is queued automatically.</li>
          </ol>
        </div>

        <!-- Auth note -->
        <div class="bg-gray-700/50 rounded-lg px-4 py-3 space-y-1.5 text-xs text-gray-400 leading-relaxed">
          <p class="font-medium text-gray-300">Authentication</p>
          <p>
            The shortcut opens mediarvester in Safari. If your session is active, the download
            is queued silently. If it has expired, Safari shows the login page — log in once
            and you are redirected back automatically, with the download queued.
          </p>
          <p>
            Check <strong class="text-gray-300">Remember me</strong> when logging in so your
            session stays active for 30 days and the login prompt rarely appears.
          </p>
        </div>
      </div>

      <p class="text-xs text-gray-600">Requires iOS 13 or later.</p>
    </section>
  </div>
</template>
