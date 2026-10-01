<script setup lang="ts">
import { computed, watchEffect } from "vue";
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  lightTheme,
  useOsTheme,
} from "naive-ui";

// 跟随浏览器/系统的浅色深色偏好
const osTheme = useOsTheme();
const isDark = computed(() => osTheme.value === "dark");

// 把 Naive UI 主题变量注入为全局 CSS 变量，供各组件 var() 引用。
// 这样浅色/深色只需由 Naive UI 主题决定，改主题时只改这一处。
watchEffect(() => {
  const common = (isDark.value ? darkTheme : lightTheme).common;
  const root = document.documentElement;
  const set = (k: string, v: string) => root.style.setProperty(k, v);

  set("--app-text-1", common.textColor1);
  set("--app-text-2", common.textColor2);
  set("--app-text-3", common.textColor3);
  set("--app-placeholder", common.placeholderColor);
  set("--app-border", common.borderColor);
  set("--app-divider", common.dividerColor);
  set("--app-body", common.bodyColor);
  set("--app-card", common.cardColor);
  set("--app-hover", common.hoverColor);
  set("--app-tag", common.tagColor);
  set("--app-error", common.errorColor);

  // 项目自定义主色（Google 蓝），深色下用更亮的蓝
  set("--app-primary", isDark.value ? "#8ab4f8" : "#1a73e8");
  set("--app-primary-hover", isDark.value ? "#aecbfa" : "#1665c4");
  set("--app-primary-soft", isDark.value ? "rgba(138, 180, 248, 0.12)" : "#e8f0fe");

  // body 背景/文字跟随主题（各组件根元素未显式设背景时继承这里）
  document.body.style.background = common.bodyColor;
  document.body.style.color = common.textColor2;

  // 让浏览器原生 UI（滚动条等）跟随深色/浅色
  root.style.colorScheme = isDark.value ? "dark" : "light";
});
</script>

<template>
  <n-config-provider :theme="isDark ? darkTheme : null">
    <n-dialog-provider>
      <n-message-provider>
        <RouterView />
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<!-- 全局（非 scoped）：限制所有 modal 弹窗高度，超出后内容区滚动，
     header（标题+叉号）和 footer（确定/取消等按钮）保持固定。
     注意：preset="card" 的弹窗根元素同时带 n-card 和 n-modal 两个类。 -->
<style>
.n-card.n-modal {
  max-height: 85vh;
  display: flex;
  flex-direction: column;
}

/* 弹窗封顶 85vh：header 固定，剩下的高度给内容区。
   内容区滚动由 content-scrollable 负责——Naive UI 会在 header 与内容之间插一个
   NScrollbar（.n-card__content-scrollbar，带 overflow: hidden），它作为 flex 项
   会自动收缩、正好吃掉剩余高度。所以这里只需要「确定高度 + 纵向 flex」两件事，
   不需要再给 .n-card-content 设 overflow/高度（实测它现在是个内层普通 div）。 */
.n-card.n-modal {
  max-height: 85vh;
  display: flex;
  flex-direction: column;
}

/* footer 里的按钮别被压扁；header 由 Naive UI 自己设了 flex: 0 0 auto。
   注：实测 footer 的类名是 .n-card__footer（不是 .n-card__action）。 */
.n-card.n-modal > .n-card__footer {
  flex-shrink: 0;
}

/* 全站滚动条已统一为 Naive UI 的 NScrollbar（5px 悬浮、跟随主题），
   原先那套 ::-webkit-scrollbar 规则已删除——项目里已无原生滚动容器。 */
</style>
