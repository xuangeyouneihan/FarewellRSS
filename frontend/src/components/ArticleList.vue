<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { useMessage } from "naive-ui";
import { useStreamStore, type SortOrder } from "@/stores/stream";
import { isRead, type Item } from "@/types/greader";
import { t, locale } from "@/i18n";

const props = defineProps<{ showBack?: boolean; fullWidth?: boolean }>();
const emit = defineEmits<{ (e: "back"): void }>();
void props;
void emit;

const stream = useStreamStore();
const message = useMessage();
const listEl = ref<HTMLElement | null>(null);

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
  const el = listEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

/** 向上加载更旧，并保持视觉位置 */
async function loadOlderKeepPosition(): Promise<void> {
  const el = listEl.value;
  if (!el) return;
  const prevHeight = el.scrollHeight;
  const prevTop = el.scrollTop;
  await stream.loadNewer();
  await nextTick();
  const current = listEl.value;
  if (current) current.scrollTop = current.scrollHeight - prevHeight + prevTop;
}

function onScroll(): void {
  const el = listEl.value;
  if (!el) return;
  // 向下：距底部不足 200px 时加载更多
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    void stream.loadMore().catch((e) => {
      message.error(e instanceof Error ? e.message : t("loadFailed"));
    });
  } else if (el.scrollTop <= 200 && stream.sortOrder === "o") {
    // 向上：仅最旧在前
    void loadOlderKeepPosition().catch(() => {});
  }
}

/** 加载直到撑满一屏（或没有更多） */
async function fillViewport(): Promise<void> {
  const el = listEl.value;
  if (!el) return;
  let guard = 0;
  while (el.scrollHeight <= el.clientHeight && guard++ < 20) {
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
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});

// 供 ReaderView 在初次加载流后撑满一屏
defineExpose({ fillViewport });
</script>

<template>
  <main class="article-list" :class="{ 'full-width': fullWidth }">
    <header class="list-header">
      <button v-if="showBack" class="back-btn" @click="emit('back')">‹ {{ t("back") }}</button>
      <button class="sort-btn" @click="toggleSort">
        {{ stream.sortOrder === "n" ? t("newestFirst") : t("oldestFirst") }}
      </button>
    </header>

    <div class="list-body" ref="listEl" @scroll="onScroll">
      <button
        v-if="stream.hasNewItems"
        class="new-items-banner"
        :class="stream.sortOrder === 'o' ? 'banner-bottom' : 'banner-top'"
        @click="onBubbleClick"
      >
        {{ stream.sortOrder === "o" ? t("newItemsLatest") : t("newItemsRefresh") }}
      </button>
      <a
        v-for="item in stream.items"
        :key="item.id"
        class="article-item"
        :class="{ active: item.id === stream.currentItemId, read: isRead(item) }"
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
      <p v-if="stream.loadingMore" class="loading">{{ t("loading") }}</p>
      <p v-else-if="stream.loadError && !stream.items.length" class="empty">{{ t("loadFailedRetry") }}</p>
      <p v-else-if="stream.loaded && !stream.items.length" class="empty">{{ t("emptyArticles") }}</p>
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
}

.list-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
}

.back-btn {
  border: none;
  background: none;
  color: var(--app-primary);
  font-size: 14px;
  cursor: pointer;
  padding: 4px 4px;
  flex-shrink: 0;
}

.sort-btn {
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
  overflow-y: auto;
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
