<script setup lang="ts">
import { computed, ref, watch } from "vue";
import DOMPurify from "dompurify";
import { NButton, NSelect, useMessage } from "naive-ui";
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
import { t } from "@/i18n";

defineProps<{ showBack?: boolean; compact?: boolean }>();
const emit = defineEmits<{ (e: "back"): void }>();

const stream = useStreamStore();
const subs = useSubscriptionsStore();
const message = useMessage();

const currentItem = computed(() => stream.currentItem());

const currentItemHtml = computed(() =>
  currentItem.value ? DOMPurify.sanitize(currentItem.value.summary.content) : "",
);

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
      <div class="article-body" v-html="currentItemHtml"></div>
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

    <CreateLabelModal ref="createModalRef" />
  </article>
</template>

<style scoped>
.article-view {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

/* 手机：减小内边距 */
.article-view.compact {
  padding: 12px 16px;
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

.placeholder {
  color: var(--app-placeholder);
  text-align: center;
  margin-top: 40vh;
}
</style>
