<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from "vue";
import { useRouter } from "vue-router";
import { NAvatar, NButton, NDrawer, NDrawerContent, NDropdown, NInput, useMessage } from "naive-ui";
import { useSubscriptionsStore } from "@/stores/subscriptions";
import { useStreamStore } from "@/stores/stream";
import { useAuthStore } from "@/stores/auth";
import { STATE, labelName } from "@/types/greader";
import { useBreakpoint } from "@/responsive";
import { useUrlState } from "@/urlState";
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

// 手机：list ⇄ article 两级钻取（订阅列表已收进左侧抽屉，与平板一致）
const phoneLevel = ref<"list" | "article">("list");
// 手机 / 平板：侧栏抽屉开关
const sidebarDrawer = ref(false);
// 状态 ⇄ URL：hash 层级 #/item/<hex> + query（stream / type / sidebar / modal）
const {
  applyFromUrl,
  migrateLegacyQuery,
  refineLabelType,
  openModal,
  profileShow,
  adminShow,
} = useUrlState({ sidebarDrawer, isCompact });

// 监听文章打开/关闭，驱动手机钻取层级
watch(
  () => stream.currentItemId,
  (id) => {
    if (!isPhone.value) return;
    if (id) phoneLevel.value = "article";
    else if (phoneLevel.value === "article") phoneLevel.value = "list";
  },
);
// 切换流：关抽屉（手机/平板从抽屉里选了订阅源或分类；桌面下这个值本来就是 false）
watch(
  () => stream.currentStreamId,
  () => {
    sidebarDrawer.value = false;
  },
);
// 断点变化时复位手机层级
watch(isPhone, (v) => {
  if (v) phoneLevel.value = "list";
});

/** 从抽屉里选了订阅源 / 分类：关抽屉；手机上回到列表层 */
function onSidebarNavigate(): void {
  sidebarDrawer.value = false;
  if (isPhone.value) phoneLevel.value = "list";
}

/** 打开侧栏抽屉（手机顶栏 ☰、手机列表页的「返回」） */
function openSidebar(): void {
  sidebarDrawer.value = true;
}

function phoneBackToList(): void {
  stream.closeItem();
  phoneLevel.value = "list";
}

const menuBtnRef = ref<InstanceType<typeof NButton> | null>(null);

/** 最近一次输入是键盘吗？用来区分「用户自己聚焦」与「浮层关完把焦点还回来」。

 不拿 :focus-visible 当判据：实测在「抽屉关闭时还焦点」这条路径上，它时真时假
 （似乎取决于此前是否敲过键盘），用它做判断会时灵时不灵。 */
let lastInputWasKeyboard = false;
function onKeyInput(): void {
  lastInputWasKeyboard = true;
}
function onPointerInput(): void {
  lastInputWasKeyboard = false;
}
onMounted(() => {
  window.addEventListener("keydown", onKeyInput, true);
  window.addEventListener("pointerdown", onPointerInput, true);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeyInput, true);
  window.removeEventListener("pointerdown", onPointerInput, true);
});

/** 把 ☰ 上「不是键盘来的」焦点立刻移掉

  触摸/鼠标点完会留下 :focus；抽屉关闭时 naive-ui 又会把焦点还给这个触发按钮（此时
  n-button 只看 :focus 就上主色）→ 看起来像「还按着」。键盘聚焦保持不动，不把 a11y 一起修没。 */
function dropMenuBtnFocus(): void {
  if (lastInputWasKeyboard) return;
  const el = menuBtnRef.value?.$el as HTMLElement | undefined;
  el?.blur();
}

function onMenuBtn(): void {
  openSidebar();
  dropMenuBtnFocus();
}

