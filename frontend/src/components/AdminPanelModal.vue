<script setup lang="ts">
import { h, ref, watch } from "vue";
import { NButton, NCheckbox, NInput, NModal, useDialog, useMessage } from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import type { UserEntry } from "@/types/greader";
import { t } from "@/i18n";

defineOptions({ name: "AdminPanelModal" });

const auth = useAuthStore();
const message = useMessage();
const dialog = useDialog();

const show = ref(false);

const adminUsers = ref<UserEntry[]>([]);
const adminLoading = ref(false);

// 新增用户
const showCreateUser = ref(false);
const cuUsername = ref("");
const cuPassword = ref("");
const cuFriendlyName = ref("");
const cuIsAdmin = ref(false);
const cuOpPassword = ref("");
const cuBusy = ref(false);

watch(show, async (v) => {
  if (v && auth.isAdmin) {
    adminLoading.value = true;
    try {
      adminUsers.value = await auth.listUsers();
    } catch (e) {
      message.error(e instanceof Error ? e.message : t("listUsersFailed"));
    } finally {
      adminLoading.value = false;
    }
  }
  if (!v) {
    showCreateUser.value = false;
  }
});

async function refreshAdminUsers(): Promise<void> {
  adminUsers.value = await auth.listUsers();
}

async function submitCreateUser(): Promise<void> {
  if (!cuUsername.value.trim() || !cuPassword.value || !cuOpPassword.value) {
    message.warning(t("fillUserPasswordYours"));
    return;
  }
  cuBusy.value = true;
  try {
    await auth.adminCreateUser(
      cuUsername.value.trim(),
      cuPassword.value,
      cuFriendlyName.value.trim() || undefined,
      cuIsAdmin.value,
      cuOpPassword.value,
    );
    message.success(`${t("createdUser")} ${cuUsername.value.trim()}`);
    showCreateUser.value = false;
    cuUsername.value = "";
    cuPassword.value = "";
    cuFriendlyName.value = "";
    cuIsAdmin.value = false;
    cuOpPassword.value = "";
    await refreshAdminUsers();
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("createUserFailed"));
  } finally {
    cuBusy.value = false;
  }
}

// 用带输入框的 dialog 确认管理员操作。fields: [placeholder, 是否密码]
function promptAdminInputs(
  title: string,
  fields: { placeholder: string; password?: boolean }[],
  onConfirm: (values: string[]) => Promise<void>,
): void {
  const values = fields.map(() => "");
  dialog.warning({
    title,
    content: () =>
      h(
        "div",
        { style: "display: flex; flex-direction: column; gap: 8px;" },
        fields.map((f, i) =>
          h(NInput, {
            type: f.password === false ? "text" : "password",
            showPasswordOn: "click",
            placeholder: f.placeholder,
            "onUpdate:value": (v: string) => {
              values[i] = v;
            },
          }),
        ),
      ),
    positiveText: t("confirm"),
    negativeText: t("cancel"),
    onPositiveClick: async () => {
      if (values.some((v) => !v)) {
        message.warning(t("fillAllFields"));
        return false;
      }
      await onConfirm(values);
    },
  });
}

function onToggleAdmin(u: UserEntry): void {
  const next = !u.isAdmin;
  promptAdminInputs(
    `${next ? t("promoteAdmin") : t("demoteAdmin")}：${u.username}`,
    [{ placeholder: t("inputYourPasswordConfirm") }],
    async ([pw]) => {
      try {
        await auth.adminSetAdmin(u.username, next, pw!);
        message.success(t("updated"));
        await refreshAdminUsers();
      } catch (e) {
        message.error(e instanceof Error ? e.message : t("operationFailed"));
      }
    },
  );
}

function onAdminChangePassword(u: UserEntry): void {
  promptAdminInputs(
    `${t("changePassword")}：${u.username}`,
    [{ placeholder: t("newPassword") }, { placeholder: t("inputYourPasswordConfirm") }],
    async ([newPw, opPw]) => {
      try {
        await auth.adminChangePassword(u.username, newPw!, opPw!);
        message.success(t("changedPasswordOf"));
      } catch (e) {
        message.error(e instanceof Error ? e.message : t("changePasswordFailed"));
      }
    },
  );
}

