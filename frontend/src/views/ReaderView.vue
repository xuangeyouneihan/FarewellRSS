<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, watchEffect } from "vue";
import { useRouter } from "vue-router";
import { NAvatar, NButton, NDrawer, NDrawerContent, NDropdown, NInput, useMessage } from "naive-ui";
import { useSubscriptionsStore } from "@/stores/subscriptions";
import { useStreamStore } from "@/stores/stream";
import { useAuthStore } from "@/stores/auth";
import { STATE, labelName } from "@/types/greader";
import { useBreakpoint } from "@/responsive";
import Sidebar from "@/components/Sidebar.vue";
import ArticleList from "@/components/ArticleList.vue";
import ArticleView from "@/components/ArticleView.vue";
import UserProfileModal from "@/components/UserProfileModal.vue";
import AdminPanelModal from "@/components/AdminPanelModal.vue";
import { t } from "@/i18n";

const subs = useSubscriptionsStore();
const stream = useStreamStore();
const auth = useAuthStore();
const message = useMessage();
const router = useRouter();
const { isPhone, isTablet, isCompact } = useBreakpoint();

// 手机逐级钻取：sidebar → list → article
const phoneLevel = ref<"sidebar" | "list" | "article">("sidebar");
// 平板：侧栏抽屉开关
const sidebarDrawer = ref(false);

// 监听文章打开/关闭，驱动手机钻取层级
watch(
  () => stream.currentItemId,
  (id) => {
    if (!isPhone.value) return;
    if (id) phoneLevel.value = "article";
    else if (phoneLevel.value === "article") phoneLevel.value = "list";
  },
);
// 监听流切换：手机回到列表层，平板关抽屉
watch(
  () => stream.currentStreamId,
  () => {
    if (isPhone.value && phoneLevel.value === "sidebar") phoneLevel.value = "list";
    if (isTablet.value) sidebarDrawer.value = false;
  },
);
// 断点变化时复位手机层级
watch(isPhone, (v) => {
  if (v) phoneLevel.value = "sidebar";
});

function onSidebarNavigate(): void {
  // 手机进入列表层；平板关抽屉（平板的 navigate 已在模板上绑了关抽屉，这里只处理手机）
  if (isPhone.value) phoneLevel.value = "list";
}

function phoneBackToList(): void {
  stream.closeItem();
  phoneLevel.value = "list";
}

function phoneBackToSidebar(): void {
  phoneLevel.value = "sidebar";
}

function onMenuBtn(): void {
  if (isPhone.value) {
    phoneBackToSidebar();
  } else {
    sidebarDrawer.value = true;
  }
}

const searchQuery = ref("");
const profileModalRef = ref<{ open: () => void } | null>(null);
const adminPanelRef = ref<{ open: () => void } | null>(null);
const articleListRef = ref<{ fillViewport: () => Promise<void> } | null>(null);

// 用户下拉菜单
const userMenuOptions = computed(() => {
  const opts = [{ label: t("profile"), key: "profile" }];
  if (auth.isAdmin) opts.push({ label: t("adminPanel"), key: "admin" });
  opts.push({ label: t("logout"), key: "logout" });
  return opts;
});

async function onUserMenuSelect(key: string): Promise<void> {
  if (key === "profile") {
    profileModalRef.value?.open();
  } else if (key === "admin") {
    adminPanelRef.value?.open();
  } else if (key === "logout") {
    auth.logout();
    await router.push({ name: "login" });
  }
}

// 未读数前缀（仅全部文章 / 订阅源 / 订阅分类有）
function unreadPrefix(id: string): string {
  const n = subs.unreadCounts[id] ?? 0;
  return n > 0 ? `(${n}) ` : "";
}

// 根据当前流设置页面标题
watchEffect(() => {
  const id = stream.currentStreamId;
  let name: string | null = null;
  let withUnread = true;

  if (id === STATE.readingList) {
    name = null; // 全部文章：无名称段
  } else if (id.startsWith("feed/")) {
    name = subs.subscriptions.find((s) => s.id === id)?.title ?? t("subscriptions");
  } else if (id === STATE.starred) {
    name = t("starred");
    withUnread = false;
  } else if (id === STATE.uncategorized) {
    name = t("uncategorized");
    withUnread = false;
  } else if (id.startsWith("user/-/label/")) {
    name = labelName(id);
    // 收藏夹（type=tag）不带未读数；订阅分类（folder）带
    withUnread = stream.currentType !== "tag";
  } else if (id.startsWith("user/-/search/")) {
    name = t("search");
    withUnread = false;
  }

  // 拼接前缀（未读数 + 名称），前缀为空时连「 · 」一起去掉
  const unread = withUnread ? unreadPrefix(id).trim() : "";
  const prefix = [unread, name].filter(Boolean).join(" ");
  document.title = prefix ? `${prefix} · ${t("appName")}` : t("appName");
});

