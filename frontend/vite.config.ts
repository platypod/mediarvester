import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'mediarvester',
        short_name: 'mediarvester',
        description: 'Self-hosted media downloader — YouTube, Instagram, TikTok and more.',
        theme_color: '#030712',
        background_color: '#111827',
        display: 'standalone',
        start_url: '/',
        icons: [
          {
            src: '/icon.svg',
            sizes: 'any',
            type: 'image/svg+xml',
            purpose: 'any',
          },
        ],
        // Registers the app as a share target in the OS share sheet.
        // When a user shares a URL to mediarvester, the OS opens /share?url=...
        share_target: {
          action: '/share',
          method: 'GET',
          params: {
            title: 'title',
            text: 'text',
            url: 'url',
          },
        },
      },
    }),
  ],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/media-files': 'http://localhost:8080',
    },
  },
})
