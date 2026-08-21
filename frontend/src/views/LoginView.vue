<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NCard, NForm, NFormItem, NInput, useMessage } from "naive-ui";
import { useAuthStore } from "@/stores/auth";
import { t } from "@/i18n";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
const message = useMessage();

// 登录/注册模式由路由决定（/login 与 /register 是两个端点）
const isRegister = ref(route.name === "register");
watch(
  () => route.name,
  (name) => {
    isRegister.value = name === "register";
  },
);
watch(
  isRegister,
  (v) => {
    document.title = `${v ? t("register") : t("login")} · ${t("appName")}`;
  },
  { immediate: true },
);

const email = ref("");
const password = ref("");
const confirmPassword = ref("");
const friendlyName = ref("");
const inviteCode = ref("");
const submitting = ref(false);

async function submit(): Promise<void> {
  if (!email.value || !password.value) {
    message.error(t("inputUserPassword"));
    return;
  }
  if (isRegister.value && password.value !== confirmPassword.value) {
    message.error(t("passwordMismatch"));
    return;
  }
  submitting.value = true;
  try {
    if (isRegister.value) {
      await auth.register(
        email.value,
        password.value,
        friendlyName.value || undefined,
        inviteCode.value || undefined,
      );
    } else {
      await auth.login(email.value, password.value);
    }
    await router.push({ name: "reader" });
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("operationFailed"));
  } finally {
    submitting.value = false;
  }
}

function toggleMode(): void {
  friendlyName.value = "";
  confirmPassword.value = "";
  inviteCode.value = "";
  void router.push({ name: isRegister.value ? "login" : "register" });
}
</script>

<template>
  <div class="login-page">
    <n-card class="login-card" :title="isRegister ? t('register') : t('login')">
      <n-form @submit.prevent="submit">
        <n-form-item :label="t('username')">
          <n-input v-model:value="email" :placeholder="t('username')" @keydown.enter="submit" />
        </n-form-item>
        <n-form-item :label="t('password')">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :placeholder="t('password')"
            @keydown.enter="submit"
          />
        </n-form-item>
        <n-form-item v-if="isRegister" :label="t('confirmPassword')">
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('confirmPassword')"
            @keydown.enter="submit"
          />
        </n-form-item>
        <n-form-item v-if="isRegister" :label="t('nickname')">
          <n-input v-model:value="friendlyName" :placeholder="t('nicknameOptional')" />
        </n-form-item>
        <n-form-item v-if="isRegister" :label="t('inviteCode')">
          <n-input
            v-model:value="inviteCode"
            :placeholder="t('inviteCodePlaceholder')"
            @keydown.enter="submit"
          />
        </n-form-item>
        <n-button type="primary" block :loading="submitting" @click="submit">
          {{ isRegister ? t("register") : t("login") }}
        </n-button>
      </n-form>
      <n-button text class="toggle" @click="toggleMode">
        {{ isRegister ? t("hasAccountToLogin") : t("noAccountToRegister") }}
      </n-button>
    </n-card>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.login-card {
  width: 360px;
}

.toggle {
  margin-top: 8px;
  width: 100%;
}
</style>
