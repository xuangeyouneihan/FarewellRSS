<script setup lang="ts">
import { computed, ref } from "vue";
import {
  NButton,
  NDropdown,
  NInput,
  NModal,
  NSelect,
  useDialog,
  useMessage,
} from "naive-ui";
import { useSubscriptionsStore } from "@/stores/subscriptions";
import { useStreamStore } from "@/stores/stream";
import {
  STATE,
  labelName,
  type LabelType,
  type Subscription,
  type TagItem,
} from "@/types/greader";
import CreateLabelModal from "@/components/CreateLabelModal.vue";
import { t } from "@/i18n";

defineOptions({ name: "AppSidebar" });

const emit = defineEmits<{ (e: "navigate"): void }>();

const subs = useSubscriptionsStore();
const stream = useStreamStore();
const message = useMessage();
const dialog = useDialog();

// 添加订阅
const showAddModal = ref(false);
const feedUrl = ref("");
const adding = ref(false);

// 新建文件夹 / 收藏夹
const createModalRef = ref<{ open: (type: LabelType) => void } | null>(null);
const createModalShow = ref(false);
const createType = ref<LabelType>("tag");

// 重命名
const renameTarget = ref<TagItem | null>(null);
const renameValue = ref("");
const renaming = ref(false);

// 归类订阅源
const moveTarget = ref<Subscription | null>(null);
const moveValue = ref("");
const moving = ref(false);
const moveSelectShow = ref(false);

const systemStreams = computed(() => [
  { id: STATE.readingList, title: t("allArticles"), type: undefined as LabelType | undefined },
]);

// 已收藏分组是否收起
const starredCollapsed = ref(false);

const showRenameModal = computed({
  get: () => renameTarget.value !== null,
  set: (v: boolean) => {
    if (!v) renameTarget.value = null;
  },
});

const showMoveModal = computed({
  get: () => moveTarget.value !== null,
  set: (v: boolean) => {
    if (!v) moveTarget.value = null;
  },
});

const moveOptions = computed(() => [
  { label: t("noneNoCategory"), value: "" },
  ...subs.folders.map((f) => ({
    label: labelName(f.id),
    value: labelName(f.id),
  })),
]);

// 文件夹展开/收起状态（不在集合中 = 展开）
const collapsedFolders = ref<Set<string>>(new Set());

// 未归类到任何文件夹的订阅源
const uncategorizedSubs = computed(() =>
  subs.subscriptions.filter((s) => !s.categories.length),
);

function feedsOfFolder(folderId: string): Subscription[] {
  return subs.subscriptions.filter((s) => s.categories[0]?.id === folderId);
}

function isFolderCollapsed(folderId: string): boolean {
  return collapsedFolders.value.has(folderId);
}

function toggleFolder(folderId: string, event?: MouseEvent): void {
  const next = new Set(collapsedFolders.value);
  if (next.has(folderId)) {
    next.delete(folderId);
  } else {
    next.add(folderId);
  }
  collapsedFolders.value = next;
  // 点击后去掉按钮焦点，避免 Naive UI 的 focus 态让箭头一直保持高亮
  const target = event?.currentTarget;
  if (target instanceof HTMLElement) {
    target.blur();
  }
}

function toggleStarred(event?: MouseEvent): void {
  starredCollapsed.value = !starredCollapsed.value;
  const target = event?.currentTarget;
  if (target instanceof HTMLElement) {
    target.blur();
  }
}

function openStream(id: string, type?: LabelType): void {
  emit("navigate");
  void stream.loadStream(id, type).catch((e) => {
    message.error(e instanceof Error ? e.message : t("loadFailed"));
  });
}

function unread(id: string): number {
  return subs.unreadCounts[id] ?? 0;
}

function isActive(id: string): boolean {
  return stream.currentStreamId === id;
}

async function submitAdd(): Promise<void> {
  const url = feedUrl.value.trim();
  if (!url) {
    message.warning(t("inputFeedUrl"));
    return;
  }
  adding.value = true;
  try {
    const streamId = await subs.addSubscription(url);
    showAddModal.value = false;
    feedUrl.value = "";
    await stream.loadStream(streamId);
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("addSubFailed"));
  } finally {
    adding.value = false;
  }
}

function openCreate(type: LabelType): void {
  createType.value = type;
  createModalRef.value?.open(type);
}

