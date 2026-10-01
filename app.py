"""打包入口（PyInstaller 从这里开始）。开发时也可以直接 python app.py 打开界面。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from gongwen_formatter.gui import main  # noqa: E402

main()
