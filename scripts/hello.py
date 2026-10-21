"""示例：使用命令行参数运行 Python 脚本。"""

import argparse


def greeting(name: str) -> str:
    """生成问候语。"""
    return f"你好，{name}！"


def main() -> None:
    parser = argparse.ArgumentParser(description="Python 脚本项目示例")
    parser.add_argument("--name", default="世界", help="要问候的名字")
    args = parser.parse_args()
    print(greeting(args.name))


if __name__ == "__main__":
    main()