function groupAddMenu() {
  return [
    { label: t("subscriptionItem"), key: "add-subscription" },
    { label: t("category"), key: "create-folder" },
  ];
}

function onGroupAddAction(key: string): void {
  if (key === "add-subscription") {
    showAddModal.value = true;
  } else if (key === "create-folder") {
    openCreate("folder");
  }
}

function openRename(f: TagItem): void {
  renameTarget.value = f;
  renameValue.value = labelName(f.id);
}

async function submitRename(): Promise<void> {
  const target = renameTarget.value;
  const name = renameValue.value.trim();
  if (!target || !name) return;
  const oldName = labelName(target.id);
  renaming.value = true;
  try {
    await subs.renameLabel(target.id, name, target.type ?? "folder");
    stream.renameItemLabel(oldName, name);
    renameTarget.value = null;
    renameValue.value = "";
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("renameFailed"));
  } finally {
    renaming.value = false;
  }
}

function confirmDelete(f: TagItem): void {
  const isFolder = f.type === "folder";
  dialog.warning({
    title: isFolder ? t("deleteSubCategoryTitle") : t("deleteStarCategoryTitle"),
    content: isFolder
      ? `${t("deleteConfirmPrefix")}${t("deleteSubCategoryTitle")}「${labelName(f.id)}」${t("suffixQuestion")}${t("deleteSubCategoryContent")}`
      : `${t("deleteConfirmPrefix")}${t("deleteStarCategoryTitle")}「${labelName(f.id)}」${t("suffixQuestion")}${t("deleteStarCategoryContent")}`,
    positiveText: t("delete"),
    negativeText: t("cancel"),
    onPositiveClick: async () => {
      try {
        await subs.deleteLabel(f.id, f.type ?? "folder");
      } catch (e) {
        message.error(e instanceof Error ? e.message : t("deleteFailed"));
      }
    },
  });
}

function onLabelAction(key: string, f: TagItem): void {
  if (key === "rename") openRename(f);
  else if (key === "delete") confirmDelete(f);
}

function labelMenu() {
  return [
    { label: t("rename"), key: "rename" },
    { label: t("delete"), key: "delete" },
  ];
}

function openMove(sub: Subscription): void {
  moveTarget.value = sub;
  moveValue.value = sub.categories[0]?.label ?? "";
}

function openCreateFolder(): void {
  moveSelectShow.value = false;
  openCreate("folder");
}

async function submitMove(): Promise<void> {
  const target = moveTarget.value;
  if (!target) return;
  moving.value = true;
  try {
    await subs.moveSubscriptionToFolder(target.id, moveValue.value || null);
    moveTarget.value = null;
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("moveFailed"));
  } finally {
    moving.value = false;
  }
}

function onSubAction(key: string, sub: Subscription): void {
  if (key === "move") {
    openMove(sub);
  } else if (key === "unsubscribe") {
    dialog.warning({
      title: t("unsubscribe"),
      content: `${t("confirmUnsubscribe")}「${sub.title}」${t("suffixQuestion")}`,
      positiveText: t("unsubscribe"),
      negativeText: t("cancel"),
      onPositiveClick: async () => {
        try {
          await subs.unsubscribe(sub.id);
        } catch (e) {
          message.error(e instanceof Error ? e.message : t("unsubscribeFailed"));
        }
      },
    });
  }
}

function subMenu() {
  return [
    { label: t("editCategory"), key: "move" },
    { label: t("unsubscribe"), key: "unsubscribe" },
  ];
}
</script>

