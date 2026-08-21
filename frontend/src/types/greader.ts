// Google Reader API 的 TypeScript 类型定义

export type LabelType = 'folder' | 'tag'

/** 系统状态标签 ID 常量 */
export const STATE = {
  readingList: 'user/-/state/com.google/reading-list',
  read: 'user/-/state/com.google/read',
  starred: 'user/-/state/com.google/starred',
  uncategorized: 'user/-/state/farewell-rss/starred-uncategorized',
} as const

/** label/{name} 前缀 */
export const LABEL_PREFIX = 'user/-/label/'

/** 从 label ID 提取名称 */
export function labelName(id: string): string {
  return id.startsWith(LABEL_PREFIX) ? id.slice(LABEL_PREFIX.length) : id
}

/** 从 item id（tag:...item/{hex}）解析出条目自增 id */
export function parseEntryId(itemId: string): number {
  return parseInt(itemId.split('/').pop() ?? '0', 16)
}

/** 构造 continuation：16 位 hex 时间戳 + 16 位 hex id（与后端 _encode_continuation 对齐） */
export function encodeContinuation(published: number, entryId: number): string {
  return (
    published.toString(16).padStart(16, '0') +
    entryId.toString(16).padStart(16, '0')
  )
}

/** 判断条目是否已读 */
export function isRead(item: Pick<Item, 'categories'>): boolean {
  return item.categories.includes(STATE.read)
}

/** 判断条目是否已收藏 */
export function isStarred(item: Pick<Item, 'categories'>): boolean {
  return item.categories.includes(STATE.starred)
}

/** 条目的收藏夹 tag 名称（无则返回 null）；tagIds 直接传 Set，避免重复构造 */
export function itemTagName(
  item: Pick<Item, 'categories'>,
  tagIds: ReadonlySet<string>,
): string | null {
  for (const c of item.categories) {
    if (c.startsWith(LABEL_PREFIX) && tagIds.has(c)) {
      return labelName(c)
    }
  }
  return null
}

/** 订阅（subscription/list 的元素） */
export interface Subscription {
  id: string // "feed/3"
  title: string
  categories: { id: string; label: string }[]
  url: string
  htmlUrl: string
  iconUrl: string
}

/** tag/list 的标签项（系统标签无 type） */
export interface TagItem {
  id: string
  type?: LabelType
}

/** 文章条目（stream/contents 的 item） */
export interface Item {
  id: string
  crawlTimeMsec: string
  timestampUsec: string
  published: number
  updated: number
  title: string
  canonical: { href: string }[]
  alternate: { href: string; type: string }[]
  categories: string[]
  origin: {
    streamId: string
    title: string
    htmlUrl: string
  }
  summary: { content: string }
  author: string | null
}

export interface StreamContents {
  id: string
  updated: number
  items: Item[]
  continuation?: string
}

export interface ItemRefs {
  itemRefs: { id: string }[]
  continuation?: string
}

export interface UnreadCountEntry {
  id: string
  count: number
  newestItemTimestampUsec: string
}

export interface UnreadCount {
  max: number
  unreadcounts: UnreadCountEntry[]
}

export interface SubscriptionList {
  subscriptions: Subscription[]
}

/** subscription/quickadd 的返回值 */
export interface QuickAddResult {
  numResults: number
  streamId: string
  streamName: string
}

export interface TagList {
  tags: TagItem[]
}

export interface UserInfo {
  userId: string
  userName: string | null
  userProfileId: string
  userEmail: string
  isAdmin: boolean
}

/** ListUsers 返回的用户条目 */
export interface UserEntry {
  username: string
  friendlyName: string | null
  isAdmin: boolean
}

/** stream/contents 的查询参数 */
export interface StreamParams {
  n?: number
  r?: 'n' | 'd' | 'o'
  ot?: number
  nt?: number
  c?: string
  xt?: string
  it?: string
  type?: LabelType
}
