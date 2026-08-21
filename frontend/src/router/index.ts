import { createRouter, createWebHistory } from 'vue-router'
import { hasToken } from '@/api/greader'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/LoginView.vue'),
    },
    {
      path: '/',
      name: 'reader',
      component: () => import('@/views/ReaderView.vue'),
    },
  ],
})

const AUTH_ROUTES = ['login', 'register']

router.beforeEach((to) => {
  // 未登录 → 登录页
  if (!AUTH_ROUTES.includes(to.name as string) && !hasToken()) {
    return { name: 'login' }
  }
  // 已登录 → 阅读器
  if (AUTH_ROUTES.includes(to.name as string) && hasToken()) {
    return { name: 'reader' }
  }
})

export default router
