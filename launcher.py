"""PyInstaller 打包入口：以顶层脚本方式启动 scansplit 包（相对导入不可用于 __main__ 脚本）。"""

from scansplit.main import main

if __name__ == "__main__":
    raise SystemExit(main())