onMounted(async () => {
  // 三个请求互不依赖，并行加载首屏
  const results = await Promise.allSettled([
    subs.refresh(),
    stream.loadStream(STATE.readingList),
    auth.fetchUserInfo(),
  ]);
  const failed = results.find((r) => r.status === "rejected");
  if (failed && failed.status === "rejected") {
    const e = failed.reason;
    message.error(e instanceof Error ? e.message : t("loadFailed"));
  }
  // 首屏不足一屏时继续加载，直到出现滚动条或没有更多
  await nextTick();
  await articleListRef.value?.fillViewport();
});

function submitSearch(): void {
  const q = searchQuery.value.trim();
  if (q) {
    void stream.loadStream(`user/-/search/${q}`).catch((e) => {
      message.error(e instanceof Error ? e.message : t("searchFailed"));
    });
  } else {
    void stream.loadStream(STATE.readingList);
  }
}
</script>

<template>
  <div class="reader">
    <header class="topbar">
      <!-- 手机/平板：左上角抽屉按钮 -->
      <n-button
        v-if="isCompact"
        text
        class="menu-btn"
        @click="onMenuBtn"
      >
        {{ isPhone && phoneLevel !== "sidebar" ? "‹" : "☰" }}
      </n-button>
      <span class="brand">{{ t("appName") }}</span>
      <n-input
        v-model:value="searchQuery"
        class="search"
        :placeholder="t('searchArticles')"
        clearable
        @keydown.enter="submitSearch"
        @clear="submitSearch"
      />
      <n-dropdown
        trigger="click"
        :options="userMenuOptions"
        @select="(key: string | number) => onUserMenuSelect(String(key))"
      >
        <n-button size="small" quaternary class="user-btn">
          <n-avatar :size="20" round class="user-avatar">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="#fff" aria-hidden="true">
              <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
            </svg>
          </n-avatar>
          <span v-if="!isPhone">{{ auth.displayName ?? auth.username ?? t("userFallback") }}</span>
        </n-button>
      </n-dropdown>
    </header>

    <!-- 桌面：三栏 -->
    <div v-if="!isCompact" class="reader-body">
      <Sidebar />
      <div class="columns">
        <ArticleList ref="articleListRef" />
        <ArticleView />
      </div>
    </div>

    <!-- 平板：抽屉侧栏 + 列表 + 正文 -->
    <template v-else-if="isTablet">
      <n-drawer v-model:show="sidebarDrawer" placement="left" :width="280">
        <n-drawer-content :body-content-style="{ padding: 0 }">
          <Sidebar @navigate="sidebarDrawer = false" />
        </n-drawer-content>
      </n-drawer>
      <div class="reader-body">
        <div class="columns">
          <ArticleList ref="articleListRef" />
          <ArticleView />
        </div>
      </div>
    </template>

    <!-- 手机：逐级钻取 -->
    <div v-else class="reader-body">
      <div v-show="phoneLevel === 'sidebar'" class="phone-pane">
        <Sidebar @navigate="onSidebarNavigate" />
      </div>
      <div v-show="phoneLevel === 'list'" class="phone-pane">
        <ArticleList ref="articleListRef" show-back full-width @back="phoneBackToSidebar" />
      </div>
      <div v-show="phoneLevel === 'article'" class="phone-pane">
        <ArticleView show-back compact @back="phoneBackToList" />
      </div>
    </div>

    <UserProfileModal ref="profileModalRef" />
    <AdminPanelModal ref="adminPanelRef" />
  </div>
</template>

<style scoped>
.reader {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.topbar {
  position: relative;
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--app-border);
  flex-shrink: 0;
}

.brand {
  font-weight: 700;
  font-size: 15px;
  flex-shrink: 0;
}

.search {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  width: 360px;
  max-width: 40vw;
}

/* 手机/平板：搜索框退让左上角按钮 */
@media (max-width: 1024px) {
  .search {
    position: static;
    transform: none;
    flex: 1;
    max-width: none;
    margin: 0 8px;
    width: auto;
  }
}

.menu-btn {
  font-size: 18px;
  flex-shrink: 0;
  margin-right: 4px;
}

.phone-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.user-btn {
  margin-left: auto;
  flex-shrink: 0;
}

.user-avatar {
  margin-right: 6px;
}

.reader-body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.columns {
  flex: 1;
  display: flex;
  min-width: 0;
  min-height: 0;
}
</style>
