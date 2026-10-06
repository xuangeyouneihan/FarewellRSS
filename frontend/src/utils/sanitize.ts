// 文章正文的消毒 + 内嵌内容的处理。
//
// 后端是原样存、原样发正文的（别的 GReader 客户端自己决定怎么处理），所以消毒只有这一层，
// 而这一层很关键：`v-html` 插进来的是**我们自己 origin 的文档** —— 登录态、cookie、
// localStorage 就在旁边，这里漏一点就是真漏洞。
//
// 参考同类实现：NetNewsWire 和 read you 都不做白名单式消毒，它们靠的是"宿主沙盒"
// （WKWebView 的临时存储、导航全外抛、只暴露一个极小的 JS 桥）。我们在浏览器里拿不到那些
// 能力，所以两头收紧：能过滤的过滤掉；过滤不掉的（iframe）**一律换成占位器** ——
// 点击才加载，而且第一次加载前先把"要去哪个域名"问一次（见 ArticleView 的点击委托）。
//
// 不再对 iframe 做域名白名单：那样只能覆盖我预先想到的站，遇到没收录的就**静默丢掉**
// （这正是本模块最早踩的坑：B 站播放器无声无息消失、日志里一个字都没有）。现在的做法是
// 全都交给你：看到占位器、看到域名、你自己决定点不点。

import DOMPurify from "dompurify";

export type EmbedLabels = {
  /** 占位器主按钮文案，例如「点击加载内嵌内容」 */
  load: string;
  /** 「在新窗口打开」文案 */
  open: string;
};



/**
 * 内嵌内容默认不带 Referer。
 *
 * 为什么不带（而不是像 NetNewsWire 那样给一个第三方域名）：
 *
 * - Referer 由浏览器**根据文档 URL 生成**，页面没有任何 API 能覆盖它（`referrerpolicy`、
 *   `<meta name="referrer">`、CSP 的 `referrer` 指令都只能调"送多少"，不能改"送谁"）。
 *   NNW 能造出 `netnewswire.com` 是因为它是原生 App：`loadHTMLString(html, baseURL:)`
 *   直接把文档 URL 设成了自己的官网。我们在浏览器里没有这个能力。
 * - 所以我们只能在"送我们的域名"和"什么都不送"之间选。而本项目的实例都是自部署的，
 *   域名/端口千奇百怪、外面没人认得 —— 送过去对面既不会因此信任我们（换不来播放），
 *   又白白把"哪个实例在看这个视频"这条身份信息交了出去（只挂一个自用实例的域名，
 *   `origin` 本身就是身份，跟送不送路径无关）。
 * - 实测 B 站播放器对 Referer 不敏感（带不带都能播），所以目前没有站点需要例外；
 *   真碰上了再在这里开一个逐站的名单，别一上来就送。
 * - 另外记着：`no-referrer` 只是不送 Referer，对端依旧能按 IP 认出你。
 */
const DEFAULT_EMBED_REFERRER_POLICY = "no-referrer";

/**
 * 正文里的**图片**一律不带 Referer（这是写上去的，不是求源站给）。
 *
 * 实测（B 站图床 `https://i0.hdslb.com/bfs/archive/….jpg`）：
 *
 * | 请求时带的 Referer | 结果 |
 * | --- | --- |
 * | 不带 | 200（94 KB jpeg） |
 * | 我们的域名（= 浏览器跳站的默认策略） | **403** |
 * | 它自己的域名 | 200 |
 *
 * 也就是“第三方 Referer 一律拒绝”的图床很常见。而经典的防盗链配置恰恰**允许空 Referer**
 * （Apache 那条 `RewriteCond %{HTTP_REFERER} !^$` 就是“只拦第三方、放行直接访问”）——
 * 所以“不送”比“送我们的域名”兼容面更大，还顺带少泄漏实例域名。
 *
 * 注意 RSSHub 自己就给图片写了 `referrerpolicy="no-referrer"`，但这个属性会被**两层剥掉**：
 * feedparser 的入库白名单没有它，DOMPurify 的默认属性白名单也没有它。所以我们自己写，
 * 不指望源站带；写在我们的钩子里（在 DOMPurify 消完属性之后），两道都能盖住。
 */
const IMAGE_REFERRER_POLICY = "no-referrer";

/**
 * 会跑到正文框外面去的 inline style 属性：绝对定位能压住标题、飘到页头上去
 * （NetNewsWire 的 release note 里专门修过这个）。只删这几个，**不整个禁掉 `style`
 * 属性** —— 源站常用它控制图片宽度，全剥会让正常排版变形。
 */
