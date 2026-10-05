import { createRouter, createWebHashHistory } from 'vue-router'
import { hasToken } from '@/api/greader'

const routerBase = window.location.pathname
  .replace(/\/(?:login|register)\/?$/, '/')
  .replace(/[^/]+$/, '')

const router = createRouter({
  // hash 路由：pathname 恒为部署根，vite 的 `base: './'` 相对资源、以及这里与
  // greader.ts 从 pathname 推导 base 的逻辑都照旧成立 —— 打包产物仍然能免重建
  // 地放到任意子路径。实测路径层级（/item/xxx）会让这两件事同时失效（白屏）。
  history: createWebHashHistory(routerBase),
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
    {
      // 条目是阅读器里唯一真正的父子层级（列表 → 条目）：
      // 打开文章 = 下钻一级，返回 = 去掉这一段。详见 urlState.ts 头部说明。
      path: '/item/:hex',
      name: 'item',
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
