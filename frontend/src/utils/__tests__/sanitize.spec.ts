import { describe, expect, it } from 'vitest'

import {
  createEmbedConfirm,
  createEmbedFrame,
  playableUrl,
  sanitizeArticleHtml,
} from '../sanitize'

const labels = { load: '点击加载内嵌内容', open: '在新窗口打开' }

/** RSSHub 现在发的那个：B 站手机端页面，桌面浏览器里只有一片黑 */
const BILIBILI_MOBILE =
  'https://www.bilibili.com/blackboard/html5mobileplayer.html?aid=1&cid=undefined&bvid=BV1'
/** 手机端地址修复后（也是直接接受的）官方嵌入播放器 */
const BILIBILI_LEGACY = 'https://player.bilibili.com/player.html?aid=1&bvid=BV1&page=1'

function sanitize(html: string): string {
  return sanitizeArticleHtml(html, labels)
}

describe('sanitizeArticleHtml', () => {
  describe('内嵌播放器', () => {
    it('白名单里的 iframe 换成占位器，输出里不再有 iframe', () => {
      const result = sanitize(
        `<p>前</p><iframe width="640" height="360" ` +
          `src="https://www.bilibili.com/blackboard/html5mobileplayer.html?aid=1&amp;cid=2&amp;bvid=BV1" ` +
          `frameborder="0" allowfullscreen></iframe><p>后</p>`,
      )

      expect(result).not.toContain('<iframe')
      expect(result).toContain('class="embed-load"')
      // 地址原样用，一个参数都不动
      expect(result).toContain(
        'data-embed="https://www.bilibili.com/blackboard/html5mobileplayer.html?aid=1&amp;cid=2&amp;bvid=BV1"',
      )
      // 宽高比留着，插入时才能让高度跟着宽度走
      expect(result).toContain('data-embed-ratio="640 / 360"')
      // 显示的域名取自地址本身
      expect(result).toContain('www.bilibili.com')
      // 前后的正文不能丢
      expect(result).toContain('前')
      expect(result).toContain('后')
    })

    it('源站给的地址原样使用，不做播放器改写（包括 cid=undefined 这种假参数）', () => {
      const result = sanitize(`<iframe src="${BILIBILI_MOBILE}"></iframe>`)

      expect(result).not.toContain('<iframe')
      expect(result).toContain(
        'data-embed="https://www.bilibili.com/blackboard/html5mobileplayer.html?aid=1&amp;cid=undefined&amp;bvid=BV1"',
      )
      // 曾经会改写成 player.bilibili.com/player.html，依据是带插件跑出来的对照实验，已删
      expect(result).not.toContain('player.bilibili.com')
    })

    it('协议相对的写法按 https 解析；显式的 http 原样保留（不知道对面支不支持 https）', () => {
      const result = sanitize(
        '<iframe src="//player.bilibili.com/player.html?aid=9"></iframe>' +
          '<iframe src="http://player.bilibili.com/player.html?aid=8"></iframe>',
      )

      expect(result).toContain('data-embed="https://player.bilibili.com/player.html?aid=9"')
      expect(result).toContain('data-embed="http://player.bilibili.com/player.html?aid=8"')
      expect(result).not.toContain('<iframe')
    })

    it('不在任何名单里的域名同样有占位器：不再静默丢掉源里的东西', () => {
      const result = sanitize('<p>前</p><iframe src="https://some-blog.example/widget"></iframe>')

      expect(result).toContain('class="embed-load"')
      expect(result).toContain('data-embed="https://some-blog.example/widget"')
      expect(result).toContain('some-blog.example')
      expect(result).not.toContain('<iframe')
      expect(result).toContain('前')
    })

    it('占位器上显示的域名取自地址本身', () => {
      const result = sanitize(
        '<iframe src="https://www.bilibili.com.evil.example/blackboard/html5mobileplayer.html"></iframe>',
      )

      // 伪装成 bilibili 的子域名？显示的就是它真实的主机名，没有修复可走
      expect(result).toContain('www.bilibili.com.evil.example')
      expect(result).toContain('class="embed-load"')
    })

    it('javascript: / data: / srcdoc 的 src 什么都不生成', () => {
      for (const src of ['javascript:alert(1)', 'data:text/html,<b>x</b>', '']) {
        const result = sanitize(`<iframe src="${src}"></iframe>`)
        expect(result).not.toContain('iframe')
        expect(result).not.toContain('embed-load')
      }

      const srcdoc = sanitize(
        '<iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></iframe>',
      )
      expect(srcdoc).not.toContain('iframe')
      expect(srcdoc).not.toContain('embed-load')
    })

    it('带凭据的地址不算数（占位器会把域名显示给人看）', () => {
      expect(playableUrl('https://user:pass@player.bilibili.com/player.html')).toBeNull()
    })

    it('隐藏的 iframe 直接删掉，不留一行噪音', () => {
      const hidden = [
        '<iframe src="https://player.bilibili.com/player.html" width="0" height="0"></iframe>',
        '<iframe src="https://player.bilibili.com/player.html" style="display:none;visibility:hidden"></iframe>',
        '<iframe src="https://player.bilibili.com/player.html" hidden></iframe>',
      ]

      for (const html of hidden) {
        const result = sanitize(html)
        expect(result).not.toContain('iframe')
        expect(result).not.toContain('embed-load')
      }
    })
  })

  describe('样式与表单', () => {
    it('<style> 标签删掉，连 CSS 文本都不留（它本来能改我们整篇页面）', () => {
      const result = sanitize('<style>body{display:none}</style><p>正文</p>')

      expect(result).not.toContain('<style')
      expect(result).not.toContain('display:none')
      expect(result).not.toContain('body{')
      expect(result).toContain('正文')
    })

    it('源站给自己元素加的 display:none 留着：那是它对自己内容的排版，不是我们的问题', () => {
      expect(sanitize('<p style="display:none">x</p>')).toContain('display:none')
    })

    it('只剥会跑出正文框的定位属性，别的 inline style 保留', () => {
      const result = sanitize(
        '<p style="position:fixed;top:0;z-index:99;color:red;margin-left:12px">x</p>',
      )

      expect(result).not.toContain('position')
      expect(result).not.toContain('z-index')
      expect(result).not.toContain('top:')
      expect(result).toContain('color:red')
      expect(result).toContain('margin-left:12px')
    })

    it('表单元素删掉：不在阅读器里给假登录页留位置', () => {
      const result = sanitize(
        '<form action="https://evil.example/login"><input name="p"><button>登录</button></form><p>正文</p>',
      )

      expect(result).not.toContain('<form')
      expect(result).not.toContain('<input')
      expect(result).not.toContain('<button')
      expect(result).toContain('正文')
    })
  })

  describe('经典 XSS 回归', () => {
    it('脚本、事件属性、javascript: 链接照旧被清掉', () => {
      const result = sanitize(
        '<script>alert(1)</script><p onclick="alert(2)">x</p><a href="javascript:alert(3)">y</a>',
      )

      expect(result).not.toContain('alert')
      expect(result).not.toContain('onclick')
      expect(result).not.toContain('javascript:')
    })

    it('正常正文原样保留', () => {
      const html =
        '<p>文字</p><img src="https://example.com/a.png" alt="图"><a href="https://example.com">链接</a>'
      const result = sanitize(html)

      expect(result).toContain('<p>文字</p>')
      expect(result).toContain('src="https://example.com/a.png"')
      expect(result).toContain('alt="图"')
      expect(result).toContain('href="https://example.com"')
    })

    it('图片一律不带 Referer：B 站图床对第三方 Referer 直接 403', () => {
      expect(sanitize('<img src="https://i0.hdslb.com/a.jpg">')).toContain(
        'referrerpolicy="no-referrer"',
      )
    })

    it('源站也别想改这个属性去漏我们的地址', () => {
      const result = sanitize('<img src="https://example.com/a.png" referrerpolicy="unsafe-url">')

      expect(result).toContain('referrerpolicy="no-referrer"')
      expect(result).not.toContain('unsafe-url')
    })

    it('只动图片，链接的 Referer 行为不碰', () => {
      const result = sanitize('<a href="https://example.com">链接</a>')

      expect(result).not.toContain('referrerpolicy')
    })

    it('空输入返回空串', () => {
      expect(sanitize('')).toBe('')
    })
  })
})

