// 响应式断点 + 触屏检测
// 布局按屏幕宽度分三档；触屏优化额外看 UA/指针类型

import { computed, onMounted, onUnmounted, ref } from "vue";

export type Breakpoint = "phone" | "tablet" | "desktop";

const width = ref(typeof window !== "undefined" ? window.innerWidth : 1280);

function onResize(): void {
  width.value = window.innerWidth;
}

/** 当前断点：<768 手机，768-1024 平板，>1024 桌面 */
export function useBreakpoint() {
  onMounted(() => window.addEventListener("resize", onResize));
  onUnmounted(() => window.removeEventListener("resize", onResize));

  const breakpoint = computed<Breakpoint>(() => {
    if (width.value < 768) return "phone";
    if (width.value <= 1024) return "tablet";
    return "desktop";
  });
  const isPhone = computed(() => breakpoint.value === "phone");
  const isTablet = computed(() => breakpoint.value === "tablet");
  const isDesktop = computed(() => breakpoint.value === "desktop");
  // 需要紧凑布局（手机或平板）
  const isCompact = computed(() => breakpoint.value !== "desktop");

  return { breakpoint, isPhone, isTablet, isDesktop, isCompact, width };
}

/** 是否触屏设备（UA 移动设备 或 粗指针） */
export const isTouchDevice = (() => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent.toLowerCase();
  const mobileUA = /android|iphone|ipod|ipad|mobile|windows phone/.test(ua);
  const coarsePointer =
    typeof window !== "undefined" &&
    window.matchMedia?.("(pointer: coarse)").matches;
  return mobileUA || !!coarsePointer;
})();
