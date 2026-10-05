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


def _child_env(data_dir: Path, port: int) -> dict[str, str]:
    """子进程的环境变量

    `PYTHONIOENCODING` 必须钉死 utf-8：启动日志里有中文，而这个测试要把子进程的输出读
    回来做断言，两边一旦不一致就会出事 —— 只按 locale（中文 Windows 上是 GBK）解码、
    子进程却吐 UTF-8 时，`subprocess` 的读取线程会抛 `UnicodeDecodeError`，`output`
    直接变成 `None`，断言处报成 `TypeError: argument of type 'NoneType' is not a
    container`（看着像代码坏了，其实是编码）；反过来只让子进程说 UTF-8 而不改解码，
    在 GBK 机器上又会把中文解成乱码、断言失配。所以两端一起钉。

    **必须放在 `**os.environ` 之后**：宿主环境里已有的同名变量（比如某个终端里
    `export PYTHONIOENCODING=utf-8` 漏进来的）不能把子进程带偏。
    """
    return {
        **os.environ,
        "FAREWELL_RSS_DATA_DIR": str(data_dir),
        "FAREWELL_RSS_PORT": str(port),
        # 让调度器只跑一轮，别在测试里干活
        "FAREWELL_RSS_FEED_REFRESH_INTERVAL": "999999",
        "PYTHONIOENCODING": "utf-8",
    }


def _start_and_wait(data_dir: Path, port: int) -> tuple[int | None, str]:
    """起进程、等它应答；**无论如何都收尸**，并把启动日志带回来（断言失败时要看）"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "from farewell_rss.main import main; main()"],
        env=_child_env(data_dir, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # 与子进程的 PYTHONIOENCODING 对齐；errors 只是兵底，不让半个字节把整条断言带走
        encoding="utf-8",
        errors="replace",
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


def test_child_env_overrides_leaked_io_encoding(monkeypatch, tmp_path):
    """宿主环境里的 PYTHONIOENCODING 不能影响子进程：两端必须都是 UTF-8

    不这么钉的话，父进程里 leak 一个 `PYTHONIOENCODING=utf-8` 就能把上面那条启动
    测试搞挂（表现为 `TypeError: ... 'NoneType' ...`，看起来像代码回归）。
    """
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")

    assert _child_env(tmp_path, 1)["PYTHONIOENCODING"] == "utf-8"
