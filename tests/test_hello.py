"""验证示例脚本的命令行行为。"""

from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hello.py"


class HelloScriptTests(unittest.TestCase):
    def test_default_name(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "你好，世界！")

    def test_custom_name(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--name", "小明"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "你好，小明！")


if __name__ == "__main__":
    unittest.main()