const HARMFUL_STYLE_PROPERTIES = [
  "position",
  "top",
  "right",
  "bottom",
  "left",
  "inset",
  "inset-block",
  "inset-inline",
  "z-index",
];

/** 标签层面直接禁掉的东西，理由见下面各条 */
const FORBID_TAGS = [
  // 能改我们**整篇**页面的样式（v-html 插入的 <style> 作用域是整个文档，不是它所在的那段）
  "style",
  // 能在阅读器里做一个假登录页，把你的密码 POST 到别处
  "form",
  "input",
  "button",
  "select",
  "textarea",
  "option",
  "optgroup",
  "label",
];

// 占位器里的外链要能开新窗口；DOMPurify 默认会剥掉 target
const ADD_ATTR = ["target"];

/**
 * 消毒文章正文，并把内嵌播放器换成"点击加载"的占位器。
 *
 * 两遍消毒是刻意的：第一遍`ADD_TAGS`放行 iframe 才能看到它，第二遍用默认配置（不放行
 * iframe）再消一次 —— 于是"输出里绝不含 iframe"变成结构性保证，第一遍的钩子万一漏了某种
 * 情况（遍历顺序、奇怪的大小写与嵌套），第二遍也会删掉。
 */
export function sanitizeArticleHtml(html: string, labels: EmbedLabels): string {
  if (!html) return "";

  const onElement = (node: Node) => {
    if (node.nodeType !== 1) return; // 1 = ELEMENT_NODE
    const element = node as Element;
    if (element.tagName !== "IFRAME") return;
    replaceIframe(element, labels);
  };

  // `nodeType` 判断而不是 `instanceof Element`：DOMPurify 在它自己的沙箱文档里操作，
  // 跨文档时 instanceof 会失效。
  DOMPurify.addHook("afterSanitizeElements", onElement);
  DOMPurify.addHook("afterSanitizeAttributes", sanitizeAttributes);
  try {
    const first = DOMPurify.sanitize(html, {
      ADD_TAGS: ["iframe"],
      ADD_ATTR,
      FORBID_TAGS,
    });
    return DOMPurify.sanitize(first, { ADD_ATTR, FORBID_TAGS });
  } finally {
    DOMPurify.removeHook("afterSanitizeElements", onElement);
    DOMPurify.removeHook("afterSanitizeAttributes", sanitizeAttributes);
  }
}

/**
 * 由我们构造要插入的 iframe。**点击时再校验一次地址** —— 占位器的 `data-embed` 是能被
 * 源站伪造的（正文里任何 HTML 都可能出现），校验不过就什么都不插。
 *
 * 注意 ratio 只用于 CSS，别把它当可信输入。
 */
export function createEmbedFrame(url: string, ratio: string): HTMLIFrameElement | null {
  const target = playableUrl(url);
  if (!target) return null;

  const frame = document.createElement("iframe");
  frame.src = target;
  frame.className = "embed-frame";
  frame.setAttribute("allowfullscreen", "true");
  frame.setAttribute("referrerpolicy", DEFAULT_EMBED_REFERRER_POLICY);
  frame.setAttribute("loading", "lazy");
  // 只给宽高比、不给固定高度：源站的 640×360 在窄屏上会溢出，而只靠 max-width:100%
  // 压扁又会让高度留在原处（变形，且加载失败时是一大块空白）—— NetNewsWire 专门修过。
  frame.style.aspectRatio = /^\d+(?:\.\d+)? \/ \d+(?:\.\d+)?$/.test(ratio) ? ratio : DEFAULT_RATIO;
  frame.style.width = "100%";
  frame.style.height = "auto";
  frame.style.border = "0";
  return frame;
}