<template>
  <aside class="sidebar">
    <nav>
      <ul class="menu">
        <li v-for="s in systemStreams" :key="s.id">
          <a :class="{ active: isActive(s.id) }" @click="openStream(s.id, s.type)">
            <span class="label">{{ s.title }}</span>
            <span v-if="unread(s.id)" class="badge">{{ unread(s.id) }}</span>
          </a>
        </li>

        <!-- 已收藏（可展开：未分类 + 收藏夹） -->
        <li>
          <div class="folder-row" :class="{ active: isActive(STATE.starred) }">
            <n-button
              text
              size="tiny"
              class="folder-toggle"
              :class="{ expanded: !starredCollapsed }"
              @click="toggleStarred($event)"
            >
              {{ starredCollapsed ? "▶" : "▼" }}
            </n-button>
            <a class="row-main" @click="openStream(STATE.starred)">
              <span class="label">{{ t("starred") }}</span>
            </a>
            <n-button
              text
              size="tiny"
              class="row-action plus-action"
              :class="{ 'trigger-active': createModalShow && createType === 'tag' }"
              @mousedown.prevent
              @click="openCreate('tag')"
            >
              +
            </n-button>
          </div>
          <ul v-if="!starredCollapsed" class="menu sub-menu">
            <li class="row">
              <div class="row-body" :class="{ active: isActive(STATE.uncategorized) }">
                <a class="row-main" @click="openStream(STATE.uncategorized)">
                  <span class="label">{{ t("uncategorized") }}</span>
                </a>
              </div>
            </li>
            <li v-for="f in subs.starFolders" :key="f.id" class="row">
              <div class="row-body" :class="{ active: isActive(f.id) }">
                <a class="row-main" @click="openStream(f.id, 'tag')">
                  <span class="label">{{ labelName(f.id) }}</span>
                  <span v-if="unread(f.id)" class="badge">{{ unread(f.id) }}</span>
                </a>
                <n-dropdown
                  trigger="click"
                  :options="labelMenu()"
                  @select="(key: string | number) => onLabelAction(String(key), f)"
                >
                  <n-button text size="tiny" class="row-action">⋯</n-button>
                </n-dropdown>
              </div>
            </li>
          </ul>
        </li>
      </ul>

      <div class="group-header">
        <h3 class="group-title">{{ t("subscriptions") }}</h3>
        <n-dropdown
          trigger="click"
          :options="groupAddMenu()"
          @select="(key: string | number) => onGroupAddAction(String(key))"
        >
          <n-button
            text
            size="tiny"
            class="group-add"
          >
            +
          </n-button>
        </n-dropdown>
      </div>
      <ul v-if="subs.folders.length || subs.subscriptions.length" class="menu">
        <!-- 文件夹（含其下订阅源） -->
        <li v-for="f in subs.folders" :key="f.id" class="folder-item">
          <div class="folder-row" :class="{ active: isActive(f.id) }">
            <n-button
              text
              size="tiny"
              class="folder-toggle"
              :class="{ expanded: !isFolderCollapsed(f.id) }"
              @click="toggleFolder(f.id, $event)"
            >
              {{ isFolderCollapsed(f.id) ? "▶" : "▼" }}
            </n-button>
            <a class="row-main" @click="openStream(f.id, 'folder')">
              <span class="label">{{ labelName(f.id) }}</span>
              <span v-if="unread(f.id)" class="badge">{{ unread(f.id) }}</span>
            </a>
            <n-dropdown
              trigger="click"
              :options="labelMenu()"
              @select="(key: string | number) => onLabelAction(String(key), f)"
            >
              <n-button text size="tiny" class="row-action">⋯</n-button>
            </n-dropdown>
          </div>
          <ul v-if="!isFolderCollapsed(f.id)" class="menu sub-menu">
            <li v-for="sub in feedsOfFolder(f.id)" :key="sub.id" class="row">
              <div class="row-body" :class="{ active: isActive(sub.id) }">
                <a class="row-main" @click="openStream(sub.id)">
                  <span class="label">{{ sub.title }}</span>
                  <span v-if="unread(sub.id)" class="badge">{{ unread(sub.id) }}</span>
                </a>
                <n-dropdown
                  trigger="click"
                  :options="subMenu()"
                  @select="(key: string | number) => onSubAction(String(key), sub)"
                >
                  <n-button text size="tiny" class="row-action">⋯</n-button>
                </n-dropdown>
              </div>
            </li>
          </ul>
        </li>
        <!-- 未归类订阅源 -->
        <li v-for="sub in uncategorizedSubs" :key="sub.id" class="row">
          <div class="row-body" :class="{ active: isActive(sub.id) }">
            <a class="row-main" @click="openStream(sub.id)">
              <span class="label">{{ sub.title }}</span>
              <span v-if="unread(sub.id)" class="badge">{{ unread(sub.id) }}</span>
            </a>
            <n-dropdown
              trigger="click"
              :options="subMenu()"
              @select="(key: string | number) => onSubAction(String(key), sub)"
            >
              <n-button text size="tiny" class="row-action">⋯</n-button>
            </n-dropdown>
          </div>
        </li>
      </ul>
    </nav>

    <n-modal v-model:show="showAddModal" preset="card" :title="t('addSubscription')" style="width: 360px">
      <n-input
        v-model:value="feedUrl"
        :placeholder="t('feedUrlPlaceholder')"
        @keydown.enter="submitAdd"
      />
      <template #footer>
        <n-button size="small" :loading="adding" type="primary" @click="submitAdd"> {{ t("add") }} </n-button>
      </template>
    </n-modal>

    <CreateLabelModal
      ref="createModalRef"
      @update:show="createModalShow = $event"
    />

    <n-modal v-model:show="showRenameModal" preset="card" :title="t('rename')" style="width: 320px">
      <n-input
        v-model:value="renameValue"
        :placeholder="t('newName')"
        @keydown.enter="submitRename"
      />
      <template #footer>
        <n-button size="small" :loading="renaming" type="primary" @click="submitRename"> {{ t("ok") }} </n-button>
      </template>
    </n-modal>

    <n-modal v-model:show="showMoveModal" preset="card" :title="t('editCategory')" style="width: 320px">
      <n-select
        v-model:value="moveValue"
        v-model:show="moveSelectShow"
        :options="moveOptions"
      >
        <template #action>
          <div class="move-select-action" @click="openCreateFolder">{{ t("newCategoryAction") }}</div>
        </template>
      </n-select>
      <template #footer>
        <n-button size="small" :loading="moving" type="primary" @click="submitMove"> {{ t("ok") }} </n-button>
      </template>
    </n-modal>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 200px;
  flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid var(--app-border);
  padding: 8px;
}

