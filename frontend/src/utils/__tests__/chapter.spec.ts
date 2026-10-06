import { describe, expect, it } from 'vitest'

import { chapterSeconds, isBareFragment } from '../chapter'

describe('chapterSeconds', () => {
  it('认入库后被 feedparser 补成绝对地址的章节链接（这是线上真实形态）', () => {
    expect(chapterSeconds('https://avocadotoast.typlog.io/feed/audio.xml#t=00:05:18')).toBe(318)
  })

  it('裸片段、纯秒数、小数都认', () => {
    expect(chapterSeconds('#t=00:05:18')).toBe(318)
    expect(chapterSeconds('#t=318')).toBe(318)
    expect(chapterSeconds('#t=1:02:03')).toBe(3723)
    expect(chapterSeconds('#t=00:05:18.5')).toBe(318.5)
  })

  it('不是章节链接 → null（普通链接不能被拦）', () => {
    expect(chapterSeconds('https://e.com/post/1')).toBeNull()
    expect(chapterSeconds('https://e.com/post/1#section-2')).toBeNull()
    expect(chapterSeconds('')).toBeNull()
    expect(chapterSeconds('#')).toBeNull()
  })

  it('格式不对的时间点宁可不动，也不要跳到 0 秒', () => {
    expect(chapterSeconds('#t=')).toBeNull()
    expect(chapterSeconds('#t=abc')).toBeNull()
    expect(chapterSeconds('#t=1::2')).toBeNull()
    expect(chapterSeconds('#t=1:2:3:4')).toBeNull()
  })
})

describe('isBareFragment', () => {
  it('只认“就在本文档内跳”的锚点（hash 路由下它会打空白页）', () => {
    expect(isBareFragment('#foo')).toBe(true)
    expect(isBareFragment(' #foo ')).toBe(true)
    expect(isBareFragment('https://e.com/post/1#foo')).toBe(false)
    expect(isBareFragment('#')).toBe(false)
    expect(isBareFragment('')).toBe(false)
  })
})
