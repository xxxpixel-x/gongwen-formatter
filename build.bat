@echo off
chcp 65001 >nul
REM 在 Windows 上本地打包：生成 dist\GongwenFormatter\GongwenFormatter.exe
REM 如果装了 Inno Setup 6，还会生成 dist\公文格式整理器-安装包-*.exe

python -m pip install --upgrade pip || goto :error
python -m pip install -e ".[dev]" pyinstaller || goto :error
python -m pytest -q || goto :error
python -m PyInstaller --noconfirm --clean --windowed --name GongwenFormatter --icon packaging\icon.ico --paths src app.py || goto :error

if not exist packaging\ChineseSimplified.isl (
  curl -L -o packaging\ChineseSimplified.isl https://raw.githubusercontent.com/jrsoftware/issrc/is-6_4_3/Files/Languages/Unofficial/ChineseSimplified.isl
)
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% /DAppVersion=0.5.0 packaging\installer.iss || goto :error
) else (
  echo 未找到 Inno Setup 6，只生成了免安装版：dist\GongwenFormatter\
)
echo 完成。
goto :eof

:error
echo 打包失败，请看上面的报错。
exit /b 1
