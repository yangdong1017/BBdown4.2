# BBDown4.2

BBDown4.2 是一个 Windows 桌面工具，用来降低批量处理视频、音频素材的操作成本。

它支持 B站链接批量下载音频、抖音视频直链批量下载，也支持将抖音音频链接、本地音频、本地视频批量转写为文字，适合整理口播文案、字幕文本和音频内容。

## 功能

### B站批量下载

- 支持 B站链接批量下载音频
- 支持一行一个链接批量处理
- 支持 WEB / TV 扫码登录
- 下载失败或未产出音频的链接会自动保留，方便重试

### 抖音视频下载

- 支持批量粘贴 `aweme.snssdk.com/aweme/v1/play/?video_id=...` 视频直链
- 自动提取并去重视频 ID
- 显示单个视频和整批任务下载进度
- 支持停止任务和失败重试
- 当前不解析 `douyin.com/video/...` 页面链接

### 下载或转写后批量重命名

- 抖音视频、音频下载结束后，点击结果表上方“批量重命名”。
- 直接在弹窗粘贴一列标题（一行一个），点击“应用重命名”。不需要在表格里逐个修改。
- 未选中行时处理当前列表全部任务；选中多行时只处理选中的任务，均按列表原始顺序对应。
- 标题行数必须一致，空白行保留原名；失败任务保留位置，重试下载成功后自动使用已填写的标题。
- 自动保留扩展名、处理非法字符和同名文件，不覆盖已有文件；可撤销最近一次重命名。
- 下载结果和改名后的路径保存在 `bbdown_runtime/douyin_downloads.json`，切换视频/音频或重启后保留；再次下载同一链接会识别已改名文件。
- “批量转文字”的本地音视频和音频链接两种模式也提供相同按钮和弹窗，仅修改输出的 TXT/SRT/ASS 文稿，原音视频不变。
- 文稿改名支持选择部分行、空白行保留、失败重试后应用标题及撤销。运行中锁定输入、格式、输出目录和改名操作，避免对应错位。
- `bbdown_runtime/asr_transcripts.json` 保存来源与实际文稿路径，重启或切换模式后保留。更换输出目录/格式不改变旧文稿的改名对象；重试仍能识别已经改名的文稿。

### 批量转文字

支持两种模式：

1. 抖音音频链接转文字
   粘贴抖音音频直链，或粘贴包含音频直链的整段文本，软件会自动提取可转写链接。
   抖音视频分享短链不是音频直链，当前不能直接转写。

2. 音视频转文字  
   选择本地音频、视频文件，或直接选择文件夹批量转写。

豆包 API Key 不再内置。需要使用豆包时，在软件左下角“设置”里填写自己的火山引擎 API Key。

支持导出格式：

- txt
- srt
- ass

## 安装方式

### 方式一：安装包安装

前往 Releases 下载：

```text
BBDown-4.2.exe
```

双击安装包，按照提示安装即可。

### 方式二：解压直接用

前往 Releases 下载：

```text
BBDown-4.2.zip
```

使用方法：

1. 解压 zip 文件。
2. 打开解压后的 `BBDown` 文件夹。
3. 双击 `BBDown.exe` 运行。

注意：不要只单独拷贝 `BBDown.exe`。解压后的 `_internal` 文件夹必须和 `BBDown.exe` 放在一起，否则软件无法正常启动。

## 源码运行

如果你下载的是源码，需要先安装 Python 3.10+。

打开 PowerShell，进入项目目录后执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

依赖安装完成后，后续可以直接双击：

```text
run_source.bat
```

## 项目结构

