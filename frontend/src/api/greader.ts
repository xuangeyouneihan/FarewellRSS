// Google Reader API 客户端封装
// 负责认证头、URL 拼接、响应解析，UI 层不直接碰 HTTP 细节

import type {
  ItemRefs,
  LabelType,
  QuickAddResult,
  StreamContents,
  StreamParams,
  SubscriptionList,
  TagList,
  UnreadCount,
  UserEntry,
  UserInfo,
} from '@/types/greader'

const appBase = window.location.pathname
  .replace(/\/(?:login|register)\/?$/, '/')
  .replace(/[^/]+$/, '')
const API_BASE = `${appBase}api`.replace(/\/\//g, '/')
const AUTH_BASE = `${API_BASE}/accounts`
const READER_BASE = `${API_BASE}/reader/api/0`

const TOKEN_KEY = 'farewell_rss_token'

// ─── token 管理 ─────────────────────────────────────────────────────────

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export function hasToken(): boolean {
  return getToken() !== null
}

// ─── 底层请求 ───────────────────────────────────────────────────────────

function authHeader(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `GoogleLogin auth=${token}` } : {}
}

/** 流 ID 按段编码，保留 / 分隔符 */
function encodeStreamId(streamId: string): string {
  return streamId.split('/').map(encodeURIComponent).join('/')
}

function toFormBody(data: Record<string, string | undefined>): URLSearchParams {
  const body = new URLSearchParams()
  for (const [key, value] of Object.entries(data)) {
    if (value !== undefined) body.append(key, value)
  }
  return body
}

async function request(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const response = await fetch(url, init)
  if (response.status === 401) {
    clearToken()
  }
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`请求失败 ${response.status}: ${url} — ${detail}`)
  }
  return response
}

// ─── 认证 ───────────────────────────────────────────────────────────────

/** 从 ClientLogin 响应文本解析 Auth token */
function parseAuthToken(text: string): string {
  for (const line of text.split('\n')) {
    if (line.startsWith('Auth=')) return line.slice(5).trim()
  }
  throw new Error(`无法解析认证响应: ${text}`)
}

export async function login(email: string, password: string): Promise<void> {
  const response = await request(`${AUTH_BASE}/ClientLogin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      Email: email,
      Passwd: password,
      accountType: 'GOOGLE',
      service: 'reader',
    }),
  })
  setToken(parseAuthToken(await response.text()))
}

export async function register(
  email: string,
  password: string,
  friendlyName?: string,
  inviteCode?: string,
): Promise<void> {
  const response = await request(`${AUTH_BASE}/ClientRegister`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      Email: email,
      Passwd: password,
      friendly_name: friendlyName,
      invite_code: inviteCode,
    }),
  })
  setToken(parseAuthToken(await response.text()))
}

export async function getUserInfo(): Promise<UserInfo> {
  const response = await request(`${READER_BASE}/user-info`, {
    headers: authHeader(),
  })
  return response.json()
}

export async function changePassword(
  username: string,
  newPassword: string,
  operatorUsername: string,
  operatorPassword: string,
): Promise<void> {
  await request(`${AUTH_BASE}/ChangePassword`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      username,
      new_password: newPassword,
      operator_username: operatorUsername,
      operator_password: operatorPassword,
    }),
  })
}

/** 修改个人资料（当前仅限昵称）。后端语义：空/缺省 → 对应项置空 */
export async function editProfile(friendlyName: string | null): Promise<void> {
  await request(`${AUTH_BASE}/EditProfile`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    // 显式发空串（而不是省略字段），清空意图更明确
    body: toFormBody({ friendly_name: friendlyName ?? '' }),
  })
}

/** 列出所有用户（仅管理员） */
export async function listUsers(): Promise<{ users: UserEntry[] }> {
  const response = await request(`${AUTH_BASE}/ListUsers`, {
    headers: authHeader(),
  })
  return response.json()
}

/** 设置/取消管理员（仅管理员，需操作者密码） */
export async function setAdmin(
  username: string,
  isAdmin: boolean,
  operatorUsername: string,
  operatorPassword: string,
): Promise<void> {
  await request(`${AUTH_BASE}/SetAdmin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      username,
      is_admin: String(isAdmin),
      operator_username: operatorUsername,
      operator_password: operatorPassword,
    }),
  })
}

/** 管理员创建用户（无视注册开关/邀请码） */
export async function createUser(
  username: string,
  password: string,
  operatorUsername: string,
  operatorPassword: string,
  friendlyName?: string,
  isAdmin?: boolean,
): Promise<void> {
  await request(`${AUTH_BASE}/CreateUser`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      username,
      password,
      friendly_name: friendlyName,
      is_admin: isAdmin !== undefined ? String(isAdmin) : undefined,
      operator_username: operatorUsername,
      operator_password: operatorPassword,
    }),
  })
}

export async function deleteAccount(
  username: string,
  operatorUsername: string,
  operatorPassword: string,
): Promise<void> {
  await request(`${AUTH_BASE}/DeleteAccount`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: toFormBody({
      username,
      operator_username: operatorUsername,
      operator_password: operatorPassword,
    }),
  })
}

// ─── 订阅 ───────────────────────────────────────────────────────────────

