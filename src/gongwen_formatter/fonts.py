"""检测电脑上装了哪些字体。

坑：Windows 注册表里记录的是英文名（黑体 = SimHei，仿宋_GB2312 = FangSong_GB2312），
而格式要求里写的是中文名。只查注册表会把明明装了的“黑体”报成缺失。
所以这里直接读每个字体文件里的“名字表”（name table），它同时记录了
中文名和英文名；再加一张常见字体的中英对照表兜底。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 常见中文字体的中英文名对照（字体文件读取失败时兜底）
ALIASES = {
    "黑体": "SimHei", "宋体": "SimSun", "新宋体": "NSimSun", "仿宋": "FangSong",
    "楷体": "KaiTi", "微软雅黑": "Microsoft YaHei", "等线": "DengXian",
    "仿宋_GB2312": "FangSong_GB2312", "楷体_GB2312": "KaiTi_GB2312",
    "隶书": "LiSu", "幼圆": "YouYuan", "华文中宋": "STZhongsong",
    "华文仿宋": "STFangsong", "华文楷体": "STKaiti", "华文宋体": "STSong",
}


def normalize(name: str) -> str:
    return name.replace(" ", "").replace("-", "_").casefold()


def _font_files() -> list[Path]:
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:  # 只给当前用户安装的字体在这里
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    files = []
    for d in dirs:
        if d.is_dir():
            files += [p for p in d.iterdir() if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    return files


def _names_from_file(path: Path) -> set[str]:
    from fontTools.ttLib import TTCollection, TTFont

    fonts = TTCollection(str(path), lazy=True).fonts if path.suffix.lower() == ".ttc" \
        else [TTFont(str(path), lazy=True, fontNumber=0)]
    names = set()
    for font in fonts:
        for rec in font["name"].names:
            if rec.nameID in (1, 4, 16):  # 字体族名、全名、排版用族名
                try:
                    names.add(rec.toUnicode())
                except Exception:
                    pass
    return names


def _registry_names() -> set[str]:
    import winreg
    names: set[str] = set()
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key_path) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumValue(key, i)[0]
                    except OSError:
                        break
                    i += 1
                    names.update(part.strip() for part in name.split(" (")[0].split("&"))
        except OSError:
            continue
    return names


def installed_fonts() -> set[str] | None:
    """返回已安装字体的所有名字（已 normalize）；非 Windows 返回 None 表示不检查。
    读几百个字体文件需要一两秒，界面里放在后台线程调用。"""
    if sys.platform != "win32":
        return None
    names = _registry_names()
    for path in _font_files():
        try:
            names |= _names_from_file(path)
        except Exception:
            continue  # 损坏或特殊格式的字体文件，跳过
    found = {normalize(n) for n in names}
    for zh, en in ALIASES.items():
        if normalize(en) in found:
            found.add(normalize(zh))
    return found


def missing_fonts(wanted: set[str], installed: set[str] | None) -> list[str]:
    if installed is None:
        return []
    return sorted(f for f in wanted if normalize(f) not in installed)
