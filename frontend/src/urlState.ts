// 阅读器状态 ⇄ URL 双向同步（hash 路由）
//
// 层级只加在「条目」上 —— 这是阅读器里唯一真正的父子链（列表 → 条目）：
//   #/                                             列表态（流/抽屉/弹窗都在 query 里）
//   #/item/<hex>?stream=…&type=…&sidebar=1&modal=profile
// 「上一级」= 去掉 /item/<hex> 这一段。抽屉和弹窗是**浮层**、不是层级里的一级：
// 抽屉能从列表开、弹窗能从顶栏开（一个节点多个父），做成路径段必然出现跳级。
//
// query 参数（顺序固定 stream → type → sidebar → modal，都在 item 这一段之后）：
//   stream   当前流 id（等于默认「全部文章」时省略，保证列表态地址干净）
//   type     label 流的类型（folder=订阅分类 / tag=收藏夹），仅 label 流出现
//   sidebar  手机/平板抽屉是否打开（桌面侧栏常驻，不写该参数）
//   modal    打开的弹窗（profile / admin）
//
// 语义：**缺参数 = 关闭/默认**，所以浏览器前进后退就是「撤销上一步界面操作」。
//
// 写入策略（syncUrl → 三条规则，按顺序）：
//   1. 上一格历史条目**正好等于目标地址** → router.back()（弹一格）。
//      「界面上的返回/关闭」「系统返回手势」「浏览器后退」因此是同一个动作，既不会重放也不会留死格。
//   2. 否则若是**同级跳转**或**向上收一层** → replace（就地改，不堆栈）。
//      翻文章（下一篇）、换订阅源、关掉浮层之后回到列表都走这条 —— 否则同级跳转会一格一格
//      堆进历史，变成「返回到列表后再按后退又掉回条目里」。
//   3. 否则（层级变深 = 下钻，或**打开浮层**） → push。
//      下钻要能一级一级退回来；浮层要 push 才能被后退关掉。
//   replace 模式（按 URL 恢复期间的校正、断点变化、恢复失败兜底）跳过 1/2/3，直接就地改。
// 同一 tick 内的多处改动由 Vue 的 watcher 批处理合并成一次导航（关抽屉+换流=1 格）。

