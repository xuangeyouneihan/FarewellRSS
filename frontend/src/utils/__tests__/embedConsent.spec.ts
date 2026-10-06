import { beforeEach, describe, expect, it, vi } from 'vitest'

import { hasEmbedConsent, rememberEmbedConsent } from '../embedConsent'

const STORAGE_KEY = 'farewell-rss.embed-consent'

describe('内嵌内容的来源同意', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  it('没同意过就是没同意过，空 id 永远不算', () => {
    expect(hasEmbedConsent('feed/1')).toBe(false)
    expect(hasEmbedConsent('')).toBe(false)

    rememberEmbedConsent('feed/1')

    expect(hasEmbedConsent('feed/1')).toBe(true)
    // 按源记，不是按站点全局开关
    expect(hasEmbedConsent('feed/2')).toBe(false)
  })

  it('空 id 记不进去（避免把空串当成一个源）', () => {
    rememberEmbedConsent('')

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('重复同意只会把它挪到最新', () => {
    rememberEmbedConsent('feed/1')
    rememberEmbedConsent('feed/2')
    rememberEmbedConsent('feed/1')

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual(['feed/2', 'feed/1'])
  })

  it('脏数据当作没同意过，不抛异常', () => {
    for (const dirty of ['不是 JSON', '{"feed/1":true}', '[1,2,3]', 'null']) {
      localStorage.setItem(STORAGE_KEY, dirty)
      expect(hasEmbedConsent('feed/1')).toBe(false)
    }
  })

  it('记满上限就丢最旧的：这是偏好缓存，不是数据库', () => {
    for (let i = 0; i < 205; i += 1) rememberEmbedConsent(`feed/${i}`)

    const remembered = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') as string[]
    expect(remembered).toHaveLength(200)
    expect(remembered).not.toContain('feed/0')
    expect(remembered).toContain('feed/204')
  })

  it('storage 写不进去（隐私模式/配额满）也不能把阅读器搞崩', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })

    expect(() => rememberEmbedConsent('feed/1')).not.toThrow()
    expect(hasEmbedConsent('feed/1')).toBe(false)

    vi.restoreAllMocks()
  })
})
