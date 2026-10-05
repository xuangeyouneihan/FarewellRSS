"""`python -m farewell_rss` 的入口。

只做「调用包里那个 main()」这一件事；真正的逻辑留在 `farewell_rss/main.py`。
这样逻辑模块始终有名字（可 import、可被测试 patch、可被 `uvicorn ...:app` 引用），
`__main__` 只是一次性入口，也避开了「把 main.py 改名成 __main__.py 后
`__init__.py` 再去 import 它」导致的模块被导入两次（一次叫 `__main__`、
一次叫 `farewell_rss.__main__`）那个经典坑。
"""

from .main import main

if __name__ == "__main__":
    main()
