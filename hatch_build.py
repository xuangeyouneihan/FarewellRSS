"""hatch 构建钩子：前端产物的打包规则集中在这里。

规则只有一条：**除了「可编辑安装」，前端产物必须存在**。

- wheel（含发布流程）：`frontend/dist` → `src/farewell_rss/static`，装出来的服务自带前端
- sdist：映射写在 pyproject 的 `[tool.hatch.build.targets.sdist.force-include]` 里 ——
  实测钩子往 `build_data["force_include"]` 里填**对 sdist 目标不生效**（产物里查不到
  frontend/dist），而它不经过 editable，留配置里没有副作用
- editable（`uv sync`）：产物缺失就跳过——包直接从 `src/` 提供，`main.py` 的
  `_find_frontend_dist()` 会把项目里的 `frontend/dist` 找到；没有它也能只跑后端
  测试、只调 API

为什么规则不在 pyproject 的 `force-include` 里：那样 hatchling 在**可编辑安装**时也会
校验源路径，新克隆的仓库没构建前端就连 `uv sync` / `pytest` 都跑不起来
（2026-10-02 的 nightly 就是这么挂的）。而钩子能改的只有 `build_data["force_include"]`
（配置里那一份实测拿不到，里面是空的），所以规则搬进来；顺带把缺前端时的报错写得比
hatchling 的 `Forced include not found: …` 直白。
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found]
    BuildHookInterface,
)


class FrontendDistHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        # 只管 wheel 目标。sdist 的映射写在 pyproject 里：往 sdist 的
        # build_data["force_include"] 里填东西会**盖掉配置里那条**，
        # 于是 sdist 包不到 frontend/dist，接着「从 sdist 构建 wheel」那步就找不到源目录。
        if self.target_name != "wheel":
            return

        dist = Path(self.root, "frontend", "dist")
        if not dist.exists():
            if version == "editable":
                # 开发/测试：允许没有前端产物（见模块 docstring）
                return
            msg = (
                "缺少前端构建产物：正式构建不会跳过这一步，"
                "请先执行 `cd frontend && pnpm install && pnpm build`"
                "（只有可编辑安装才允许没有它）"
            )
            raise FileNotFoundError(msg)

        if version != "editable":
            build_data.setdefault("force_include", {})["frontend/dist"] = (
                "src/farewell_rss/static"
            )
