import { createRouter, createWebHistory } from 'vue-router'

import DashboardView from '../RushApp.vue'


export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
  ],
})
