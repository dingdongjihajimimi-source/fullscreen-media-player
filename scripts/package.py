"""Build a desktop bundle on the target OS using a local media runtime."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from player.media import IMAGE, VIDEO, binary, runtime_dirs  # noqa: E402


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def dependencies(path):
    lines = run('otool', '-L', path, capture_output=True, text=True).stdout.splitlines()[1:]
    for line in lines:
        match = re.match(r'\s+(.+?) \(compatibility version', line)
        if match:
            yield match[1]


def copy_mac_runtime(destination, include_tools=True):
    libdirs = runtime_dirs()
    mpv = next((directory / name for directory in libdirs for name in ('libmpv.2.dylib', 'libmpv.dylib') if (directory / name).is_file()), None)
    if not mpv:
        raise SystemExit('找不到 libmpv.2.dylib。请设置 PLAYER_RUNTIME。')
    pending = [mpv]
    if include_tools:
        pending.extend(Path(binary(tool)) for tool in ('ffmpeg', 'ffprobe'))
    copied = set()
    while pending:
        source = pending.pop()
        if source.name in copied:
            continue
        copied.add(source.name)
        target = destination / source.name
        shutil.copy2(source, target)
        target.chmod(0o755)
        for link in dependencies(source):
            if link.startswith(('/System/Library/', '/usr/lib/')):
                continue
            name = Path(link).name
            if name == source.name and source.suffix == '.dylib':
                run('install_name_tool', '-id', f'@loader_path/{name}', target, stderr=subprocess.DEVNULL)
                continue
            found = next((directory / name for directory in (source.parent, *libdirs) if (directory / name).is_file()), None)
            if not found:
                # Static CLI builds may reference system frameworks with absolute paths.
                if Path(link).is_absolute() and Path(link).is_file():
                    found = Path(link)
                else:
                    raise SystemExit(f'找不到媒体依赖 {link}，由 {source.name} 引用')
            pending.append(found)
            if link != f'@loader_path/{name}':
                run('install_name_tool', '-change', link, f'@loader_path/{name}', target, stderr=subprocess.DEVNULL)
        run('codesign', '--force', '--sign', '-', target, stderr=subprocess.DEVNULL)


def finalize_mac(bundle):
    plist_path = bundle / 'Contents' / 'Info.plist'
    with plist_path.open('rb') as contents:
        data = plistlib.load(contents)
    data.update({'CFBundleDisplayName': '播放器', 'CFBundleShortVersionString': '1.0',
                 'CFBundleDocumentTypes': [
                     {'CFBundleTypeName': title, 'CFBundleTypeRole': 'Viewer', 'LSHandlerRank': 'Alternate',
                      'CFBundleTypeExtensions': [suffix[1:] for suffix in sorted(suffixes)]}
                     for title, suffixes in (('视频', VIDEO), ('图片', IMAGE))
                 ]})
    with plist_path.open('wb') as contents:
        plistlib.dump(data, contents)
    destination = bundle / 'Contents' / 'Frameworks' / 'media'
    destination.mkdir(exist_ok=True)
    copy_mac_runtime(destination)
    run('codesign', '--force', '--deep', '--sign', '-', bundle)
    run('codesign', '--verify', '--deep', '--strict', bundle)


def stage_windows_media(destination):
    copy_names = ('mpv-2.dll', 'libmpv-2.dll')
    mpv = next((directory / name for directory in runtime_dirs() for name in copy_names if (directory / name).is_file()), None)
    if mpv is None:
        raise SystemExit('找不到 mpv-2.dll 或 libmpv-2.dll。请设置 PLAYER_RUNTIME。')
    source_dirs = {mpv.parent}
    for tool in ('ffmpeg', 'ffprobe'):
        executable = Path(binary(tool))
        source_dirs.add(executable.parent)
        shutil.copy2(executable, destination)
    for directory in sorted(source_dirs):
        for dll in directory.glob('*.dll'):
            target = destination / dll.name
            if target.exists():
                if target.read_bytes() != dll.read_bytes():
                    raise SystemExit(f'Windows 媒体依赖包含同名的不同版本：{dll.name}')
                continue
            shutil.copy2(dll, target)
        for notice in directory.glob('*-LICENSE*'):
            if notice.is_file():
                shutil.copy2(notice, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, help='包含媒体二进制文件及动态库的目录')
    args = parser.parse_args()
    if args.runtime:
        os.environ['PLAYER_RUNTIME'] = str(args.runtime.resolve())
    if sys.platform not in ('darwin', 'win32'):
        parser.error('请在 macOS 或 Windows 本机打包。')
    mac = sys.platform == 'darwin'
    name = '播放器' if mac else 'Player-Windows-x64'
    outputs = ROOT / 'dist'
    qt_plugins = ROOT / '.build' / ('package' if mac else 'package-windows')
    qt_plugins.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('PYINSTALLER_CONFIG_DIR', str(qt_plugins / 'cache'))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--name', name,
               '--workpath', qt_plugins, '--specpath', qt_plugins, '--distpath', outputs,
               '--hidden-import', 'mpv']
    if mac:
        run(*command, '--onedir', '--osx-bundle-identifier', 'local.fullscreen.player', ROOT / 'app.py', cwd=ROOT)
        finalize_mac(outputs / f'{name}.app')
        shutil.rmtree(outputs / name)  # PyInstaller also writes an unbundled intermediate.
    else:
        if sysconfig.get_platform() != 'win-amd64':
            parser.error('请使用 Windows x64 版 Python 打包。')
        with tempfile.TemporaryDirectory(prefix='media-', dir=qt_plugins) as media_dir:
            stage_windows_media(Path(media_dir))
            # Windows loads these DLLs from the same directory as ffmpeg.exe.
            # The bundle root also lets PyInstaller deduplicate discovered imports.
            run(*command, '--onefile', '--add-data', f'{media_dir}{os.pathsep}.', ROOT / 'app.py', cwd=ROOT)


if __name__ == '__main__':
    main()
