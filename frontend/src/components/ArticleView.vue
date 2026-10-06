<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NScrollbar, NSelect, useMessage } from "naive-ui";
import { useStreamStore } from "@/stores/stream";
import { useSubscriptionsStore } from "@/stores/subscriptions";
import {
  isRead,
  isStarred,
  itemTagName,
  labelName,
  type LabelType,
} from "@/types/greader";
import CreateLabelModal from "@/components/CreateLabelModal.vue";
import {
  createEmbedConfirm,
  createEmbedFrame,
  playableUrl,
  sanitizeArticleHtml,
} from "@/utils/sanitize";
import { chapterSeconds, findPlayer, isBareFragment, seekTo } from "@/utils/chapter";
import { hasEmbedConsent, rememberEmbedConsent } from "@/utils/embedConsent";
import { t } from "@/i18n";

defineProps<{ showBack?: boolean; compact?: boolean }>();
const emit = defineEmits<{ (e: "back"): void }>();

const stream = useStreamStore();
const subs = useSubscriptionsStore();
const message = useMessage();

const currentItem = computed(() => stream.currentItem());

const currentItemHtml = computed(() =>
  currentItem.value
    ? sanitizeArticleHtml(currentItem.value.summary.content, {
        load: t("embedLoad"),
        open: t("embedOpen"),
      })
    : "",
);

/** 当前条目的源（`feed/12` 这种）—— 同意按源记 */
const currentFeedId = computed(() => currentItem.value?.origin.streamId ?? "");

/*
 * 附件（播客音频、视频、单集封面…）**不由前端渲染**，由后端拼在正文末尾
 * （见 `src/farewell_rss/api/_enclosures.py`）。
 *
 * 为什么改到服务端：GReader 的 `enclosure` 数组我们照发（那是契约），但**没有一家客户端读它**
 * —— ReadYou / Reeder / NetNewsWire 都只渲染 `summary.content` 里的 HTML。只发数组的话，
 * 播客在这些客户端里就是个没有播放器的简介页。拼进正文是 FreshRSS 的做法。
 *
 * 于是这边什么都不用做：消毒会把 `figure` / `audio` / `video` / `img` 放行（见 `sanitize.ts`），
 * `preload="none"` 也意味着不按播放不发请求，和后端那套「不自动加载第三方内容」的底线一致。
 */

/** 确认行 → 它替掉的那个占位器，点「取消」时原样换回来 */
const replacedPlaceholders = new WeakMap<HTMLElement, HTMLElement>();

/**
 * 正文里的链接。**返回 `true` 表示已经处理**，调用方不应再往下走。
 *
 * 两件事，都因为 hash 路由：
 *
 * 1. **章节链接**（`#t=00:05:18`）→ 不跳页，把正文末尾那个播放器跳过去。注意入库时 feedparser
 *    已经把它补成绝对地址（`…/feed/audio.xml#t=…`），所以判据是片段，见 `utils/chapter.ts`。
 * 2. 其它「就在本文档内跳」的锚点 → 拦下但什么都不做：`#foo` 会顶掉 `#/…`，匹配不上任何
 *    路由就什么都不渲染（`router/index.ts` 有意没有 catch-all），整页空白比不滚更糟。
 */
function onBodyLink(link: HTMLAnchorElement, event: MouseEvent, body: ParentNode): boolean {
  const href = link.getAttribute("href") ?? "";
  const seconds = chapterSeconds(href);
  if (seconds === null && !isBareFragment(href)) return false;

  event.preventDefault();
  if (seconds === null) return true;
  const player = findPlayer(body);
  if (player) seekTo(player, seconds);
  return true;
}

/**
 * 正文里的占位器是 v-html 注入的，挂不上 Vue 事件，所以用委托。
 *
 * 三道口：点「加载」→（第一次先问一次来源）→ 允许后才真的插 iframe。每一道都重新验
 * 一遍地址，而且**不信任 DOM 里的文字**：占位器（包括它显示的域名）是能被源站伪造的，
 * 所以确认行上的域名是从校验过的地址现算的。
 */
function onBodyClick(event: MouseEvent) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const body = event.currentTarget as HTMLElement | null;
  if (!body) return;

  // 占位器和确认行里的按钮也是链接，别被章节链接那套判断截走（它们的地址理论上也能带 `#t=`）
  if (!target.closest(".embed")) {
    const link = target.closest<HTMLAnchorElement>("a");
    if (link && onBodyLink(link, event, body)) return;
  }

  const embed = target.closest<HTMLElement>(".embed");
  if (!embed) return;

  const url = playableUrl(embed.dataset.embed ?? "");
  const ratio = embed.dataset.embedRatio ?? "";

  if (target.closest(".embed-load")) {
    event.preventDefault();
    if (url === null) return;
    const frame = createEmbedFrame(url, ratio);
    if (!frame) return;

    if (hasEmbedConsent(currentFeedId.value)) {
      // 换掉整行，而不是塞进去：占位器那圈边框内边距不该留在播放器外面
      embed.replaceWith(frame);
      return;
    }

    const confirm = createEmbedConfirm(url, ratio, {
      message: t("embedConfirm", { host: new URL(url).host }),
      allow: t("embedAllow"),
      cancel: t("cancel"),
    });
    replacedPlaceholders.set(confirm, embed);
    embed.replaceWith(confirm);
    return;
  }

  if (target.closest(".embed-allow")) {
    if (url === null) return;
    const frame = createEmbedFrame(url, ratio);
    if (!frame) return;
    rememberEmbedConsent(currentFeedId.value);
    embed.replaceWith(frame);
    return;
  }

  if (target.closest(".embed-cancel")) {
    const placeholder = replacedPlaceholders.get(embed);
    if (placeholder) embed.replaceWith(placeholder);
  }
}

