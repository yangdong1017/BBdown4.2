# 开发文档，AI禁止删除

这个文件是项目维护约定。任何人或 AI 接手本项目时，先读这个文件。

## 核心设计理念

1. 代码结构化、清晰化。
2. 不要为了设计而设计。
3. 不要把结构做死，后续必须留有修改余地。
4. 降低用户上手难度，减少用户思考阻力。
5. 禁止增加用户的思考周期。
6. 用户能少点一步，就不要让用户多点一步。
7. 报错要说人话，不要把接口原始错误直接丢给用户。

## 功能设计习惯

1. 用户界面只暴露用户必须理解的内容。
2. 技术参数默认内置，不让普通用户填写。
3. 豆包转文字只让用户填写自己的火山引擎 API Key。
4. 连接测试只给用户看“成功”或“失败”，不要显示复杂接口细节。
5. 如果用户没有填写豆包 API Key，开始任务前直接提示，不要等一批任务失败后再让用户猜原因。
6. 并发默认不能低于 5。
7. 停止任务要尽量及时，不再开始新任务。
8. “抖音音频链接转文字”只承诺处理 mp3/wav 音频直链；抖音视频分享短链需要额外解析，不能和音频直链混为一谈。


## 推送 GitHub 前的固定流程

不要只改 README 就推送。

每次准备推送前，必须按顺序做：

1. 清理本地缓存和运行产物。
2. 确认版本号一致。
3. 本地打包 EXE。
4. 生成解压版 zip。
5. 本地测试安装包和解压版。
6. 测试没问题后再提交代码。
7. 推送 GitHub。
8. 创建 GitHub Release。
9. Release 上传安装包和解压包。


## 版本一致性检查

每次发布前检查这些文件：

```text
core/config.py
installer.iss
README.md
build_bbdown_launcher.spec
```

需要保持一致：

```text
APP_VERSION
MyAppVersion
README 中的版本号
Release 文件名
```

例如 4.2：

```text
BBDown 4.2
BBDown-4.2.exe
BBDown-4.2.zip
v4.2
```

## 打包流程

先清理旧产物，再执行：

```powershell
python -m PyInstaller build_bbdown_launcher.spec --noconfirm --clean
```

生成解压版：

```powershell
New-Item -ItemType Directory -Force -Path .\release_assets\v4.2
Compress-Archive -Path .\dist\BBDown -DestinationPath .\release_assets\v4.2\BBDown-4.2.zip -Force
```

