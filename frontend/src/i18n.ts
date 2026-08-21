// vue-i18n 封装：跟随浏览器语言，不支持时回退简体中文
// 语言代码用 zh-Hans / en（不带地区后缀），文案在 src/locales/*.json（可直接喂给 Crowdin）

import { createI18n } from "vue-i18n";
import zhHans from "./locales/zh-Hans.json";
import en from "./locales/en.json";

export type Locale = "zh-Hans" | "en";

function detectLocale(): Locale {
  const lang = (navigator.language || "").toLowerCase();
  // 繁体（zh-Hant / zh-TW / zh-HK / zh-MO）暂不单独支持，走回退（简体）
  const isTraditional =
    lang.startsWith("zh-hant") ||
    ["zh-tw", "zh-hk", "zh-mo"].includes(lang);
  if (lang.startsWith("zh") && !isTraditional) return "zh-Hans";
  if (lang.startsWith("en")) return "en";
  return "zh-Hans"; // 回退简体中文
}

export const locale: Locale = detectLocale();

export const i18n = createI18n({
  legacy: false, // Composition API
  locale,
  fallbackLocale: "zh-Hans",
  messages: {
    "zh-Hans": zhHans,
    en,
  },
});

/** 全局取文案（组件外用，等价于 useI18n 的 t） */
export const t = i18n.global.t;
