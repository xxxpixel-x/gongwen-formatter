# PROGRESS

## 现状（v0.2.0，2026-10-01）

方向调整：不再从范例文件学习格式，改为用户**输入文字格式要求**，默认值为规范图片中的要求。

- `spec.py`：格式要求解析器。支持“标题（xx，xx）”和“标题：xx xx”两种写法；没写的段落类型从正文推出默认值；看不懂的片段写入 `problems`
- `classify.py`：段落角色识别（未改动）
- `render.py`：新建 A4 文档，每段的格式直接写在段落上，不依赖 Word 样式；同时修改 docDefaults，避免 Calibri 和段后 8 磅
- `gui.py`：tkinter 界面，包括要求输入、实时解析表、缺字体检测（读取 Windows 注册表）、识别结果预览、双击修改类型、生成文件
- 打包：`app.py` 作为入口，`build.bat` 用于本地打包，`packaging/installer.iss` 是 Inno Setup 脚本（免管理员安装、中文安装向导），`.github/workflows/build.yml` 负责推送 tag 后自动发布
- 测试：8 个，全部通过。Linux 上 PyInstaller 打包并运行通过；**Windows 打包和安装包尚未实际验证**

## 下一步

1. 在 Windows 上运行 build.bat，或推送 tag 让 CI 打包，在朋友的电脑上实际安装、试用
2. 用朋友的真实原稿测试识别准确率，收集识别错的例子
3. 支持拖拽文件（需要 tkinterdnd2 库）
4. 支持 .doc / .wps：用 pywin32 调用 Word 或 WPS 先转换成 docx
5. 支持图片：迁移图片关系（rId）
6. 页码要求（如“页码：宋体四号，-1- 样式”）

## 交接说明

- 设计原则：原稿只取文字，所有格式都来自格式要求
- 新增一种格式写法时，在 `spec._parse_attrs` 里加一个 `take(...)`，并在 tests/test_spec.py 里加一条测试
- `_parse_attrs` 中正则的先后顺序有讲究：必须先去掉“行距 28 磅”，再识别字号
