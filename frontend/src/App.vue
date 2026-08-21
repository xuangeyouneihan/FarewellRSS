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

/* 内容区可滚动；header 与 action（footer）自然收缩固定在上下 */
.n-card.n-modal > .n-card-content {
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.n-card.n-modal > .n-card-header,
.n-card.n-modal > .n-card__action {
  flex-shrink: 0;
}

/* 自定义滚动条（WebKit 系）。深色/浅色都用细滚动条 + 圆角半透明 thumb */
::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-thumb {
  border-radius: 5px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

/* 浅色：浅灰 thumb，与白色轨道协调 */
html[style*="color-scheme: light"] ::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.12);
}

html[style*="color-scheme: light"] ::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.24);
}

/* 深色：半透明白 */
html[style*="color-scheme: dark"] ::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.24);
}

html[style*="color-scheme: dark"] ::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.36);
}
</style>
