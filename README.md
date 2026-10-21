# Python 脚本项目

用于编写和运行日常 Python 脚本，要求 Python 3.12 或更新版本。初始项目只使用标准库，无需安装第三方依赖。

## 初始化

在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell 激活命令：`.venv\Scripts\Activate.ps1`。

## 运行脚本

```bash
python scripts/hello.py
python scripts/hello.py --name 小明
```

新脚本放在 `scripts/` 目录下，推荐使用 `main()` 函数和 `if __name__ == "__main__":` 入口，避免导入脚本时执行任务。

## 运行测试

```bash
python -m unittest discover -s tests -v
```

## 第三方依赖

需要第三方包时，先将依赖及版本约束写入 `requirements.txt`，再运行：

```bash
python -m pip install -r requirements.txt
```

不要提交虚拟环境、密钥或 `.env` 文件；如需记录环境变量格式，可创建不含真实密钥的 `.env.example`。
