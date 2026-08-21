<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NInput, NModal, useMessage } from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { t } from "@/i18n";

defineOptions({ name: "UserProfileModal" });

const auth = useAuthStore();
const router = useRouter();
const message = useMessage();

const show = ref(false);

// 修改密码
const oldPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const changing = ref(false);

// 删除账户
const confirmUsername = ref("");
const deletePassword = ref("");
const deleting = ref(false);

const username = computed(() => auth.username);
const displayName = computed(() => auth.displayName ?? t("noNickname"));

// 昵称行内编辑
const editingName = ref(false);
const nameInput = ref("");
const savingName = ref(false);

function startEditName(): void {
  nameInput.value = auth.displayName ?? "";
  editingName.value = true;
}

async function saveName(): Promise<void> {
  const value = nameInput.value.trim();
  savingName.value = true;
  try {
    // 空串 → null（后端语义：空值置空昵称）
    await auth.updateProfile(value || null);
    message.success(t("nicknameUpdated"));
    editingName.value = false;
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("changeNicknameFailed"));
  } finally {
    savingName.value = false;
  }
}

// NInput 的 onKeydown 只接受单个函数，Enter/Esc 合到一个处理器里
function onNameKeydown(e: KeyboardEvent): void {
  if (e.key === "Enter") void saveName();
  else if (e.key === "Escape") editingName.value = false;
}

const canDelete = computed(
  () =>
    username.value !== null &&
    confirmUsername.value === username.value &&
    deletePassword.value.length > 0,
);

function open(): void {
  show.value = true;
  oldPassword.value = "";
  newPassword.value = "";
  confirmPassword.value = "";
  confirmUsername.value = "";
  deletePassword.value = "";
  editingName.value = false;
}

async function changePassword(): Promise<void> {
  if (!oldPassword.value || !newPassword.value) {
    message.warning(t("inputOldNewPassword"));
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    message.warning(t("newPasswordMismatch"));
    return;
  }
  changing.value = true;
  try {
    await auth.changePassword(oldPassword.value, newPassword.value);
    message.success(t("passwordChanged"));
    oldPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("changePasswordFailed"));
  } finally {
    changing.value = false;
  }
}

async function deleteAccount(): Promise<void> {
  if (!username.value) return;
  deleting.value = true;
  try {
    await auth.deleteAccount(deletePassword.value);
    message.success(t("accountDeleted"));
    show.value = false;
    await router.push({ name: "login" });
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("deleteAccountFailed"));
  } finally {
    deleting.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <n-modal v-model:show="show" preset="card" :title="t('profile')" style="width: 420px">
    <div class="profile">
      <div class="username nickname-row">
        <template v-if="!editingName">
          <span>{{ t("nickname") }}{{ t("colon") }}{{ displayName }}</span>
          <n-button text size="tiny" class="edit-name-btn" @click="startEditName"> ✏️ </n-button>
        </template>
        <template v-else>
          <span>{{ t("nickname") }}{{ t("colon") }}</span>
          <n-input
            v-model:value="nameInput"
            size="small"
            class="name-input"
            :placeholder="t('nicknameClearedPlaceholder')"
            @keydown="onNameKeydown"
          />
          <n-button size="tiny" type="primary" :loading="savingName" @click="saveName">
            {{ t("ok") }}
          </n-button>
          <n-button size="tiny" quaternary @click="editingName = false"> {{ t("cancel") }} </n-button>
        </template>
      </div>
      <div class="username">{{ t("username") }}{{ t("colon") }}{{ username ?? "—" }}</div>

      <div class="section">
        <h4 class="section-title">{{ t("changePassword") }}</h4>
        <n-input
          v-model:value="oldPassword"
          type="password"
          show-password-on="click"
          :placeholder="t('oldPassword')"
          @keydown.enter="changePassword"
        />
        <n-input
          v-model:value="newPassword"
          type="password"
          show-password-on="click"
          :placeholder="t('newPassword')"
          @keydown.enter="changePassword"
        />
        <n-input
          v-model:value="confirmPassword"
          type="password"
          show-password-on="click"
          :placeholder="t('confirmNewPassword')"
          @keydown.enter="changePassword"
        />
        <n-button
          size="small"
          type="primary"
          :loading="changing"
          @click="changePassword"
        >
          {{ t("confirm") }}
        </n-button>
      </div>

      <div class="section danger">
        <h4 class="section-title">{{ t("deleteAccount") }}</h4>
        <p class="tip">{{ t("deleteAccountTip") }}</p>
        <n-input v-model:value="confirmUsername" :placeholder="t('inputUsernameConfirm')" />
        <n-input
          v-model:value="deletePassword"
          type="password"
          show-password-on="click"
          :placeholder="t('inputPasswordConfirm')"
          @keydown.enter="deleteAccount"
        />
        <n-button
          size="small"
          type="error"
          :disabled="!canDelete"
          :loading="deleting"
          @click="deleteAccount"
        >
          {{ t("confirmDelete") }}
        </n-button>
      </div>
    </div>
  </n-modal>
</template>

<style scoped>
.profile {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.username {
  font-size: 14px;
  color: var(--app-text-2);
}

.nickname-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.edit-name-btn {
  font-size: 12px;
}

.name-input {
  flex: 1;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--app-divider);
}

.section:first-of-type {
  padding-top: 0;
  border-top: none;
}

.section-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-1);
}

.danger .section-title {
  color: var(--app-error);
}

.tip {
  margin: 0;
  font-size: 12px;
  color: var(--app-error);
}
</style>