const starred = computed(() => currentItem.value !== null && isStarred(currentItem.value));

const read = computed(() => currentItem.value !== null && isRead(currentItem.value));

const currentIndex = computed(() =>
  stream.items.findIndex((it) => it.id === stream.currentItemId),
);
const hasPrev = computed(() => currentIndex.value > 0);
const hasNext = computed(
  () => currentIndex.value >= 0 && currentIndex.value < stream.items.length - 1,
);

/** 收藏夹选择器的当前值（空字符串 = 纯收藏） */
const tagValue = computed(() => {
  const item = currentItem.value;
  if (!item) return "";
  return itemTagName(item, subs.starFolderIds) ?? "";
});

const tagOptions = computed(() => [
  { label: t("uncategorized"), value: "" },
  ...subs.starFolders.map((f) => ({
    label: labelName(f.id),
    value: labelName(f.id),
  })),
]);

// 新建收藏夹弹窗
const createModalRef = ref<{ open: (type: LabelType) => void } | null>(null);

// 收藏夹下拉的展开状态
const tagSelectShow = ref(false);

function openCreateTag(): void {
  tagSelectShow.value = false;
  createModalRef.value?.open("tag");
}

// 打开文章时自动标已读（用缓存的完整条目，跨流打开也能标记）
watch(
  () => stream.currentItemId,
  (id) => {
    const item = id ? stream.currentItem() : null;
    if (item && !isRead(item)) {
      void stream.markRead(item.id).catch((e) => {
        message.error(e instanceof Error ? e.message : t("markReadFailed"));
      });
    }
  },
);

async function toggleStar(): Promise<void> {
  const item = currentItem.value;
  if (!item) return;
  try {
    await stream.toggleStar(item.id);
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("operationFailed"));
  }
}

async function toggleRead(): Promise<void> {
  const item = currentItem.value;
  if (!item) return;
  try {
    await stream.toggleRead(item.id);
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("operationFailed"));
  }
}

function goPrev(): void {
  const idx = currentIndex.value;
  const prev = idx > 0 ? stream.items[idx - 1] : undefined;
  if (prev) stream.openItem(prev);
}

function goNext(): void {
  const idx = currentIndex.value;
  const next =
    idx >= 0 && idx < stream.items.length - 1
      ? stream.items[idx + 1]
      : undefined;
  if (next) stream.openItem(next);
}

async function onTagChange(value: string): Promise<void> {
  const item = currentItem.value;
  if (!item) return;
  try {
    await stream.setItemTag(item.id, value || null, subs.starFolderIds);
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("setCategoryFailed"));
  }
}
</script>

<template>
  <article class="article-view" :class="{ compact }">
    <!-- x-scrollable：正文里可能有宽表格/图，以前靠 overflow-y:auto 隐式获得的横向滚动要保留 -->
    <n-scrollbar
      class="article-scroll"
      x-scrollable
      :content-style="compact ? 'padding: 12px 16px' : 'padding: 24px'"
    >
    <template v-if="currentItem">
      <button v-if="showBack" class="view-back-btn" @click="emit('back')">‹ {{ t("back") }}</button>
      <header class="article-header">
        <h1>{{ currentItem.title }}</h1>
        <div class="article-meta">
          <span class="origin">{{ currentItem.origin.title }}</span>
          <a
            v-if="currentItem.canonical?.length"
            class="origin-link"
            :href="currentItem.canonical?.[0]?.href"
            target="_blank"
            rel="noopener noreferrer"
            >{{ t("originalArticle") }}</a
          >
        </div>
        <div class="article-actions">
          <n-button
            size="small"
            :type="starred ? 'warning' : 'default'"
            @mousedown.prevent
            @click="toggleStar"
          >
            {{ starred ? t("starredActive") : t("star") }}
          </n-button>
          <n-select
            v-if="starred"
            v-model:show="tagSelectShow"
            class="tag-select"
            size="small"
            :value="tagValue"
            :options="tagOptions"
            :placeholder="t('selectCategory')"
            @update:value="onTagChange"
          >
            <template #action>
              <div class="tag-select-action" @click="openCreateTag">{{ t("newCategoryAction") }}</div>
            </template>
          </n-select>
          <n-button size="small" quaternary @mousedown.prevent @click="toggleRead">
            {{ read ? t("markUnread") : t("markRead") }}
          </n-button>
          <n-button
            size="small"
            quaternary
            class="prev-btn"
            :disabled="!hasPrev"
            @mousedown.prevent
            @click="goPrev"
          >
            {{ t("prevArticle") }}
          </n-button>
          <n-button
            size="small"
            quaternary
            :disabled="!hasNext"
            @mousedown.prevent
            @click="goNext"
          >
            {{ t("nextArticle") }}
          </n-button>
        </div>
      </header>
      <div class="article-body" v-html="currentItemHtml" @click="onBodyClick"></div>
      <div class="article-footer">
        <n-button
          size="small"
          quaternary
          :disabled="!hasPrev"
          @mousedown.prevent
          @click="goPrev"
        >
          {{ t("prevArticle") }}
        </n-button>
        <n-button
          size="small"
          quaternary
          :disabled="!hasNext"
          @mousedown.prevent
          @click="goNext"
        >
          {{ t("nextArticle") }}
        </n-button>
      </div>
    </template>
    <p v-else class="placeholder">{{ t("selectToRead") }}</p>
    </n-scrollbar>

    <CreateLabelModal ref="createModalRef" />
  </article>
