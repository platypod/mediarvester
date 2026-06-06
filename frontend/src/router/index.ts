import { createRouter, createWebHistory } from 'vue-router'
import QueuePage from '../pages/QueuePage.vue'
import LibraryPage from '../pages/LibraryPage.vue'
import SourcesPage from '../pages/SourcesPage.vue'
import PwaPage from '../pages/PwaPage.vue'
import CookiesPage from '../pages/CookiesPage.vue'
import SharePage from '../pages/SharePage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: LibraryPage },
    { path: '/queue', component: QueuePage },
    { path: '/sources', component: SourcesPage },
    { path: '/pwa', component: PwaPage },
    { path: '/cookies', component: CookiesPage },
    // Invoked by the OS share sheet when the PWA is used as a share target
    { path: '/share', component: SharePage },
  ],
})