/**
 * 能当占位器加载的地址：绝对的 http(s)、不带凭据。**不做任何改写** —— 源站给什么用什么。
 *
 * 这里**不做域名白名单**：任何站点都可能被内嵌，加载与否由用户点那一下 + 第一次的
 * 来源确认决定。所以它只保证"是个能塞进 iframe 的正常地址"，不保证"对面肯被嵌入" ——
 * 那是浏览器和对面的事，真不行的话那一行里还有「在新窗口打开」。
 *
 * 【曾经有过一条 B 站改写，已删——这里记一下过程，免得以后重走一遍】
 *
 * 那时把源站给的 `www.bilibili.com/blackboard/html5mobileplayer.html` 改写成
 * `player.bilibili.com/player.html`，依据是"桌面 Edge 里手机端页面是一片黑、只有 legacy 能播"。
 * 后来查清：**那片黑是浏览器插件造成的**（关掉插件后手机端页面在 Edge 桌面里正常播，
 * 显示的只是移动端 UI）。那批对照实验全都是带插件跑的，结论从一开始就是错的。
 *
 * 关插件复测的结果（6 个环境，同一个视频）：
 *
 * | 环境 | 手机端页面 | legacy |
 * | --- | --- | --- |
 * | Edge 桌面 | ✓（移动端 UI） | ✓（桌面端 UI） |
 * | Firefox 桌面 | ✓ | ✓ |
 * | Chrome 安卓 | ✓ | ✓ |
 * | Via（WebView 套壳） | ✓ | ✓ |
 * | Firefox 安卓 | ✓ | ✗ |
 * | VS Code 内置浏览器 | ✓ | ✗ |
 *
 * 也就是说：**源站给的那个手机端页面 6/6 全通，而 legacy 有两格放不出来**（媒体栈不支持它
 * 要的那套）。所以不用猜、也不做"按设备挑播放器"—— 那样在桌面能拿到更好看的桌面 UI，
 * 但会把"处处能播"换成"多数地方更好看、个别环境黑框"，不划算。
 *
 * **教训：测"能不能播"之前先关插件。** 带广告拦截插件的环境里出现黑框，可能是插件干的，
 * 别当成播放器的问题去改代码。
 *
 * 以后真碰到"源给的地址在浏览器里出不来"（不是插件），再在这里加一张"地址修复"表，
 * 验收流程是：
 *   1. 关掉插件，浏览器里直接打开那个地址 → 能播吗？
 *   2. 塞进 `<iframe>` 里 → 能播吗？
 *   3. 两步都过 → 什么都不用做；第 2 步不过但你知道正确地址 → 按宿主 + 路径加一条改写，
 *      并补一条 `playableUrl` 断言。（响应头看不出来：B 站这几个地址响应头全都正常。）
 */
export function playableUrl(raw: string): string | null {
  return parseFrameUrl(raw)?.href ?? null;
}

const DEFAULT_RATIO = "16 / 9";

/**
 * 只接受绝对的 http(s) 地址，并且拒绝带凭据的（占位器要把域名显示给人看，凭据只会误导）。
 *
 * `//host/path` 这种协议相对写法按 https 解析（源站本来就该给 https）；而显式的 `http://`
 * **不**硬升成 https —— 我们不知道对面支不支持，升错了会直接把能用的地址打死。
 */
function parseFrameUrl(raw: string): URL | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const withScheme = trimmed.startsWith("//") ? `https:${trimmed}` : trimmed;
  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (parsed.username !== "" || parsed.password !== "") return null;
  return parsed;
}

function replaceIframe(node: Element, labels: EmbedLabels): void {
  const doc = node.ownerDocument;
  const parent = node.parentNode;
  if (!doc || !parent) return;

  const placeholder = buildPlaceholder(node, labels, doc);
  if (!placeholder) {
    // 隐藏的、或地址根本不可用（javascript: / data: / 带凭据…）：整块删掉
    node.remove();
    return;
  }
  parent.replaceChild(placeholder, node);
}

function buildPlaceholder(
  node: Element,
  labels: EmbedLabels,
  doc: Document,
): HTMLElement | null {
  if (isHiddenFrame(node)) return null;
  const url = playableUrl(node.getAttribute("src") ?? "");
  if (!url) return null;
  return createEmbedPlaceholder(url, ratioOf(node), labels, doc);
}

/**
 * 生成占位器。sanitize 时插入的、以及用户点「取消」后恢复的，用的都是它 —— 保证两处不会漂。
 *
 * 全程 `createElement` + `textContent`，**不拼 HTML 字符串**：这样就不存在转义漏一格的问题。
 * 主按钮做成 `<a>` 而不是 `<span>`：键盘能聚焦、中键/右键「在新窗口打开」照样工作；左键与
 * 回车由 ArticleView 的点击委托接管，改成就地加载。
 */
function createEmbedPlaceholder(
  url: string,
  ratio: string,
  labels: EmbedLabels,
  doc: Document,
): HTMLElement {
  const wrapper = doc.createElement("div");
  wrapper.className = "embed";
  wrapper.dataset.embed = url;
  wrapper.dataset.embedRatio = ratio;
  wrapper.append(
    buildLink(doc, "embed-load", `▶ ${labels.load}`, url),
    hostLabel(doc, url),
    buildLink(doc, "embed-open", labels.open, url),
  );
  return wrapper;
}