/* 手机（钻取整页）/ 平板（抽屉内）：占满容器 */
@media (max-width: 1024px) {
  .sidebar {
    width: 100%;
    border-right: none;
  }
}

.move-select-action {
  padding: 6px 12px;
  cursor: pointer;
  color: var(--app-primary);
}

.move-select-action:hover {
  background: var(--app-primary-soft);
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  margin-bottom: 4px;
}

.group-title {
  font-size: 14px;
  color: var(--app-text-3);
  margin: 0;
  padding-left: 8px;
}

.group-add {
  font-size: 16px;
  line-height: 1;
  min-width: 20px;
  padding: 0 4px;
  color: var(--app-text-3);
}

.menu {
  list-style: none;
  padding: 0;
  margin: 0;
}

.menu li.row {
  display: flex;
  align-items: center;
}

/* 系统流（无操作按钮） */
.menu > li > a {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--app-text-1);
  text-decoration: none;
}

.menu > li > a:hover {
  background: var(--app-hover);
}

.menu > li > a.active {
  background: var(--app-primary-soft);
  color: var(--app-primary);
}

/* 有操作按钮的行体（标签 + ⋯），hover/active 背景画在这一层 */
.menu .row-body {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  border-radius: 4px;
}

.menu .row-body:hover {
  background: var(--app-hover);
}

.menu .row-body.active {
  background: var(--app-primary-soft);
}

.menu .row-body.active .row-main {
  color: var(--app-primary);
}

.menu a.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  cursor: pointer;
  color: var(--app-text-1);
  text-decoration: none;
}

.row-action {
  flex-shrink: 0;
  color: var(--app-text-3);
  padding: 0 4px;
}

.trigger-active {
  color: var(--n-text-color-focus);
}

.plus-action {
  font-size: 16px;
  line-height: 1;
  min-width: 20px;
}

.folder-row {
  display: flex;
  align-items: center;
  border-radius: 4px;
}

.folder-row:hover {
  background: var(--app-hover);
}

.folder-row.active {
  background: var(--app-primary-soft);
}

.folder-row.active .row-main {
  color: var(--app-primary);
}

.folder-toggle {
  flex-shrink: 0;
  width: 16px;
  margin-left: 6px;
  padding: 0;
  color: var(--app-text-3);
  font-size: 10px;
  line-height: 1;
}

/* 展开态（▼）保持展开色，不随焦点丢失；颜色跟随 Naive UI 主题 focus 色 */
.folder-toggle.expanded {
  color: var(--n-text-color-focus);
}

/* 分组标题与子项文字对齐（箭头已占 8+16=24px，标题不再额外缩进） */
.menu .folder-row a.row-main {
  padding-left: 2px;
}

.sub-menu .row-body {
  margin-left: 16px;
}

.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--app-text-3);
  background: var(--app-tag);
  border-radius: 10px;
  padding: 0 6px;
  line-height: 16px;
}
</style>
