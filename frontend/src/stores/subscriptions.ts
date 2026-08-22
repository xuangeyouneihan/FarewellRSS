import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as greader from '@/api/greader'
import {
  labelName,
  type LabelType,
  type Subscription,
  type TagItem,
} from '@/types/greader'

export const useSubscriptionsStore = defineStore('subscriptions', () => {
  const subscriptions = ref<Subscription[]>([])
  const tags = ref<TagItem[]>([])
  // 流 ID → 未读数
  const unreadCounts = ref<Record<string, number>>({})

  // 文件夹（归类订阅源）
  const folders = computed(() => tags.value.filter((t) => t.type === 'folder'))
  // 收藏夹（归类收藏条目）
  const starFolders = computed(() => tags.value.filter((t) => t.type === 'tag'))
  // 收藏夹 ID 集合（用于区分条目的 folder/tag label）
  const starFolderIds = computed(
    () => new Set(starFolders.value.map((t) => t.id)),
  )

  async function fetchSubscriptions(): Promise<void> {
    const data = await greader.getSubscriptions()
    subscriptions.value = data.subscriptions
  }

  async function fetchTags(): Promise<void> {
    const data = await greader.getTags()
    tags.value = data.tags
  }

  async function fetchUnreadCounts(): Promise<void> {
    const data = await greader.getUnreadCount()
    const map: Record<string, number> = {}
    for (const entry of data.unreadcounts) {
      map[entry.id] = entry.count
    }
    unreadCounts.value = map
  }

  async function refresh(): Promise<void> {
    await Promise.all([
      fetchSubscriptions(),
      fetchTags(),
      fetchUnreadCounts(),
    ])
  }

  /** 添加订阅并刷新订阅列表 */
  async function addSubscription(feedUrl: string): Promise<string> {
    const result = await greader.quickAdd(feedUrl)
    await refresh()
    return result.streamId
  }

  /** 导入 OPML 并刷新订阅、分类和未读数 */
  async function importOpml(file: File): Promise<void> {
    await greader.importOpml(file)
    await refresh()
  }

  /** 新建标签（收藏夹）或文件夹 */
  async function createLabel(name: string, type: LabelType): Promise<void> {
    await greader.enableTag([name], [type])
    await fetchTags()
  }

  /** 重命名文件夹 / 收藏夹 */
  async function renameLabel(
    id: string,
    newName: string,
    type: LabelType,
  ): Promise<void> {
    await greader.renameTag([labelName(id)], [newName], [type])
    await refresh()
  }

  /** 删除文件夹 / 收藏夹 */
  async function deleteLabel(id: string, type: LabelType): Promise<void> {
    await greader.disableTag([labelName(id)], [type])
    await refresh()
  }

  /** 退订 */
  async function unsubscribe(streamId: string): Promise<void> {
    await greader.editSubscription('unsubscribe', [streamId])
    await refresh()
  }

  /** 归类到文件夹；folderName 为 null 表示取消归类 */
  async function moveSubscriptionToFolder(
    streamId: string,
    folderName: string | null,
  ): Promise<void> {
    const sub = subscriptions.value.find((s) => s.id === streamId)
    const currentFolder = sub?.categories[0]?.label ?? null
    if (folderName) {
      await greader.editSubscription('edit', [streamId], {
        addFolder: folderName,
      })
    } else if (currentFolder) {
      await greader.editSubscription('edit', [streamId], {
        removeFolder: currentFolder,
      })
    }
    await refresh()
  }

  return {
    subscriptions,
    tags,
    unreadCounts,
    folders,
    starFolders,
    starFolderIds,
    fetchSubscriptions,
    fetchTags,
    fetchUnreadCounts,
    refresh,
    addSubscription,
    importOpml,
    createLabel,
    renameLabel,
    deleteLabel,
    unsubscribe,
    moveSubscriptionToFolder,
  }
})
