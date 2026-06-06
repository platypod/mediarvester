import { createRouter, createWebHistory } from 'vue-router'
import QueuePage from '../pages/QueuePage.vue'
import LibraryPage from '../pages/LibraryPage.vue'
import SourcesPage from '../pages/SourcesPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'
import SharePage from '../pages/SharePage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: LibraryPage },
    { path: '/queue', component: QueuePage },
    { path: '/sources', component: SourcesPage },
    { path: '/settings', component: SettingsPage },
    // Invoked by the OS share sheet when the PWA is used as a share target
    { path: '/share', component: SharePage },
  ],
})
