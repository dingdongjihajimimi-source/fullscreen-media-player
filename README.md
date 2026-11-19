# 全屏播放器

适用于 Windows 和 macOS 的本地全屏播放器，面向展览大屏、演示和素材预览。打开视频即可全屏循环，播放区隐藏进度条、文件名和操作提示。按 `P` 展开侧栏，浏览同目录视频、查看媒体信息，或继续用 FFmpeg 处理当前素材。

播放由 `libmpv` 使用 FFmpeg 库解封装和解码。界面采用 PySide6，媒体导出和检测由 `ffmpeg` 与 `ffprobe` 完成。支持 MP4、MKV、MOV、AVI、WebM 等常见视频，以及静态图片和 GIF。

## 使用

构建后，在 Windows 10（1809 及以上）或 Windows 11 x64 打开 `dist/Player-Windows-x64.exe`，在 Apple Silicon Mac 打开 `dist/播放器.app`。两种构建产物均内置媒体运行库。

将视频或图片拖入窗口，或点击「打开文件」。

| 快捷键 | 操作 |
| --- | --- |
| `P` | 显示或隐藏侧栏 |
| `Esc` / `F11` | 切换全屏 |
| `O` | 打开媒体文件 |
| `Space` | 播放或暂停 |
| `←` / `→` | 播放目录中的上一部或下一部视频 |

播放快捷键在播放画面获得焦点时生效。也可以通过系统的「打开方式」或从命令行传入本地媒体路径。

## FFmpeg 工具

- 视频压缩支持独立调整分辨率、码率和帧率。「大屏兼容版」导出 H.264 MP4，支持横屏、竖屏、留黑边或裁切铺满，识别到 HDR PQ 或 HLG 时转换为 SDR。
- 「截取片段」支持 A–B 循环预览和 MP4 精确截取。快速截取使用原编码输出 MKV，片段边界受关键帧和流时间戳影响。
- 可保存当前画面为 PNG 或 JPEG，并逐条查看源文件的音轨、字幕和章节信息。
- 「换封装」支持 MP4 和 MKV，可以按轨保留、跳过或转码。图像字幕可通过 MKV 保留。独立提取音轨支持原编码、AAC、WAV 和 FLAC。
- 单张图片可转为静止视频，多张可排序并单独设置时长，也可为每张加入淡入和淡出。
- 「素材体检」检测疑似黑场、静音和冻帧，点击检测结果定位到对应位置。

导出默认保存到源文件目录，同名时递增序号。任务进度、取消和失败详情都在右侧栏。素材体检的结果取决于检测阈值，建议通过画面和声音确认。

## 从源码运行和打包

macOS Apple Silicon 已验证播放、截图和媒体导出。Windows x64 已通过 Wine 环境下的启动、播放和导出测试。

在 Apple Silicon Mac 上准备依赖并重新打包。

```bash
bash scripts/setup-macos.sh
.venv/bin/python app.py "/path/to/media.mkv"
```

在 Windows 本机安装 x64 版 Python 3.12 或更新版本，准备同架构的 `libmpv-2.dll`、`ffmpeg.exe` 和 `ffprobe.exe`。FFmpeg 构建需包含 `libx264` 和 `zscale`。将运行库文件连同配套 DLL 放进项目的 `bin` 目录，或通过 `--runtime` 指定目录。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt 'pyinstaller>=6,<7'
.\.venv\Scripts\python.exe scripts\package.py
```

Windows 构建生成独立的 `dist\Player-Windows-x64.exe`。运行时会把内置依赖解压到系统临时目录，并在正常关闭后清理。

## 媒体运行库来源

- macOS 播放运行库与构建脚本见 [Media Kit 的 libmpv 构建](https://github.com/media-kit/libmpv-darwin-build/tree/v0.7.2)。
- macOS 转码工具使用 [Martin Riedl 的 FFmpeg 9.0.2 构建](https://ffmpeg.martin-riedl.de/)，FFmpeg 源码见 [FFmpeg 下载页](https://ffmpeg.org/download.html)。
- Windows 的播放 DLL 可从 [mpv 官方发行页](https://github.com/mpv-player/mpv/releases)获取。Windows FFmpeg 完整版入口见 [FFmpeg 下载页](https://ffmpeg.org/download.html)。
