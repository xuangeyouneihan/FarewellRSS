import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as greader from '@/api/greader'
import { useSubscriptionsStore } from '@/stores/subscriptions'
import {
  STATE,
  isRead,
  isStarred,
  itemTagName,
  encodeContinuation,
  parseEntryId,
  type Item,
  type LabelType,
  type StreamParams,
} from '@/types/greader'

export type SortOrder = 'n' | 'o' // n=最新在前 o=最旧在前

export const useStreamStore = defineStore('stream', () => {
  // 当前流
  const currentStreamId = ref('user/-/state/com.google/reading-list')
  const currentType = ref<LabelType | undefined>(undefined)

  // 文章列表（无限滚动累积）
  const items = ref<Item[]>([])
  const continuation = ref<string | undefined>(undefined)
  const hasMore = ref(true)
  const loadingMore = ref(false)
  const loaded = ref(false)
  // 最近一次加载是否失败（区分「空流」与「加载出错」）
  const loadError = ref(false)

  // 排序
  const sortOrder = ref<SortOrder>('n')

  // 当前打开的文章
  const currentItemId = ref<string | null>(null)
  // 当前文章的独立缓存（切流/刷新后仍保留，直到主动关闭）
  const currentItemData = ref<Item | null>(null)

  // 新内容横幅
  const hasNewItems = ref(false)
  // 向上加载更旧（仅最旧在前使用）
  const hasOlder = ref(false)
  const loadingOlder = ref(false)

  async function loadStream(streamId: string, type?: LabelType): Promise<void> {
    currentStreamId.value = streamId
    currentType.value = type
    items.value = []
    continuation.value = undefined
    hasMore.value = true
    loaded.value = false
    loadError.value = false
    hasNewItems.value = false
    hasOlder.value = false
    loadingOlder.value = false
    await loadMore()
  }

  async function loadMore(): Promise<void> {
    if (loadingMore.value || !hasMore.value) return
    loadingMore.value = true
    try {
      const params: StreamParams = {
        n: 20,
        r: sortOrder.value,
        type: currentType.value,
      }
      if (continuation.value) params.c = continuation.value
      const data = await greader.getStreamContents(currentStreamId.value, params)
      items.value.push(...data.items)
      continuation.value = data.continuation
      hasMore.value = data.continuation !== undefined
      loaded.value = true
      loadError.value = false
    } catch (e) {
      loadError.value = true
      throw e
    } finally {
      loadingMore.value = false
    }
  }

  async function setSortOrder(order: SortOrder): Promise<void> {
    if (sortOrder.value === order) return
    sortOrder.value = order
    await loadStream(currentStreamId.value, currentType.value)
  }

  /** 当前视图是否轮询（全部文章 / 订阅源 / 文件夹） */
  function shouldPoll(): boolean {
    if (currentStreamId.value === STATE.readingList) return true
    if (currentStreamId.value.startsWith('feed/')) return true
    return currentType.value === 'folder'
  }

  /** 轮询检测是否有新内容 */
  async function pollLatest(): Promise<void> {
    if (!shouldPoll()) return
    try {
      const data = await greader.getStreamContents(currentStreamId.value, {
        n: 1,
        r: 'n',
        type: currentType.value,
      })
      const latest = data.items[0]
      if (!latest) return
      const newestInList = items.value.reduce<Item | undefined>((max, it) => {
        if (!max) return it
        if (it.published !== max.published) {
          return it.published > max.published ? it : max
        }
        return it.id > max.id ? it : max
      }, undefined)
      if (!newestInList || latest.id !== newestInList.id) {
        hasNewItems.value = true
      }
    } catch {
      // 轮询失败静默忽略
    }
  }

  /** 向上加载更旧（仅最旧在前）：r='n' 反转后 prepend */
  async function loadNewer(): Promise<void> {
    if (loadingOlder.value || !hasOlder.value) return
    const top = items.value[0]
    if (!top) return
    loadingOlder.value = true
    try {
      const c = encodeContinuation(top.published, parseEntryId(top.id))
      const data = await greader.getStreamContents(currentStreamId.value, {
        n: 20,
        r: 'n',
        type: currentType.value,
        c,
      })
      if (data.items.length > 0) {
        items.value = [...data.items].reverse().concat(items.value)
      }
      hasOlder.value = data.continuation !== undefined
    } finally {
      loadingOlder.value = false
    }
  }

  /** 最旧在前点横幅：r='n' 抓最新 20 条反转替换列表 */
  async function loadLatest(): Promise<void> {
    const data = await greader.getStreamContents(currentStreamId.value, {
      n: 20,
      r: 'n',
      type: currentType.value,
    })
    items.value = [...data.items].reverse()
    continuation.value = undefined
    hasMore.value = false
    hasOlder.value = data.continuation !== undefined
  }

  /** 点横幅统一入口：最新在前整表刷新，最旧在前跳到最新 */
  async function applyBubble(): Promise<void> {
    hasNewItems.value = false
    if (sortOrder.value === 'n') {
      await loadStream(currentStreamId.value, currentType.value)
    } else {
      await loadLatest()
    }
  }

  const currentItem = () => currentItemData.value

  /** 按 id 查找条目：先查当前列表，再回退到跨流缓存 */
  function findItem(itemId: string): Item | undefined {
    const item = items.value.find((it) => it.id === itemId)
    if (item) return item
    return currentItemData.value?.id === itemId
      ? currentItemData.value
      : undefined
  }

  /** 打开文章（缓存完整条目，切流/刷新后仍保留） */
  function openItem(item: Item): void {
    currentItemId.value = item.id
    currentItemData.value = item
  }

  /** 关闭文章 */
  function closeItem(): void {
    currentItemId.value = null
    currentItemData.value = null
  }

  /** 本地更新某条目的 categories（乐观更新），同步当前文章缓存 */
  function updateItemCategories(
    itemId: string,
    add: string[],
    remove: string[],
  ): void {
    const removed = new Set(remove)
    const apply = (item: Item): void => {
      item.categories = [
        ...item.categories.filter((c) => !removed.has(c)),
        ...add,
      ]
    }
    const item = items.value.find((it) => it.id === itemId)
    if (item && item !== currentItemData.value) apply(item)
    if (currentItemData.value?.id === itemId) apply(currentItemData.value)
  }

  /** 标已读 */
  async function markRead(itemId: string): Promise<void> {
    await greader.editTag([itemId], [STATE.read])
    updateItemCategories(itemId, [STATE.read], [])
    await useSubscriptionsStore().fetchUnreadCounts()
  }

  /** 切换已读 / 未读 */
  async function toggleRead(itemId: string): Promise<void> {
    const item = findItem(itemId)
    if (!item) return
    if (isRead(item)) {
      await greader.editTag([itemId], [], [STATE.read])
      updateItemCategories(itemId, [], [STATE.read])
    } else {
      await greader.editTag([itemId], [STATE.read])
      updateItemCategories(itemId, [STATE.read], [])
    }
    await useSubscriptionsStore().fetchUnreadCounts()
  }

  /** 收藏 / 取消收藏 */
  async function toggleStar(itemId: string): Promise<void> {
    const item = findItem(itemId)
    if (!item) return
    const subs = useSubscriptionsStore()
    const wasStarred = isStarred(item)
    // 取消收藏前，记录条目所在的收藏夹（用于判断是否需要刷新当前流）
    const tagName = wasStarred ? itemTagName(item, subs.starFolderIds) : null
    if (wasStarred) {
      await greader.editTag([itemId], [], [STATE.starred])
      updateItemCategories(itemId, [], [STATE.starred])
    } else {
      await greader.editTag([itemId], [STATE.starred])
      updateItemCategories(itemId, [STATE.starred], [])
    }
    await subs.fetchUnreadCounts()
    // 取消收藏后：当前视图是「已收藏」或该条目所在的收藏夹时，刷新当前流
    if (wasStarred) {
      const inStarredView = currentStreamId.value === STATE.starred
      const inTagView =
        tagName !== null &&
        currentStreamId.value === `user/-/label/${tagName}`
      if (inStarredView || inTagView) {
        await loadStream(currentStreamId.value, currentType.value)
      }
    }
  }

  /** 设置条目的收藏夹 tag；tagName 为 null 表示「纯收藏（无分类）」 */
  async function setItemTag(
    itemId: string,
    tagName: string | null,
    tagIds: ReadonlySet<string>,
  ): Promise<void> {
    const item = findItem(itemId)
    if (!item) return
    const current = itemTagName(item, tagIds)
    // 先移除旧 tag（若有且不同）
    if (current && current !== tagName) {
      await greader.editTag([itemId], [], [`user/-/label/${current}`])
      updateItemCategories(itemId, [], [`user/-/label/${current}`])
    }
    if (tagName && current !== tagName) {
      await greader.editTag([itemId], [`user/-/label/${tagName}`])
      updateItemCategories(itemId, [`user/-/label/${tagName}`], [])
    }
    await useSubscriptionsStore().fetchUnreadCounts()
  }

  /** 标签/文件夹重命名后，同步更新已加载条目的 categories */
  function renameItemLabel(oldName: string, newName: string): void {
    const oldId = `user/-/label/${oldName}`
    const newId = `user/-/label/${newName}`
    const rename = (item: Item): void => {
      item.categories = item.categories.map((c) => (c === oldId ? newId : c))
    }
    for (const item of items.value) rename(item)
    if (currentItemData.value) rename(currentItemData.value)
  }

  return {
    currentStreamId,
    currentType,
    items,
    continuation,
    hasMore,
    loadingMore,
    loaded,
    loadError,
    sortOrder,
    currentItemId,
    hasNewItems,
    hasOlder,
    loadingOlder,
    loadStream,
    loadMore,
    setSortOrder,
    shouldPoll,
    pollLatest,
    loadNewer,
    loadLatest,
    applyBubble,
    currentItem,
    findItem,
    openItem,
    closeItem,
    markRead,
    toggleRead,
    toggleStar,
    setItemTag,
    renameItemLabel,
  }
})