```text
BBDown4.2/
├─ app.py
├─ core/                         # 下载、配置、任务调度、转写服务
│  ├─ asr_service.py              # ASR 接口封装
│  ├─ asr_task.py                 # 转写任务处理
│  ├─ asr_file_worker.py          # 本地文件转写后台任务
│  ├─ bilibili_workers.py         # B站下载和登录后台任务
│  ├─ config_store.py             # 配置原子读写
│  ├─ douyin_video_downloader.py  # 抖音视频单任务下载
│  ├─ douyin_video_worker.py      # 抖音视频批量调度
│  ├─ feishu_api.py               # 飞书 Base API 客户端
│  ├─ feishu_license_client.py    # 飞书 Base 卡密直连客户端
│  ├─ license_service.py          # 卡密激活和校验
│  ├─ license_private.example.py  # 本地密钥配置模板
│  ├─ machine_id.py               # 本机设备ID生成
│  ├─ output_paths.py             # 并发输出文件名保护
│  ├─ url_asr_worker.py           # 音频链接转写后台任务
│  ├─ url_audio.py                # 音频链接识别和读取
│  └─ task_scheduler.py           # 并发任务调度
├─ ui/                            # 图形界面
│  ├─ asr_inputs.py               # 本地文件和链接输入组件
│  ├─ bilibili_login_panel.py     # B站登录面板
│  ├─ douyin_video_page.py        # 抖音下载页面
│  ├─ license_dialog.py           # 卡密激活窗口
│  └─ settings_page.py            # 豆包 API Key 设置页
├─ tests/                         # 自动化测试
├─ bk_asr/                        # ASR 实现
├─ tools/                         # BBDown、FFmpeg、aria2c
├─ requirements.txt
├─ build_bbdown_launcher.spec
└─ installer.iss
```

## 打包

安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller build_bbdown_launcher.spec --noconfirm
```

打包结果位于：

```text
dist\BBDown\BBDown.exe
```

如需生成安装包，需要先安装 Inno Setup 6，然后执行：

```powershell
ISCC.exe installer.iss
```

安装包默认输出到：

```text
installer_output\
```

## 卡密维护

4.2 保留卡密激活逻辑，默认开启强制校验。

本版本采用本地 EXE 直连飞书 Base 的方式。先复制模板：

```powershell
copy .\core\license_private.example.py .\core\license_private.py
```

然后在 `core\license_private.py` 填入：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
BASE_APP_TOKEN
CARD_TABLE_ID
LOG_TABLE_ID
```

注意：`core\license_private.py` 已加入 `.gitignore`，不要提交到 GitHub。

强制校验开关在：

```text
core/config.py
LICENSE_REQUIRED = True
```

如果打包 EXE，`core\license_private.py` 会一起打进安装包。这个方式上手简单，但安装包被反编译时可能暴露飞书密钥。

## 发布维护流程

每次发布新版本时，先本地打包，再创建 GitHub Release。

1. 更新版本相关文件：

```text
README.md
installer.iss
```

2. 生成解压版：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller build_bbdown_launcher.spec --noconfirm --clean
New-Item -ItemType Directory -Force -Path .\release_assets\v4.2
Compress-Archive -Path .\dist\BBDown -DestinationPath .\release_assets\v4.2\BBDown-4.2.zip -Force
```

3. 生成安装包：

```powershell
ISCC.exe installer.iss
```

如果 `ISCC.exe` 不在 PATH，可以使用：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\installer.iss
```

生成文件：

```text
installer_output\BBDown-4.2.exe
```

4. 先测试本地产物：

```text
dist\BBDown\BBDown.exe
release_assets\v4.2\BBDown-4.2.zip
installer_output\BBDown-4.2.exe
```

5. 测试无误后，再创建 GitHub Release 并上传安装包和解压包。

注意：不要只更新 README 就创建 Release。Release 必须对应已经本地打包并测试过的安装包和解压包。

发布包不要提交到 Git 仓库，只上传到 GitHub Release。

## 作者声明

本仓库由作者借助人工智能工具整理、开发和维护。

未经作者许可，禁止以任何形式冒用作者身份发布本项目，禁止将本项目用于侵犯他人知识产权、违反平台规则或违反法律法规的用途。由此产生的责任由行为人自行承担。

最终解释权归作者所有。