export async function getSubscriptions(): Promise<SubscriptionList> {
  const response = await request(`${READER_BASE}/subscription/list`, {
    headers: authHeader(),
  })
  return response.json()
}

export async function quickAdd(feedUrl: string): Promise<QuickAddResult> {
  const response = await request(`${READER_BASE}/subscription/quickadd`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: toFormBody({ quickadd: feedUrl }),
  })
  return response.json()
}

/** 导入 OPML（后端接收原始 XML 请求体） */
export async function importOpml(file: File): Promise<void> {
  await request(`${READER_BASE}/subscription/import`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': file.type || 'application/xml',
    },
    body: file,
  })
}

/** 导出 OPML，返回文件内容与后端建议的文件名 */
export async function exportOpml(): Promise<{
  blob: Blob
  filename: string
}> {
  const response = await request(`${READER_BASE}/subscription/export`, {
    headers: authHeader(),
  })
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encodedFilename = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const filename = encodedFilename
    ? decodeURIComponent(encodedFilename)
    : (disposition.match(/filename="?([^";]+)"?/i)?.[1] ??
      'subscriptions.opml')
  return { blob: await response.blob(), filename }
}

export type SubscriptionAction = 'subscribe' | 'unsubscribe' | 'edit'

export interface SubscriptionEditOptions {
  title?: string
  addFolder?: string
  removeFolder?: string
}

/** 订阅管理（退订 / 归类到文件夹 / 重命名标题） */
export async function editSubscription(
  action: SubscriptionAction,
  streamIds: string[],
  options: SubscriptionEditOptions = {},
): Promise<void> {
  const body = new URLSearchParams()
  body.append('ac', action)
  for (const id of streamIds) body.append('s', id)
  if (options.title !== undefined) body.append('t', options.title)
  if (options.addFolder !== undefined)
    body.append('a', `user/-/label/${options.addFolder}`)
  if (options.removeFolder !== undefined)
    body.append('r', `user/-/label/${options.removeFolder}`)
  await request(`${READER_BASE}/subscription/edit`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })
}

// ─── 标签 ───────────────────────────────────────────────────────────────

export async function getTags(): Promise<TagList> {
  const response = await request(`${READER_BASE}/tag/list`, {
    headers: authHeader(),
  })
  return response.json()
}

export async function enableTag(
  names: string[],
  types?: LabelType[],
): Promise<void> {
  const body = new URLSearchParams()
  for (const name of names) body.append('s', `user/-/label/${name}`)
  for (const type of types ?? []) body.append('type', type)
  await request(`${READER_BASE}/enable-tag`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })
}

export async function renameTag(
  names: string[],
  dest: string[],
  types: LabelType[],
): Promise<void> {
  const body = new URLSearchParams()
  for (const name of names) body.append('s', `user/-/label/${name}`)
  for (const name of dest) body.append('dest', `user/-/label/${name}`)
  for (const type of types) body.append('type', type)
  await request(`${READER_BASE}/rename-tag`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })
}

export async function disableTag(
  names: string[],
  types: LabelType[],
): Promise<void> {
  const body = new URLSearchParams()
  for (const name of names) body.append('s', `user/-/label/${name}`)
  for (const type of types) body.append('type', type)
  await request(`${READER_BASE}/disable-tag`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })
}

// ─── 流 ────────────────────────────────────────────────────────────────

function toQueryString(params: StreamParams): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.append(key, String(value))
  }
  const s = query.toString()
  return s ? `?${s}` : ''
}

export async function getStreamContents(
  streamId: string,
  params: StreamParams = {},
): Promise<StreamContents> {
  const response = await request(
    `${READER_BASE}/stream/contents/${encodeStreamId(streamId)}${toQueryString(params)}`,
    { headers: authHeader() },
  )
  return response.json()
}

export async function getStreamItemIds(
  streamId: string,
  params: StreamParams = {},
): Promise<ItemRefs> {
  const query = new URLSearchParams({ s: streamId })
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.append(key, String(value))
  }
  const qs = query.toString()
  const response = await request(
    `${READER_BASE}/stream/items/ids${qs ? `?${qs}` : ''}`,
    { headers: authHeader() },
  )
  return response.json()
}

export async function getUnreadCount(): Promise<UnreadCount> {
  const response = await request(`${READER_BASE}/unread-count`, {
    headers: authHeader(),
  })
  return response.json()
}

// ─── 状态操作 ───────────────────────────────────────────────────────────

/** 批量添加/移除标签（收藏、已读、收藏夹归类都走这里） */
export async function editTag(
  itemIds: string[],
  add?: string[],
  remove?: string[],
): Promise<void> {
  const body = new URLSearchParams()
  for (const id of itemIds) body.append('i', id)
  for (const label of add ?? []) body.append('a', label)
  for (const label of remove ?? []) body.append('r', label)
  await request(`${READER_BASE}/edit-tag`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  })
}

export async function markAllAsRead(
  streamId: string,
  type?: LabelType,
): Promise<void> {
  await request(`${READER_BASE}/mark-all-as-read`, {
    method: 'POST',
    headers: {
      ...authHeader(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: toFormBody({ s: streamId, type }),
  })
}