function onAdminDelete(u: UserEntry): void {
  promptAdminInputs(
    `${t("deleteAccount")}：${u.username}${t("irreversible")}`,
    [{ placeholder: t("inputYourPasswordConfirm") }],
    async ([pw]) => {
      try {
        await auth.adminDeleteAccount(u.username, pw!);
        message.success(t("accountDeleted"));
        await refreshAdminUsers();
      } catch (e) {
        message.error(e instanceof Error ? e.message : t("deleteFailed"));
      }
    },
  );
}

function open(): void {
  show.value = true;
}

defineExpose({ open });
</script>

<template>
  <n-modal v-model:show="show" preset="card" :title="t('adminPanel')" style="width: 480px">
    <p v-if="adminLoading" class="admin-tip">{{ t("loadingUsers") }}</p>
    <p v-else-if="!adminUsers.length" class="admin-tip">{{ t("noUsers") }}</p>
    <div v-for="u in adminUsers" :key="u.username" class="admin-user">
      <div class="admin-user-row">
        <div class="admin-user-info">
          <span class="admin-username">{{ u.username }}</span>
          <span v-if="u.friendlyName" class="admin-nickname">{{ u.friendlyName }}</span>
          <span v-if="u.username === auth.username" class="admin-self">{{ t("self") }}</span>
          <span v-if="u.isAdmin" class="admin-badge">{{ t("admin") }}</span>
        </div>
        <div class="admin-user-actions">
          <n-button
            size="tiny"
            quaternary
            :disabled="u.username === auth.username"
            @click="onAdminChangePassword(u)"
          >
            {{ t("changePasswordShort") }}
          </n-button>
          <n-button
            size="tiny"
            quaternary
            :disabled="u.username === auth.username"
            @click="onToggleAdmin(u)"
          >
            {{ u.isAdmin ? t("demoteAdmin") : t("promoteAdmin") }}
          </n-button>
          <n-button
            size="tiny"
            quaternary
            type="error"
            :disabled="u.username === auth.username"
            @click="onAdminDelete(u)"
          >
            {{ t("delete") }}
          </n-button>
        </div>
      </div>
    </div>
    <n-button
      size="small"
      dashed
      block
      class="admin-add-btn"
      @click="showCreateUser = true"
    >
      {{ t("addUser") }}
    </n-button>

    <!-- 新增用户弹窗 -->
    <n-modal
      v-model:show="showCreateUser"
      preset="card"
      :title="t('newUser')"
      style="width: 360px"
    >
      <div class="create-user-form">
        <n-input v-model:value="cuUsername" :placeholder="t('username')" />
        <n-input
          v-model:value="cuPassword"
          type="password"
          show-password-on="click"
          :placeholder="t('initialPassword')"
        />
        <n-input v-model:value="cuFriendlyName" :placeholder="t('nicknameOptional')" />
        <n-checkbox v-model:checked="cuIsAdmin">{{ t("setAsAdmin") }}</n-checkbox>
        <n-input
          v-model:value="cuOpPassword"
          type="password"
          show-password-on="click"
          :placeholder="t('inputYourPasswordConfirm')"
          @keydown.enter="submitCreateUser"
        />
        <n-button
          type="primary"
          size="small"
          :loading="cuBusy"
          @click="submitCreateUser"
        >
          {{ t("create") }}
        </n-button>
      </div>
    </n-modal>
  </n-modal>
</template>

<style scoped>
.admin-tip {
  margin: 0;
  font-size: 12px;
  color: var(--app-text-3);
}

.admin-user {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.admin-user-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
}

.admin-user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1 1 auto;
  flex-wrap: wrap;
  word-break: break-all;
}

.admin-username {
  font-size: 13px;
  color: var(--app-text-1);
}

.admin-nickname {
  font-size: 12px;
  color: var(--app-text-3);
}

.admin-badge {
  font-size: 11px;
  color: var(--app-primary);
  background: var(--app-primary-soft);
  border-radius: 8px;
  padding: 0 6px;
  line-height: 16px;
}

.admin-self {
  font-size: 12px;
  color: var(--app-text-3);
}

.admin-user-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  margin-left: auto;
}

.admin-add-btn {
  margin-top: 12px;
}

.create-user-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
