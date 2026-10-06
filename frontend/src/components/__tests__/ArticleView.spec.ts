import { beforeEach, describe, expect, it } from 'vitest'

import { mount } from '@vue/test-utils'
import { NMessageProvider } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'

import ArticleView from '../ArticleView.vue'
import { useStreamStore } from '@/stores/stream'
import type { Item } from '@/types/greader'

function makeItem(overrides: Partial<Item> = {}): Item {
  return {
    id: 'tag:google.com,2005:reader/item/1',
    crawlTimeMsec: '0',
    timestampUsec: '0',
    published: 0,
    updated: 0,
    title: '第一集：炒饭',
    canonical: [],
    alternate: [],
    categories: ['user/-/state/com.google/reading-list'],
    origin: { streamId: 'feed/1', title: '测试播客', htmlUrl: '' },
    summary: { content: '<p>本期聊炒饭</p>' },
    author: null,
    ...overrides,
  }
}

/**
 * 用 `h` 而不是模板字符串：测试环境里的 vue 是 runtime-only 构建，
 * 挂在组件上的 `template` 编译不了（ArticleView 自己走 SFC 编译，没问题）。
 * `NMessageProvider` 是必须的 —— ArticleView 里调了 `useMessage()`。
 */
function mountView(data: Item) {
  // 走 store 的公开入口，别直接 $patch：currentItemData 不在 store 的返回里
  // （`currentItem()` 是读它的唯一途径），$patch 一个不存在的键是静默无效的。
  useStreamStore().openItem(data)

  const Wrapper = defineComponent({
    setup: () => () => h(NMessageProvider, null, { default: () => h(ArticleView) }),
  })
  return mount(Wrapper)
}

/**
 * 后端拼在正文末尾的附件块（形状见 `src/farewell_rss/api/_enclosures.py`）。
 * 这几条测的是**它能不能活着穿过消毒**：标签被 DOMPurify 剥掉的话，
 * 播客在这些客户端里就只剩简介了。
 */
const SERVER_AUDIO =
  '<p>本期聊炒饭</p>' +
  '<figure class="enclosure"><p class="enclosure-content">' +
  '<audio preload="none" controls="controls" src="https://media.example.com/ep1.mp3"></audio>' +
  ' <a href="https://media.example.com/ep1.mp3" target="_blank" rel="noopener noreferrer">💾</a>' +
  '</p></figure>'

describe('ArticleView 正文里的附件块与章节链接', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('服务端拼的播放器能过消毒并渲染', () => {
    const wrapper = mountView(makeItem({ summary: { content: SERVER_AUDIO } }))

    const audio = wrapper.find('audio')
    expect(audio.exists()).toBe(true)
    expect(audio.attributes('src')).toBe('https://media.example.com/ep1.mp3')
    expect(audio.attributes('preload')).toBe('none')
    expect(audio.attributes('controls')).toBeDefined()
    expect(wrapper.find('figure.enclosure').exists()).toBe(true)
  })

  it('保存链接保留 target，图片附件也内联（并被强制不带 Referer）', () => {
    const wrapper = mountView(
      makeItem({
        summary: {
          content:
            SERVER_AUDIO +
            '<figure class="enclosure"><p class="enclosure-content">' +
            '<img src="https://e.com/cover.jpg" alt="" /></p></figure>',
        },
      }),
    )

    const save = wrapper.find('figure.enclosure a')
    expect(save.attributes('href')).toBe('https://media.example.com/ep1.mp3')
    expect(save.attributes('target')).toBe('_blank')

    const image = wrapper.find('figure.enclosure img')
    expect(image.attributes('src')).toBe('https://e.com/cover.jpg')
    expect(image.attributes('referrerpolicy')).toBe('no-referrer')
  })

  it('点章节链接不跳页，把播放器跳到那个时间点', async () => {
    const wrapper = mountView(
      makeItem({
        summary: {
          // href 是入库后的样子：feedparser 按源地址把 `#t=…` 补成了绝对地址
          content:
            SERVER_AUDIO +
            '<li><a href="https://x.com/feed/audio.xml#t=00:05:18">00:05:18</a> 发布</li>',
        },
      }),
    )

    const audio = wrapper.find('audio').element as HTMLAudioElement
    // jsdom 没实现 play()（规范里它返回 promise），给个假的：这里要测的是 seek，不是播放
    audio.play = () => Promise.resolve()

    await wrapper.find('li a').trigger('click')

    expect(audio.currentTime).toBe(318)
  })
})
