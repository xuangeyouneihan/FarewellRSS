<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { NVirtualList, useMessage, type VirtualListInst } from "naive-ui";
import { useStreamStore, type SortOrder } from "@/stores/stream";
import { isRead, type Item } from "@/types/greader";
import { t, locale } from "@/i18n";

const props = defineProps<{ fullWidth?: boolean; streamName?: string | null }>();
void props;

const stream = useStreamStore();
const message = useMessage();

/**
 * 每行的固定高度（像素）—— 虚拟滚动的前提。
 *
 * 行高等高，滚动位置的换算才是精确的：prepend 之后「视觉位置不变」就等于往下
 * 挪「新增条数 × 行高」，不用测量（测量既贵，又容易在连续 prepend 时漂）。
 * 为此标题限死两行（见 .item-title 的 line-clamp），一行一定放得下：
 * 12 上留白 + 标题两行(≈39) + 4 + 元信息(≈17) + 4 + 摘要(≈17) + 12 下留白 = 105，
 * 取 106。**改这个数字要同步改 CSS 里的行高约束**，两边对不上会出现空洞或重叠。
 */
const ITEM_HEIGHT = 106;

const virtualRef = ref<VirtualListInst | null>(null);
/** .list-body：新内容横幅的定位基准，同时用来挂捕获阶段的 scroll 监听 */
const wrapperRef = ref<HTMLElement | null>(null);
/** 最近一次真正滚动的元素（滚动容器由 naive-ui 内部持有，从事件里拿最可靠） */
let scroller: HTMLElement | null = null;

/**
 * 取当前滚动容器。优先用事件里拿到的那个，但它可能是**已经卸载**的旧列表
 * （`v-if` 在 items 变空时会重建整个虚拟列表），所以要校验还在文档里，
 * 否则切流后第一次 scrollToBottom 会写到一个脱离文档的节点上，静默无效。
 */
function scrollEl(): HTMLElement | null {
  if (scroller?.isConnected) return scroller;
  scroller = virtualRef.value?.listElRef ?? null;
  return scroller;
}

const POLL_INTERVAL_MS = 60_000;
let pollTimer: number | undefined;

function openItem(item: Item): void {
  if (item.id === stream.currentItemId) {
    stream.closeItem();
  } else {
    stream.openItem(item);
  }
}

function scrollToBottom(): void {
  const el = scrollEl();
  if (el) el.scrollTop = el.scrollHeight;
}

/** 向上加载更旧，并保持视觉位置（行高等高 → 位移就是「新增条数 × 行高」） */
async function loadOlderKeepPosition(el: HTMLElement): Promise<void> {
  const prevTop = el.scrollTop;
  const prevCount = stream.items.length;
  await stream.loadNewer();
  await nextTick();
  const added = stream.items.length - prevCount;
  if (added > 0) el.scrollTop = prevTop + added * ITEM_HEIGHT;
}

/** 滚动统一入口：事件目标就是真正滚动的那个元素 */
function onScroll(event: Event): void {
  const el = event.target;
  if (!(el instanceof HTMLElement)) return;
  scroller = el;
  // 向下：距底部不足 200px 时加载更多
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    void stream.loadMore().catch((e) => {
      message.error(e instanceof Error ? e.message : t("loadFailed"));
    });
  } else if (el.scrollTop <= 200 && stream.sortOrder === "o") {
    // 向上：仅最旧在前
    void loadOlderKeepPosition(el).catch(() => {});
  }
}

/** 加载直到撑满一屏（或没有更多） */
async function fillViewport(): Promise<void> {
  let guard = 0;
  while (guard++ < 20) {
    const el = scrollEl();
    if (!el || el.scrollHeight > el.clientHeight) break;
    const hasMore = stream.sortOrder === "n" ? stream.hasMore : stream.hasOlder;
    if (!hasMore) break;
    if (stream.sortOrder === "n") {
      await stream.loadMore();
    } else {
      await stream.loadNewer();
    }
    await nextTick();
  }
}

/** 点击新内容横幅 */
async function onBubbleClick(): Promise<void> {
  try {
    await stream.applyBubble();
    await nextTick();
    if (stream.sortOrder === "o") scrollToBottom();
    await fillViewport();
    if (stream.sortOrder === "o") scrollToBottom();
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("refreshFailed"));
  }
}

function toggleSort(): void {
  const next: SortOrder = stream.sortOrder === "n" ? "o" : "n";
  void stream.setSortOrder(next);
}

/** 去掉 HTML 标签，提取纯文本摘要 */
function plainText(html: string): string {
  return html.replace(/<[^>]*>/g, "").trim();
}

function formatDate(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(locale, { month: "2-digit", day: "2-digit" });
}

onMounted(() => {
  pollTimer = window.setInterval(() => {
    // 标签页隐藏时暂停轮询，避免后台空转
    if (document.visibilityState === "visible") {
      void stream.pollLatest();
    }
  }, POLL_INTERVAL_MS);

  // 用**捕获阶段**的原生监听，而不是依赖虚拟列表自己的 @scroll：
  // scroll 事件不冒泡，但捕获阶段会在途经的祖先上触发，所以挂在 .list-body 上就能
  // 收到内部滚动容器的滚动，并由 event.target 直接拿到那个元素。
  // 实测（生产构建）走组件的 @scroll 时回调没能落到处理函数上，触底加载会整个静默
  // 失效——这种失效不报错、不报网络错误，只有数一下请求数才看得出来。
  wrapperRef.value?.addEventListener("scroll", onScroll, { capture: true, passive: true });
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
  wrapperRef.value?.removeEventListener("scroll", onScroll, { capture: true });
});

