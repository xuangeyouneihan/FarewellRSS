import { afterEach, describe, expect, it } from 'vitest'

import { mount } from '@vue/test-utils'

import App from '../App.vue'

/** App.vue 的职责：跟随系统深浅色，把 Naive UI 主题变量摊成全局 CSS 变量 */
describe('App', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('style')
    document.body.removeAttribute('style')
  })

  it('挂载后把主题变量注入 html 与 body', () => {
    mount(App, { global: { stubs: { RouterView: true } } })

    const html = document.documentElement
    expect(html.style.getPropertyValue('--app-text-1')).not.toBe('')
    expect(html.style.getPropertyValue('--app-primary')).not.toBe('')
    expect(html.style.getPropertyValue('--app-border')).not.toBe('')
    expect(html.style.colorScheme).toMatch(/^(light|dark)$/)
    expect(document.body.style.background).not.toBe('')
    expect(document.body.style.color).not.toBe('')
  })

  it('只做外壳，路由视图交给外层路由插件渲染', () => {
    const wrapper = mount(App, { global: { stubs: { RouterView: true } } })

    expect(wrapper.findComponent({ name: 'RouterView' }).exists()).toBe(true)
  })
})
