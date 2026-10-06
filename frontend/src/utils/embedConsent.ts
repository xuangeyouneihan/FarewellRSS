/**
 * 「这个源的内嵌内容可以直接加载」这个同意的记忆。
 *
 * 存在 localStorage：它是**设备上的偏好**（"我愿意在这台机器上执行这个源的内嵌内容"），
 * 不是账号数据，所以不进服务端 —— 换设备、清缓存会再问一次，这是可以接受甚至更好的行为。
 *
 * 注意同意只免掉「来源确认」那一步：**加载永远需要一次点击**，没有任何东西会自动跑起来。
 * 所以即使这条记录过期或串台（源被删了、id 被后来的源复用），后果也只是"少问一次"。
 */

const STORAGE_KEY = "farewell-rss.embed-consent";

/** 记满就丢最旧的：这是偏好缓存，不是数据库 */
const MAX_REMEMBERED = 200;

function read(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    // 隐私模式、存储被禁用、别人写过脏数据：当作没同意过，别把阅读器搞崩
    return [];
  }
}

function write(ids: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids.slice(-MAX_REMEMBERED)));
  } catch {
    // 写不进去就算了：下次再问一遍，功能不受影响
  }
}

/** 这个源是否已经同意过。`feedId` 用 `item.origin.streamId`，形如 `feed/12` */
export function hasEmbedConsent(feedId: string): boolean {
  if (feedId === "") return false;
  return read().includes(feedId);
}

/** 记住这个源的同意（重复调用只会把它挪到最新） */
export function rememberEmbedConsent(feedId: string): void {
  if (feedId === "") return;
  const ids = read().filter((id) => id !== feedId);
  ids.push(feedId);
  write(ids);
}