import { computed, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { useStreamStore } from '@/stores/stream'
import { useSubscriptionsStore } from '@/stores/subscriptions'
import {
  LABEL_PREFIX,
  STATE,
  entryHex,
  entryTagId,
  type LabelType,
} from '@/types/greader'

/** item 参数形态：后端 id 是 {entry.id:016x}，容错收 1~16 位 hex */
const ITEM_HEX_RE = /^[0-9a-fA-F]{1,16}$/

/** 条目态的路径（层级只加在 item 上；拿不到 hex 就是列表态） */
function itemPath(hex: string | null): string {
  return hex ? `/item/${hex}` : '/'
}

/** 路径层级深度：`/` = 0，`/item/<hex>` = 2。只用来判断「下钻 vs 同级/向上」 */
function pathDepth(path: string): number {
  return path.split('/').filter(Boolean).length
}

export type ReaderModal = 'profile' | 'admin'

export function useUrlState(opts: {
  sidebarDrawer: Ref<boolean>
  isCompact: Ref<boolean>
}) {
  const { sidebarDrawer, isCompact } = opts
  const route = useRoute()
  const router = useRouter()
  const stream = useStreamStore()
  const subs = useSubscriptionsStore()

  /** 打开中的弹窗（单一来源，再派生出两个 v-model:show） */
  const activeModal = ref<ReaderModal | null>(null)
  const profileShow = computed({
    get: () => activeModal.value === 'profile',
    set: (v: boolean) => {
      activeModal.value = v ? 'profile' : null
    },
  })
  const adminShow = computed({
    get: () => activeModal.value === 'admin',
    set: (v: boolean) => {
      activeModal.value = v ? 'admin' : null
    },
  })

  /** 正在按 URL 应用状态：期间不回写，避免自激 */
  let applying = 0

  function buildLocation(): { path: string; query: Record<string, string> } {
    const query: Record<string, string> = {}
    if (stream.currentStreamId !== STATE.readingList) {
      query.stream = stream.currentStreamId
    }
    if (stream.currentType) query.type = stream.currentType
    if (isCompact.value && sidebarDrawer.value) query.sidebar = '1'
    if (activeModal.value) query.modal = activeModal.value
    const hex = stream.currentItemId ? entryHex(stream.currentItemId) : null
    return { path: itemPath(hex), query }
  }

  function sameLocation(
    target: { path: string; query: Record<string, string> },
    current: { path: string; query: LocationQuery },
  ): boolean {
    if (target.path !== current.path) return false
    const keys = Object.keys(current.query).filter(
      (k) => current.query[k] !== undefined,
    )
    if (keys.length !== Object.keys(target.query).length) return false
    return keys.every((k) => current.query[k] === target.query[k])
  }

  /** vue-router 把上一格历史条目的 fullPath 放在 history.state.back（仅应用内条目） */
  function backFullPath(): string | null {
    const state = window.history.state as { back?: unknown } | null
    if (!state || typeof state.back !== 'string') return null
    // hash 模式下有的版本会带 `#` 前缀，统一去掉再比
    return state.back.replace(/^#/, '')
  }

  /** 状态 → URL。三条规则见文件头「写入策略」 */
  async function syncUrl(mode: 'auto' | 'replace'): Promise<void> {
    const target = buildLocation()
    if (sameLocation(target, route)) return
    if (mode === 'auto') {
      const back = backFullPath()
      if (back !== null && back === router.resolve(target).fullPath) {
        router.back()
        return
      }
      await navigate(target, shouldPush(target))
      return
    }
    await navigate(target, false)
  }

  /** 只有「下钻」和「打开浮层」才新增历史条目，其余同级/向上都就地替换 */
  function shouldPush(target: {
    path: string
    query: Record<string, string>
  }): boolean {
    if (!route.query.sidebar && !!target.query.sidebar) return true
    if (!route.query.modal && !!target.query.modal) return true
    return pathDepth(target.path) > pathDepth(route.path)
  }

  /** 导航；被守卫取消 / 重复导航都忽略 */
  async function navigate(
    target: { path: string; query: Record<string, string> },
    push: boolean,
  ): Promise<void> {
    try {
      if (push) await router.push(target)
      else await router.replace(target)
    } catch {
      // 忽略
    }
  }

  /** 按 URL 恢复状态（缺参数即关闭 / 默认） */
  async function applyFromUrl(): Promise<void> {
    applying++
    try {
      const q = route.query
      const streamId =
        typeof q.stream === 'string' ? q.stream : STATE.readingList
      const isLabel = streamId.startsWith(LABEL_PREFIX)
      const rawType = typeof q.type === 'string' ? q.type : undefined
      const streamType: LabelType | undefined =
        isLabel && (rawType === 'folder' || rawType === 'tag')
          ? rawType
          : undefined
      // 注意 !stream.loaded：store 的 currentStreamId 初值就是「全部文章」，
      // 只比值会把首屏加载整个跳过（表现为列表空着）
      if (
        streamId !== stream.currentStreamId ||
        streamType !== stream.currentType ||
        !stream.loaded
      ) {
        await stream.loadStream(streamId, streamType)
      }

      const rawHex = route.params.hex
      const itemHex =
        typeof rawHex === 'string' && ITEM_HEX_RE.test(rawHex) ? rawHex : null
      if (itemHex) {
        const itemId = entryTagId(itemHex)
        if (stream.currentItemId !== itemId) await stream.openItemById(itemId)
      } else if (stream.currentItemId) {
        stream.closeItem()
      }

      sidebarDrawer.value = isCompact.value && q.sidebar === '1'
      activeModal.value =
        q.modal === 'profile' || q.modal === 'admin' ? q.modal : null
    } finally {
      applying--
      // 恢复失败（例如文章已不存在）时把 URL 拉回真实状态，不动历史栈
      if (applying === 0) await syncUrl('replace')
    }
  }

  /**
   * 引导修正：URL 里 label 流没带 type 时，等订阅列表到位后按分类补齐。
   * 走 replace，所以启动时不会多出历史条目。
   */
  async function refineLabelType(): Promise<void> {
    const id = stream.currentStreamId
    if (stream.currentType !== undefined || !id.startsWith(LABEL_PREFIX)) return
    const derived: LabelType | undefined = subs.starFolderIds.has(id)
      ? 'tag'
      : subs.folders.some((f) => f.id === id)
        ? 'folder'
        : undefined
    if (!derived) return
    applying++
    try {
      await stream.loadStream(id, derived)
    } finally {
      applying--
      if (applying === 0) await syncUrl('replace')
    }
  }

  /**
   * 兼容旧的「无 hash + query」链接（`/?stream=…&item=…`）：hash 路由下 pathname 上的
   * query 不会被路由读取，这里把它搬进 hash 再走一次（搬过一次之后不再出现这种形态）。
   */
  async function migrateLegacyQuery(): Promise<void> {
    const hash = window.location.hash
    // 注意：hash 路由启动时会把空 hash 先补成 "#/"，所以 "#/" 也要当作「没有层级信息」
    if ((hash && hash !== '#/') || !window.location.search) return
    const legacy = new URLSearchParams(window.location.search)
    const query: Record<string, string> = {}
    for (const key of ['stream', 'type', 'sidebar', 'modal']) {
      const value = legacy.get(key)
      if (value) query[key] = value
    }
    const rawItem = legacy.get('item') ?? ''
    const hex = ITEM_HEX_RE.test(rawItem) ? rawItem : null
    try {
      await router.replace({ path: itemPath(hex), query })      // 顺手把 pathname 上残留的旧 query 抹掉，只留 hash
      // （router 自己的 location 只看 hash 部分，所以这样改不影响它的 state/back/forward）
      window.history.replaceState(
        window.history.state,
        '',
        window.location.pathname + window.location.hash,
      )    } catch {
      // 忽略导航异常，交给后续 applyFromUrl 处罾
    }
  }

  // 状态变化 → URL（同一 tick 内的多处改动合并为一次导航）
  watch(
    [
      () => stream.currentStreamId,
      () => stream.currentType,
      () => stream.currentItemId,
      sidebarDrawer,
      activeModal,
    ],
    () => {
      if (applying === 0) void syncUrl('auto')
    },
  )

  // 断点变化不动历史栈，只把 sidebar 参数校正掉（桌面档不带该参数）
  watch(isCompact, () => {
    if (applying === 0) void syncUrl('replace')
  })

  // 地址栏变化（前进/后退 / 手改 hash）→ 状态。fullPath 同时覆盖路径与 query
  watch(
    () => route.fullPath,
    () => {
      void applyFromUrl()
    },
  )

  /** 弹窗开关（菜单等 UI 入口用，写进 URL 后由 watch 同步） */
  function openModal(which: ReaderModal): void {
    activeModal.value = which
  }

  return {
    applyFromUrl,
    migrateLegacyQuery,
    refineLabelType,
    openModal,
    profileShow,
    adminShow,
  }
}