// 供 ReaderView 在初次加载流后撑满一屏
defineExpose({ fillViewport });
</script>

<template>
  <main class="article-list" :class="{ 'full-width': fullWidth }">
    <header class="list-header">
      <!-- 手机/平板档：显示当前流名称（桌面档侧栏常驻、能看到高亮，所以不传这个 prop） -->
      <span v-if="streamName" class="list-title" :title="streamName">{{ streamName }}</span>
      <button class="sort-btn" @click="toggleSort">
        {{ stream.sortOrder === "n" ? t("newestFirst") : t("oldestFirst") }}
      </button>
    </header>

    <div ref="wrapperRef" class="list-body">
      <button
        v-if="stream.hasNewItems"
        class="new-items-banner"
        :class="stream.sortOrder === 'o' ? 'banner-bottom' : 'banner-top'"
        @click="onBubbleClick"
      >
        {{ stream.sortOrder === "o" ? t("newItemsLatest") : t("newItemsRefresh") }}
      </button>
      <!--
        虚拟列表：只渲染可视区附近的行。行数是「可视区高度 / 行高」的量级，
        跟已加载条数无关 —— 旧写法是 v-for 把整个 items 数组铺成真实 DOM，
        滚 100 页就是 2000 个节点。
        高度靠 flex: 1 从 .list-body 拿（虚拟列表必须有确定高度）。
      -->
      <n-virtual-list
        v-if="stream.items.length"
        ref="virtualRef"
        :items="stream.items"
        :item-size="ITEM_HEIGHT"
        key-field="id"
        style="flex: 1; min-height: 0"
      >
        <template #default="{ item }">
          <a
            class="article-item"
            :class="{ active: item.id === stream.currentItemId, read: isRead(item) }"
            :style="{ height: ITEM_HEIGHT + 'px' }"
            @click="openItem(item)"
          >
            <div class="item-top">
              <span v-if="!isRead(item)" class="unread-dot"></span>
              <h2 class="item-title">{{ item.title }}</h2>
            </div>
            <div class="item-meta">
              <span class="item-origin">{{ item.origin.title }}</span>
              <span class="item-date">{{ formatDate(item.published) }}</span>
            </div>
            <p class="item-snippet">{{ plainText(item.summary.content).slice(0, 80) }}</p>
          </a>
        </template>
      </n-virtual-list>
      <p v-else-if="stream.loadingMore" class="loading">{{ t("loading") }}</p>
      <p v-else-if="stream.loadError" class="empty">{{ t("loadFailedRetry") }}</p>
      <p v-else-if="stream.loaded" class="empty">{{ t("emptyArticles") }}</p>
    </div>
  </main>
</template>

<style scoped>
.article-list {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--app-border);
}

/* 手机/平板：占满容器 */
.article-list.full-width {
  width: 100%;
  border-right: none;
  /* 手机上它是**竖向** flex 项，要放开收缩：否则高度跟着内容走（= 已加载条数 × 行高，
     实测 2120px），超出部分被 .phone-pane 的 overflow: hidden 裁掉、根本够不着。
     桌面的 flex-shrink: 0 是为了守住 320px 宽度，在竖向布局里只会帮倒忙。 */
  flex-shrink: 1;
  min-height: 0;
}

.list-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
}

.list-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text-1);
}

.sort-btn {
  flex-shrink: 0;
  border: 1px solid var(--app-border);
  background: var(--app-card);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--app-text-2);
}

.sort-btn:hover {
  border-color: var(--app-primary);
  color: var(--app-primary);
}

.list-body {
  position: relative;
  flex: 1;
  /* 滚动交给内部的虚拟列表；这一层只负责「有确定高度」+ 给新内容横幅当定位基准 */
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.new-items-banner {
  position: absolute;
  left: 0;
  right: 0;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-primary);
  color: #fff;
  font-size: 13px;
  border: none;
  cursor: pointer;
  z-index: 10;
}

.new-items-banner:hover {
  background: var(--app-primary-hover);
}

.banner-top {
  top: 0;
}

.banner-bottom {
  bottom: 0;
}

.article-item {
  display: block;
  /* 行高由 ITEM_HEIGHT 给（内联 style），这里只负责让内容不要撑破它 */
  box-sizing: border-box;
  overflow: hidden;
  padding: 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--app-divider);
}

.article-item:hover {
  background: var(--app-hover);
}

.article-item.active {
  background: var(--app-primary-soft);
}

.item-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.unread-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--app-primary);
}

.item-title {
  font-size: 14px;
  margin: 0;
  font-weight: 500;
  /* 行高恒定是虚拟滚动的前提，所以标题最多两行（超出的用省略号） */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.article-item:not(.read) .item-title {
  font-weight: 700;
}

.item-meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-text-3);
}

.item-origin {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-date {
  flex-shrink: 0;
}

.item-snippet {
  font-size: 12px;
  color: var(--app-text-3);
  margin: 4px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.article-item.read .item-title {
  color: var(--app-text-2);
}

.article-item.read .item-snippet {
  color: var(--app-placeholder);
}

.loading,
.empty {
  text-align: center;
  color: var(--app-text-3);
  padding: 12px;
}
</style>
