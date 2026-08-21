<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NInput, NModal, useMessage } from "naive-ui";
import { useSubscriptionsStore } from "@/stores/subscriptions";
import type { LabelType } from "@/types/greader";
import { t } from "@/i18n";

defineOptions({ name: "CreateLabelModal" });

const emit = defineEmits<{ (e: "update:show", value: boolean): void }>();

const subs = useSubscriptionsStore();
const message = useMessage();

const show = ref(false);
const type = ref<LabelType>("tag");
const name = ref("");
const creating = ref(false);

const title = computed(() =>
  type.value === "tag" ? t("newStarCategory") : t("newSubCategory"),
);
const placeholder = computed(() => t("categoryName"));

watch(show, (v) => emit("update:show", v));

function open(labelType: LabelType): void {
  type.value = labelType;
  name.value = "";
  show.value = true;
}

async function submit(): Promise<void> {
  const value = name.value.trim();
  if (!value) {
    message.warning(t("inputName"));
    return;
  }
  creating.value = true;
  try {
    await subs.createLabel(value, type.value);
    show.value = false;
    name.value = "";
  } catch (e) {
    message.error(e instanceof Error ? e.message : t("createFailed"));
  } finally {
    creating.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <n-modal v-model:show="show" preset="card" :title="title" style="width: 320px">
    <n-input
      v-model:value="name"
      :placeholder="placeholder"
      @keydown.enter="submit"
    />
    <template #footer>
      <n-button size="small" :loading="creating" type="primary" @click="submit"> {{ t("create") }} </n-button>
    </template>
  </n-modal>
</template>