生成安装包：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\installer.iss
Copy-Item .\installer_output\BBDown-4.2.exe .\release_assets\v4.2\BBDown-4.2.exe -Force
```

## GitHub Release 习惯

Release 上传两个文件：

```text
BBDown-4.2.exe
BBDown-4.2.zip
```

不要把 `dist/`、`installer_output/`、`release_assets/` 提交到 Git 仓库。

这些只上传到 GitHub Release。

## 代码修改习惯

1. 优先沿用项目现有结构。
2. 小功能不要拆出过度复杂的架构。
3. UI 页面保持黑色主题，不能出现白色割裂窗口。
4. 页面文案要短、直接、能看懂。
5. 能自动判断的，不让用户手动判断。
6. 能默认处理的，不把选项抛给用户。
7. 只有用户确实需要控制的东西，才放到界面上。

## AI 协作要求

AI 修改本项目时必须遵守：

1. 修改前先看现有代码结构。
2. 不要擅自删除用户文件。
3. 不要删除本文件。
4. 不要删除 `core/license_private.py`。
5. 不要把本地缓存、日志、打包产物提交到 Git。
6. 推送前必须提醒先打包并测试。
7. 如果用户说“准备推送”，默认还需要先打包 EXE 和 zip。
8. 如果用户说“整理文件夹”，只清缓存和非必要产物，不动源码和私有配置。
9. 最终回复要说清楚改了什么、测试了什么、还有什么没做。

## 当前 4.2 关键变化

1. 设置页增加 API Key 获取教程及“打开”按钮。
2. 批量转文字的音频链接区显示标准音频链接、复制和打开，间距与下载页一致。
3. 抖音视频和音频下载结果支持“批量重命名”：点击直接弹出标题输入框，粘贴后直接应用，没有逐行编辑或演示数据。
4. 第一列显示文件名，ID 和实际路径放在悬停提示中。标题按任务原始顺序对应，失败项不移除；空白行保留原名。
5. `core/download_renaming.py` 负责文件身份、命名冲突、实际改名和撤销。`ui/batch_rename_dialog.py` 沿用原生深色控件。
6. `bbdown_runtime/douyin_downloads.json` 保存下载结果与改名路径。再次下载须沿用映射检查已存在文件，不能仅按原 ID 判断；更换保存目录不能改变旧结果的实际路径。
7. 重命名必须保留扩展名、不覆盖其他文件；下载运行中禁止改名。测试必须使用临时文件，不能对真实用户素材跑改名测试。
8. 批量转文字两种模式复用 `RenameToolbar` 和 `BatchRenameDialog`，仅重命名输出文稿；`LocalFileInput` 的 `Qt.UserRole` 必须始终保留源音视频路径，输出记录使用独立角色绑定。
9. `core/asr_renaming.py` 和 `bbdown_runtime/asr_transcripts.json` 保存文稿映射。任务结果须使用实际输出路径；URL 失败子集重试保留完整列表及原顺序，不能按完成顺序对应标题。
10. 转写运行期间禁用模式、格式、输出目录、输入增删和重命名，结束后再开放；跳过的文稿只有指纹匹配已记录输出时才可采纳并改名。

## 4.1 关键变化

4.1 没有新增功能，是一次稳定性和结构整理。

1. 新增全局异常兜底 `core/crash_guard.py`。未捕获异常写入
   `bbdown_runtime/bbdown.log` 并弹一次性提示，程序不再直接退出。
2. 关闭窗口最多等待 10 秒，不会被慢网络请求卡住。
3. 打包版不再读取 `BBDOWN_LICENSE_REQUIRED` / `BBDOWN_LICENSE_API_URL`，
   只有开发态可以覆盖。这两个变量原本可以零门槛绕过卡密校验。
4. 机器码只认 Windows MachineGuid，换网卡、插扩展坞、改电脑名不再掉授权。
   旧机器码仍然认，匹配到就替换成新的，设备名额不增加。
   **兼容逻辑要保留一到两个版本再拆掉**，`MACHINE_ID_SALT` 永远不要改。
5. B站页输入加 350ms 防抖，不再每敲一个键重写一次配置文件。
6. 抖音下载整批共用一个连接池；进度信号聚合成 5Hz 整批下发。
7. 报错话术收口：只有 `core/errors.py::UserFacingError` 和已知网络/磁盘异常
   会显示原文，其余一律显示"处理失败，请重试。"，真实异常写日志。
   **新增会抛给用户看的异常时，记得继承 UserFacingError。**
8. 停止任务后的摘要补上"已停止/未处理"计数，各项相加等于总数。
9. 三个页面共用 `ui/platform_utils.py`、`ui/collapsible_panel.py`、
   `ui/task_table.py`、`ui/window_title.py`、`core/links.py`、`core/atomic_io.py`。
   **改这些行为时改一处即可，不要再各页面复制。**
10. 任务结束后窗口标题统一复位。

## 4.0 关键变化

1. 豆包 API Key 不再内置。
2. 左下角新增“设置”入口。
3. 用户在设置页填写自己的火山引擎 API Key。
4. 豆包连接测试使用内置测试音频：

```text
https://lf26-music-east.douyinstatic.com/obj/ies-music-hj/7546439142222302011.mp3
```

5. 测试结果只显示“测试成功”或“测试失败”。
6. 没填 API Key 时，豆包转文字会提前提示，不会开始批量失败。
7. 新增抖音视频直链批量下载、任务进度和即时停止。
8. 配置、飞书、B站登录和转写输入完成模块拆分。
9. 并发输出增加同名文件保护，任务异常不会中断整批任务。
10. 所有并发入口默认值和最低值统一为 5。