</template>

<style scoped>
.article-view {
  flex: 1;
  /* 滚动交给内部的 NScrollbar；min-width/min-height 必须有：
     去掉 overflow 后 flex 项的自动最小尺寸变回 min-content，宽表会把布局撑破 */
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.article-scroll {
  flex: 1;
  min-height: 0;
}

.view-back-btn {
  border: none;
  background: none;
  color: var(--app-primary);
  font-size: 15px;
  cursor: pointer;
  padding: 4px 0 12px;
}

.article-header {
  margin-bottom: 16px;
}

.article-header h1 {
  font-size: 22px;
  margin: 0 0 8px;
}

.article-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--app-text-3);
  margin-bottom: 12px;
}

.origin-link {
  color: var(--app-primary);
  text-decoration: none;
}

.article-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.prev-btn {
  margin-left: auto;
}

.tag-select {
  width: 160px;
}

.tag-select-action {
  padding: 6px 12px;
  cursor: pointer;
  color: var(--app-primary);
}

.tag-select-action:hover {
  background: var(--app-primary-soft);
}

.article-body {
  line-height: 1.7;
}

/* 正文里任何播放器都撑满栏宽（`<audio>` 默认 300px，占不满） */
.article-body :deep(audio),
.article-body :deep(video) {
  width: 100%;
  max-width: 100%;
}

/* 附件块（后端拼在正文末尾，标记见 api/_enclosures.py）。
   播放器和 💾 必须在同一行：`figure`/`p` 都是块级、里面按 inline 流排，上面那句
   `width: 100%` 就会把链接挤到第二行（实测附件块 85px 里有 18px 就是被挤下去的那行）。
   所以把内容行改成 flex：播放器吃掉剩余宽度、链接贴右且不参与伸缩。 */
.article-body :deep(.enclosure) {
  /* `<figure>` 浏览器默认还带 40px 左右外边距 */
  margin: 18px 0 0;
}

.article-body :deep(.enclosure-content) {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
}

.article-body :deep(.enclosure-content audio),
.article-body :deep(.enclosure-content video) {
  /* `min-width: 0` 不能省：flex 项的自动最小尺寸是 min-content，播放器控件的固有宽度
     会让它宁可溢出也不收缩，链接照样被顶出去。 */
  flex: 1 1 auto;
  min-width: 0;
}

.article-body :deep(.enclosure-content a) {
  flex: 0 0 auto;
}

.article-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
}

.article-body :deep(img) {
  max-width: 100%;
  height: auto;
}

/* 内嵌内容的占位器、确认行与真正插入的 iframe，标记由 utils/sanitize.ts 生成，所以要 :deep */
.article-body :deep(.embed) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 0;
  padding: 10px 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  font-size: 13px;
  color: var(--app-text-3);
}

.article-body :deep(.embed-load),
.article-body :deep(.embed-open) {
  color: var(--app-primary);
  text-decoration: none;
}

.article-body :deep(.embed-load) {
  font-weight: 600;
}

.article-body :deep(.embed-load:hover),
.article-body :deep(.embed-open:hover) {
  text-decoration: underline;
}

/* 把「在新窗口打开」推到右边 */
.article-body :deep(.embed-host) {
  margin-right: auto;
}

.article-body :deep(.embed-confirm-text) {
  margin-right: auto;
}

.article-body :deep(.embed-allow),
.article-body :deep(.embed-cancel) {
  padding: 2px 10px;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  background: none;
  color: var(--app-text-2);
  font: inherit;
  cursor: pointer;
}

.article-body :deep(.embed-allow) {
  border-color: var(--app-primary);
  color: var(--app-primary);
}

.article-body :deep(.embed-frame) {
  display: block;
  margin: 14px 0;
  border-radius: 8px;
  background: #000;
}

.placeholder {
  color: var(--app-placeholder);
  text-align: center;
  margin-top: 40vh;
}
</style>
