"""启动冒烟：按生产的方式把真进程拉起来，看它能不能起、能不能应答

**不测功能**（那是其他测试的活）。守住的是集成测试永远覆盖不到的那条路径 ——
`main()` → 环境变量/`.env` →（没密钥就生成）→ `init_db` 真建库 → 起 scheduler
→ uvicorn 绑端口 → 应答 HTTP。

这个文件有两处用途，别当成重复：

* CI 里跟其他测试一起跑：`sys.executable` 是开发环境，验的是**源码**能不能起。
* CI 里另一步单独跑（装进干净 venv 的 wheel 之后）：`sys.executable` 是那个临时
  venv，于是 `farewell_rss` 来自**已构建的包** —— 回答「pip 装完能不能起来」。
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

# 只探一个端点：有应答就说明路由活着（缺参数回 4xx 也算活着）
PROBE_PATH = "/api/greader.php/reader/api/0/token"
_TIMEOUT = 30.0
_POLL_INTERVAL = 0.2


def _free_port() -> int:
    """让系统给一个空闲端口：绑 :0 读出来再关掉

    不能用固定端口：CI 上并行的残留进程会撞，本地也会。
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _probe(url: str) -> int | None:
    """有应答就返回状态码（4xx/5xx 也算有应答），连不上返回 None"""
    try:
        with urlopen(url, timeout=2) as response:
            return int(response.status)
    except HTTPError as error:  # HTTPError 是 URLError 的子类，必须先捕
        return int(error.code)
    except URLError, OSError:
        return None


def _start_and_wait(data_dir: Path, port: int) -> tuple[int | None, str]:
    """起进程、等它应答；**无论如何都收尸**，并把启动日志带回来（断言失败时要看）"""
    env = {
        **os.environ,
        "FAREWELL_RSS_DATA_DIR": str(data_dir),
        "FAREWELL_RSS_PORT": str(port),
        # 让调度器只跑一轮，别在测试里干活
        "FAREWELL_RSS_FEED_REFRESH_INTERVAL": "999999",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from farewell_rss.main import main; main()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    status: int | None = None
    deadline = time.monotonic() + _TIMEOUT
    try:
        while time.monotonic() < deadline:
            status = _probe(f"http://127.0.0.1:{port}{PROBE_PATH}")
            if status is not None:
                break
            time.sleep(_POLL_INTERVAL)
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # 收尸也得有上限，不能把 CI 挂住
            proc.kill()
            output, _ = proc.communicate()
    return status, output


def test_app_starts_and_answers(tmp_path):
    """全新空目录上：起得来、建得出库、答得出话"""
    status, output = _start_and_wait(tmp_path, _free_port())

    assert status is not None, f"服务没起来（{_TIMEOUT:.0f}s 内没应答）：\n{output}"
    assert status < 500, f"起来了但回了 5xx（{status}）：\n{output}"
    assert (tmp_path / "farewell_rss.db").exists(), f"init_db 没建库：\n{output}"
    # 「前端产物找没找到」必须看得见：这两句以前写在模块级（import 时执行），
    # 那时日志还没配、级别默认 WARNING，INFO 永远看不到，于是「为什么只有 API」
    # 也跟着说不清。这条断言就是钉住它别被挪回去。
    assert "前端静态文件目录" in output or "未找到前端构建产物" in output, (
        f"启动日志里看不到前端产物的判断：\n{output}"
    )