describe('createEmbedFrame', () => {
  it('只有地址不合法才返回 null；域名不再设限', () => {
    expect(createEmbedFrame('https://some-blog.example/widget', '16 / 9')?.src).toBe(
      'https://some-blog.example/widget',
    )
    expect(createEmbedFrame('javascript:alert(1)', '16 / 9')).toBeNull()
    expect(createEmbedFrame('', '16 / 9')).toBeNull()
  })

  it('构造出我们自己的 iframe：宽高比 + 不送 Referer + 允许全屏', () => {
    const frame = createEmbedFrame(BILIBILI_LEGACY, '640 / 360')

    expect(frame).not.toBeNull()
    expect(frame?.src).toBe(BILIBILI_LEGACY)
    expect(frame?.getAttribute('allowfullscreen')).toBe('true')
    // 自部署实例的域名外面没人认得，送过去既换不来信任又白白漏身份
    expect(frame?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(frame?.style.aspectRatio).toBe('640 / 360')
    expect(frame?.style.width).toBe('100%')
  })

  it('点击插入时也用源站给的地址（不再有播放器改写）', () => {
    expect(createEmbedFrame(BILIBILI_MOBILE, '16 / 9')?.src).toBe(BILIBILI_MOBILE)
  })

  it('宽高比不可信或格式不对时退回 16:9', () => {
    expect(createEmbedFrame(BILIBILI_LEGACY, 'javascript:alert(1)')?.style.aspectRatio).toBe(
      '16 / 9',
    )
    expect(createEmbedFrame(BILIBILI_LEGACY, '')?.style.aspectRatio).toBe('16 / 9')
  })
})

describe('createEmbedConfirm', () => {
  it('确认行用调用方算好的文案与校验过的地址', () => {
    const confirm = createEmbedConfirm(BILIBILI_LEGACY, '640 / 360', {
      message: '将从 player.bilibili.com 加载第三方内容',
      allow: '允许并加载',
      cancel: '取消',
    })

    expect(confirm.className).toContain('embed-confirm')
    expect(confirm.dataset.embed).toBe(BILIBILI_LEGACY)
    expect(confirm.dataset.embedRatio).toBe('640 / 360')
    expect(confirm.textContent).toContain('将从 player.bilibili.com 加载第三方内容')
    expect(confirm.querySelector('.embed-allow')?.textContent).toBe('允许并加载')
    expect(confirm.querySelector('.embed-cancel')?.textContent).toBe('取消')
  })
})