const searchQuery = ref("");
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
    openModal("profile");
  } else if (key === "admin") {
    openModal("admin");
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

/** 当前流的显示名 + 是否带未读数；「全部文章」没有名称段（name = null） */
function resolveStream(id: string): { name: string | null; withUnread: boolean } {
  if (id === STATE.readingList) return { name: null, withUnread: true };
  if (id.startsWith("feed/")) {
    return {
      name:
        subs.subscriptions.find((s) => s.id === id)?.title ?? t("subscriptions"),
      withUnread: true,
    };
  }
  if (id === STATE.starred) return { name: t("starred"), withUnread: false };
  if (id === STATE.uncategorized) {
    return { name: t("uncategorized"), withUnread: false };
  }
  if (id === STATE.history) return { name: t("history"), withUnread: false };
  if (id.startsWith("user/-/label/")) {
    // 收藏夹（type=tag）不带未读数；订阅分类（folder）带
    return { name: labelName(id), withUnread: stream.currentType !== "tag" };
  }
  if (id.startsWith("user/-/search/")) {
    return { name: t("search"), withUnread: false };
  }
  return { name: null, withUnread: true };
}

// 列表头里的当前流名称（手机/平板用；「全部文章」也要显示出来）
const listTitle = computed(
  () => resolveStream(stream.currentStreamId).name ?? t("allArticles"),
);

// 根据当前流设置页面标题
watchEffect(() => {
  const id = stream.currentStreamId;
  const { name, withUnread } = resolveStream(id);
  // 拼接前缀（未读数 + 名称），前缀为空时连「 · 」一起去掉
  const unread = withUnread ? unreadPrefix(id).trim() : "";
  const prefix = [unread, name].filter(Boolean).join(" ");
  document.title = prefix ? `${prefix} · ${t("appName")}` : t("appName");
});

onMounted(async () => {
  // 三个请求互不依赖，并行加载首屏
  const results = await Promise.allSettled([
    subs.refresh(),
    // 先把旧的 /?stream=…&item=… 链接搬进 hash，再按 URL 恢复
    migrateLegacyQuery().then(applyFromUrl),
    auth.fetchUserInfo(),
  ]);
  const failed = results.find((r) => r.status === "rejected");
  if (failed && failed.status === "rejected") {
    const e = failed.reason;
    message.error(e instanceof Error ? e.message : t("loadFailed"));
  }
  // URL 里 label 流没带 type 的（手写/外部链接），等订阅列表到位再补齐类型
  await refineLabelType();
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
    <!-- 手机档：文章列表保留页面顶栏（品牌/搜索/用户菜单/☰，抽屉入口就是它），
         只有进正文时才把顶栏收掉，把整屏高度留给阅读。 -->
    <header v-if="!isPhone || phoneLevel === 'list'" class="topbar">
      <!-- 手机/平板：左上角抽屉按钮 -->
      <n-button
        v-if="isCompact"
        ref="menuBtnRef"
        text
        class="menu-btn"
        @click="onMenuBtn"
        @focus="dropMenuBtnFocus"
      >
        ☰
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
        placement="bottom-end"
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

    <!-- 手机 / 平板：订阅列表收进左侧抽屉（同一份，两档布局不会漂移） -->
    <n-drawer v-if="isCompact" v-model:show="sidebarDrawer" placement="left" :width="280">
      <n-drawer-content :body-content-style="{ padding: 0 }">
        <Sidebar @navigate="onSidebarNavigate" />
      </n-drawer-content>
    </n-drawer>

    <!-- 桌面：三栏 -->
    <div v-if="!isCompact" class="reader-body">
      <Sidebar />
      <div class="columns">
        <ArticleList ref="articleListRef" />
        <ArticleView />
      </div>
    </div>

    <!-- 平板：列表 + 正文（侧栏在抽屉里） -->
    <div v-else-if="isTablet" class="reader-body">
      <div class="columns">
        <ArticleList ref="articleListRef" :stream-name="listTitle" />
        <ArticleView />
      </div>
    </div>

    <!-- 手机：列表 ⇄ 正文 两级钻取（侧栏同上在抽屉里） -->
    <div v-else class="reader-body">
      <div v-show="phoneLevel === 'list'" class="phone-pane">
        <ArticleList ref="articleListRef" full-width :stream-name="listTitle" />
      </div>
      <div v-show="phoneLevel === 'article'" class="phone-pane">
        <ArticleView show-back compact @back="phoneBackToList" />
      </div>
    </div>

    <UserProfileModal v-model:show="profileShow" />
    <AdminPanelModal v-model:show="adminShow" />
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

/* 手机/平板：搜索框在品牌与头像之间居中，且不挤占它们 */
@media (max-width: 1024px) {
  .search {
    /* 这里必须是 relative 而不是 static：.n-input__border / .n-input__state-border 都是
       absolute 定位、以 .n-input 自身为包含块；写成 static 后包含块会落到 .topbar，
       边框会被拉成整条顶栏那么大（实测平板下 900×50，而输入框只有 360×34）。 */
    position: relative;
    /* 桌面档的 left:50% 是配绝对定位用的；这里 position 变成 relative 后它依然生效，
       会把搜索框整体右推半个顶栏（实测平板 x 从 103 变 541、还溢出 1px），必须一起清掉。 */
    left: auto;
    transform: none;
    /* 居中：只靠搜索框两侧的 auto 外边距等分剩余空间（不再用 flex:1 往左长）。
       注意 .user-btn 在桌面档也带 margin-left:auto，三份 auto 会按 1:1:1 分，
       左右就不等了（实测 87 : 172）——所以在下面把紧凑档的头像那份清掉。
       360px 既是 flex 基准也是上限；窄屏时只有搜索框自己收缩（☰/品牌/头像都是 flex-shrink:0）。 */
    flex: 0 1 360px;
    width: 360px;
    min-width: 0;
    margin: 0 auto;
  }

  /* 紧凑档头像不再吃 auto margin（居中交给搜索框两侧）。
     选择器必须带 .topbar：文件末尾的基础规则 `.user-btn{margin-left:auto}` 在后面，
     同特异性下它赢，光写 .user-btn 压不住（实测头像仍在按 1:2 分摊）。 */
  .topbar .user-btn {
    margin-left: 0;
  }
}

/* 窄屏没有余量可居中，而且固定 360 基准 + auto 边距会把搜索框压得很窄
   （实测 480 时只剩 192px）、头像也不再靠右：这里改成「占满中间 + 头像回最右」。 */
@media (max-width: 560px) {
  .search {
    flex: 1 1 auto; /* 0% 基准 → 直接吃掉中间剩余空间（上限仍是 360） */
    max-width: 360px;
    margin: 0 8px;
  }

  .topbar .user-btn {
    margin-left: auto;
  }
}

.menu-btn {
  font-size: 18px;
  flex-shrink: 0;
  margin-right: 4px;
}

/* n-button 的 text 按钮在 :focus 时会把颜色变主色；抽屉关闭时 naive-ui 会把焦点还给
   触发它的这个按钮（此时 :focus-visible 为 false），于是它看起来「还亮着」，鼠标移开也不恢复。
   改成只在键盘聚焦（:focus-visible）或 hover 时高亮：鼠标点完留下的 focus 不上色。 */
.menu-btn:focus:not(:focus-visible):not(:hover) {
  color: inherit;
}

/* 手机上还有第二条路径：触摸设备点过之后 :hover 会「粘」在按钮上（iOS Safari 一直粘到
   点别处，Android 也有同类行为），n-button 的 hover 样式照样上主色 —— 上面那条规则带着
   :not(:hover)，正好把这条放过去，所以手机上看着「还是亮的」。
   ☰ 按下去立刻被抽屉盖住、这些高亮本来就看不见，索性在没有真实 hover 的设备上关掉它。
   特异性：naive-ui 用的是 .n-button--text-type:not(.n-button--disabled):hover（0,3,0），
   所以这里写 .menu-btn.menu-btn:hover（0,3,0）+ scoped 属性（0,4,0）才压得住。 */
@media (hover: none), (pointer: coarse) {
  .menu-btn.menu-btn:hover {
    color: inherit;
  }
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

/* 头像与用户名的间距。手机档没有名字（那个 span 被 v-if 掉），这 6px 就变成了「右内边距」，
   头像在按钮的 hover/focus 填充里偏左（实测左 10px / 右 16px）→ 只在真有名字时留间距。 */
.user-avatar:not(:only-child) {
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