function buildLink(doc: Document, className: string, text: string, url: string): HTMLAnchorElement {
  const link = doc.createElement("a");
  link.className = className;
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = text;
  return link;
}

function hostLabel(doc: Document, url: string): HTMLSpanElement {
  const host = doc.createElement("span");
  host.className = "embed-host";
  // url 已在 playableUrl 里验过，new URL 不会抛
  host.textContent = new URL(url).host;
  return host;
}

/** 来源确认行要用的文案（`message` 里的域名由调用方现算，见下） */
export type EmbedConfirmLabels = {
  /** 例如「将从 player.bilibili.com 加载第三方内容」——域名已拼好 */
  message: string;
  /** 「允许并加载」 */
  allow: string;
  /** 「取消」 */
  cancel: string;
};

/**
 * 首次加载前的来源确认行。
 *
 * 它替换掉占位器，点「取消」时由调用方把原来的占位器换回来（所以这里不重建占位器，
 * 两处状态就不会漂）。
 *
 * 关键一点：**要去的域名不在这个 DOM 里，而是调用方从已验证的 url 现算的**。占位器
 * （连同它显示的文字）是能被源站伪造的 —— 伪造一个写着 `player.bilibili.com` 的占位器
 * 而实际指向别处，正是这个确认行要拦的东西，所以它只信任自己算出来的 host。
 */
export function createEmbedConfirm(
  url: string,
  ratio: string,
  labels: EmbedConfirmLabels,
  doc: Document = document,
): HTMLElement {
  const wrapper = doc.createElement("div");
  wrapper.className = "embed embed-confirm";
  wrapper.dataset.embed = url;
  wrapper.dataset.embedRatio = ratio;

  const message = doc.createElement("span");
  message.className = "embed-confirm-text";
  message.textContent = labels.message;

  const allow = doc.createElement("button");
  allow.type = "button";
  allow.className = "embed-allow";
  allow.textContent = labels.allow;

  const cancel = doc.createElement("button");
  cancel.type = "button";
  cancel.className = "embed-cancel";
  cancel.textContent = labels.cancel;

  wrapper.append(message, allow, cancel);
  return wrapper;
}

/**
 * 隐藏的 iframe 直接删掉，不留链接也不留占位器：跟踪像素和 GTM 那种
 * `<iframe height="0" width="0" style="display:none">` 到处都是，做成可见的一行就是噪音。
 * （NetNewsWire 的 `wrapFrames` 里也有同样一判断。）
 */
function isHiddenFrame(node: Element): boolean {
  if (node.hasAttribute("hidden")) return true;
  const style = (node.getAttribute("style") ?? "").toLowerCase().replace(/\s+/g, "");
  if (style.includes("display:none") || style.includes("visibility:hidden")) return true;

  const isZero = (value: string | null) => value !== null && /^0(?:px|%)?$/.test(value.trim().toLowerCase());
  return isZero(node.getAttribute("width")) && isZero(node.getAttribute("height"));
}

/** 从原始的 width/height 取宽高比，取不到就用 16:9 */
function ratioOf(node: Element): string {
  const width = Number(node.getAttribute("width"));
  const height = Number(node.getAttribute("height"));
  const ratio = width / height;
  if (
    Number.isFinite(ratio) &&
    width > 0 &&
    height > 0 &&
    ratio >= 0.2 &&
    ratio <= 5
  ) {
    return `${width} / ${height}`;
  }
  return DEFAULT_RATIO;
}

function sanitizeAttributes(node: Node): void {
  if (node.nodeType !== 1) return;
  const element = node as Element;
  stripHarmfulInlineStyles(element);
  // 图片一律不带 Referer，不管是源站带的还是我们补的 —— 理由见 IMAGE_REFERRER_POLICY
  if (element.tagName === "IMG") {
    element.setAttribute("referrerpolicy", IMAGE_REFERRER_POLICY);
  }
}

function stripHarmfulInlineStyles(element: Element): void {
  const style = element.getAttribute("style");
  if (!style) return;

  const kept = style
    .split(";")
    .map((declaration) => declaration.trim())
    .filter((declaration) => {
      if (declaration === "") return false;
      const colon = declaration.indexOf(":");
      const name = (colon === -1 ? declaration : declaration.slice(0, colon)).trim().toLowerCase();
      return !HARMFUL_STYLE_PROPERTIES.includes(name);
    });

  if (kept.length === 0) element.removeAttribute("style");
  else element.setAttribute("style", kept.join("; "));
}
