import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { i18n, locale } from './i18n'

/** 让 PWA 的应用名跟随浏览器 / 系统语言（只有英文环境用英文名，其余一律中文）
 *
 * Web App Manifest 没有本地化机制：name / short_name 只能是一个字符串，Chrome 和 Firefox
 * 也都不认 _locales，所以只能按语言换一份清单。默认挂的那份（中文）就在 index.html 里写死，
 * 这里只在英文环境下换成 manifest.en.json。两份清单放同一目录，里面的相对 id / start_url
 * 解析出来完全一样，所以切换语言不会变成两个应用。
 *
 * iOS 的主屏名字来自 <meta name="apple-mobile-web-app-title">，那条要跟着改。
 * 中文用户这边一个节点都不碰（默认那份本来就是中文）。
 */
function applyPwaLocale(): void {
  if (locale !== 'en') return
  const old = document.querySelector<HTMLLinkElement>('link[rel="manifest"]')
  if (old) {
    // 换一个新节点而不是改 href：已存在 link 的 href 被改掉时，浏览器不一定会重新解析清单
    const link = document.createElement('link')
    link.rel = 'manifest'
    link.href = './manifest.en.json'
    old.replaceWith(link)
  }
  const title = document.querySelector<HTMLMetaElement>(
    'meta[name="apple-mobile-web-app-title"]',
  )
  if (title) title.content = 'FarewellRSS'
}

applyPwaLocale()

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(i18n)

app.mount('#app')
