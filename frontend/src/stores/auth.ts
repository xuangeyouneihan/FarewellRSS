import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as greader from '@/api/greader'
import type { UserEntry } from '@/types/greader'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(greader.getToken())
  const username = ref<string | null>(null)
  const displayName = ref<string | null>(null)
  const isAdmin = ref(false)

  const isAuthenticated = computed(() => token.value !== null)

  async function login(email: string, password: string): Promise<void> {
    await greader.login(email, password)
    token.value = greader.getToken()
  }

  async function register(
    email: string,
    password: string,
    friendlyName?: string,
    inviteCode?: string,
  ): Promise<void> {
    await greader.register(email, password, friendlyName, inviteCode)
    token.value = greader.getToken()
  }

  async function fetchUserInfo(): Promise<void> {
    const info = await greader.getUserInfo()
    username.value = info.userId
    displayName.value = info.userName
    isAdmin.value = info.isAdmin
  }

  /** 修改昵称；null 表示清空（后端空值即置空） */
  async function updateProfile(friendlyName: string | null): Promise<void> {
    await greader.editProfile(friendlyName)
    displayName.value = friendlyName
  }

  async function changePassword(
    oldPassword: string,
    newPassword: string,
  ): Promise<void> {
    if (!username.value) throw new Error('未获取到用户名')
    await greader.changePassword(
      username.value,
      newPassword,
      username.value,
      oldPassword,
    )
  }

  async function deleteAccount(password: string): Promise<void> {
    if (!username.value) throw new Error('未获取到用户名')
    await greader.deleteAccount(username.value, username.value, password)
    greader.clearToken()
    token.value = null
    username.value = null
    displayName.value = null
    isAdmin.value = false
  }

  function logout(): void {
    greader.clearToken()
    token.value = null
    username.value = null
    displayName.value = null
    isAdmin.value = false
  }

  // ─── 管理员操作（操作者 = 当前登录管理员，需其密码确认） ──────────

  async function listUsers(): Promise<UserEntry[]> {
    const data = await greader.listUsers()
    return data.users
  }

  function _requireOperator(password: string): [string, string] {
    if (!username.value) throw new Error('未获取到用户名')
    return [username.value, password]
  }

  /** 管理员修改其他用户密码 */
  async function adminChangePassword(
    targetUsername: string,
    newPassword: string,
    operatorPassword: string,
  ): Promise<void> {
    const [opUser, opPw] = _requireOperator(operatorPassword)
    await greader.changePassword(targetUsername, newPassword, opUser, opPw)
  }

  /** 管理员设置/取消其他用户的管理员 */
  async function adminSetAdmin(
    targetUsername: string,
    isAdminValue: boolean,
    operatorPassword: string,
  ): Promise<void> {
    const [opUser, opPw] = _requireOperator(operatorPassword)
    await greader.setAdmin(targetUsername, isAdminValue, opUser, opPw)
  }

  /** 管理员删除其他用户账户 */
  async function adminDeleteAccount(
    targetUsername: string,
    operatorPassword: string,
  ): Promise<void> {
    const [opUser, opPw] = _requireOperator(operatorPassword)
    await greader.deleteAccount(targetUsername, opUser, opPw)
  }

  /** 管理员创建用户（无视注册开关/邀请码） */
  async function adminCreateUser(
    newUsername: string,
    newPassword: string,
    friendlyName: string | undefined,
    isAdminValue: boolean,
    operatorPassword: string,
  ): Promise<void> {
    const [opUser, opPw] = _requireOperator(operatorPassword)
    await greader.createUser(
      newUsername,
      newPassword,
      opUser,
      opPw,
      friendlyName,
      isAdminValue,
    )
  }

  return {
    token,
    username,
    displayName,
    isAdmin,
    isAuthenticated,
    login,
    register,
    fetchUserInfo,
    updateProfile,
    changePassword,
    deleteAccount,
    logout,
    listUsers,
    adminChangePassword,
    adminSetAdmin,
    adminDeleteAccount,
    adminCreateUser,
  }
})
