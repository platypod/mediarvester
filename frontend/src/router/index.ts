import { createRouter, createWebHistory } from 'vue-router'
import QueuePage from '../pages/QueuePage.vue'
import LibraryPage from '../pages/LibraryPage.vue'
import SourcesPage from '../pages/SourcesPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: LibraryPage },
    { path: '/queue', component: QueuePage },
    { path: '/sources', component: SourcesPage },
    { path: '/settings', component: SettingsPage },
  ],
})
