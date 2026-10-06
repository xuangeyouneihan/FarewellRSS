// 正文里的章节链接（播客 show notes 的时间线）。
//
// 播客的 show notes 常见写法是 `<li><a href="#t=00:05:18">00:05:18</a> 发布</li>`，点了应该把
// 播放器跳过去。两个必须记着的坑（都是实测出来的）：
//
// 1. **入库时链接已经被补成绝对地址了**。feedparser 按 `content-location` 解析相对链接，源站的
//    `#t=00:05:18` 到我们手里已经是 `https://…/feed/audio.xml#t=00:05:18`（牛油果烤面包的源
//    实测如此）。所以判据是**片段里有没有 `t=`**，不是"href 是不是以 # 开头"——后者恒为假。
// 2. **不能让它真的跳**。我们是 hash 路由，浏览器跟着 `…#t=00:05:18` 走会顶掉 `#/…`、匹配不上
//    任何路由（`router/index.ts` 有意没有 catch-all）→ 整页空白，连文章都没了。所以命中的
//    链接一律 `preventDefault()`，跳转交给播放器。

/** 章节时间点的片段语法（媒体片段）：`t=00:05:18` / `t=318` / `t=318.5` */
const CHAPTER_FRAGMENT = /^t=([\d:.]+)$/;

/**
 * 章节链接的时间点（秒）；**不是**章节链接返回 `null`。
 *
 * 只看片段，不看地址本身 —— 理由见文件头第 1 条。
 */
export function chapterSeconds(href: string): number | null {
  const index = href.indexOf("#");
  if (index === -1) return null;
  const clock = CHAPTER_FRAGMENT.exec(href.slice(index + 1))?.[1] ?? null;
  return clock === null ? null : secondsOf(clock);
}

/**
 * `#foo` 这种"只在本文档内跳"的锚点（地址部分是空的）。
 *
 * 拦它是因为**一样会打空白页**：hash 路由下 `#foo` 会顶掉 `#/…`。正常入库的正文里不该出现
 * （feedparser 会按源的地址补成绝对地址，见文件头），但判断它只要一次字符串比较，而漏掉的
 * 代价是整页空白 —— 所以留着当兜底。
 */
export function isBareFragment(href: string): boolean {
  const trimmed = href.trim();
  return trimmed.startsWith("#") && trimmed.length > 1;
}

/**
 * 正文里第一个能播的元素。
 *
 * 取第一个就够：一集播客只挂一个音频；后端把播放器拼在正文**末尾**（见
 * `src/farewell_rss/api/_enclosures.py`），源站自己在正文里放播放器的情况下也是它排在前。
 */
export function findPlayer(root: ParentNode | null): HTMLMediaElement | null {
  return root?.querySelector<HTMLMediaElement>("audio, video") ?? null;
}

/**
 * 跳到指定秒数并开始播放。
 *
 * 播放器是后端拼的、带 `preload="none"`，所以点链接的那一刻 **`readyState` 正常还是 0**：
 * 按规范这时给 `currentTime` 赋值会被记成"默认起播位置"，但实现未必照做，所以再挂一次
 * `loadedmetadata` 兜底 —— 两次赋的都是同一个值，谁先生效都对（不会跳两下）。
 */
export function seekTo(player: HTMLMediaElement, seconds: number): void {
  player.currentTime = seconds;
  if (player.readyState === 0) {
    player.addEventListener(
      "loadedmetadata",
      () => {
        player.currentTime = seconds;
      },
      { once: true },
    );
  }

  // 不直接 `.catch()`：单测环境的 jsdom 没实现 play()，它返回 undefined（规范里是 promise）。
  const started = player.play() as Promise<void> | undefined;
  if (started && typeof started.catch === "function") {
    // 自动播放被拦不是什么异常情况（多的是浏览器要求"用户手势"）：没自动播而已，
    // 播放键照样能按。这里只是不让它变成一个未处理的 rejection。
    void started.catch(() => undefined);
  }
}

/** `HH:MM:SS` / `MM:SS` / `SS`（最后一段可带小数）→ 秒；格式不对返回 `null` */
function secondsOf(clock: string): number | null {
  const parts = clock.split(":");
  // 最多三段（`1:2:3:4` 不是时间点）；段内非数字（`t=1::2`）也判非法 —— 宁可不动，
  // 也不要静默跳到 0 秒。
  if (parts.length > 3) return null;

  let total = 0;
  for (const part of parts) {
    if (!/^\d+(?:\.\d+)?$/.test(part)) return null;
    total = total * 60 + Number(part);
  }
  return total;
}
