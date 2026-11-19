from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

VIDEO = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.m4v', '.mpg', '.mpeg', '.ts', '.m2ts', '.wmv', '.ogv', '.3gp', '.vob', '.mts', '.mxf'}
IMAGE = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.avif'}
ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(getattr(sys, '_MEIPASS', ROOT))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def runtime_dirs():
    override = os.environ.get('PLAYER_RUNTIME')
    candidates = [Path(override)] if override else []
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve().parent
        if os.name == 'nt':
            candidates.append(ASSETS)
        candidates.extend([ASSETS / 'media', executable.parent / 'Frameworks' / 'media', executable / 'media'])
    candidates.extend([ROOT / '.runtime', ROOT / 'bin', Path('/opt/homebrew'), Path('/usr/local')])
    return [directory for base in candidates for directory in (base, base / 'tools', base / 'portable', base / 'mpv', base / 'bin', base / 'lib', base / 'Library' / 'bin') if directory.is_dir()]


def binary(name):
    filename = f'{name}.exe' if os.name == 'nt' else name
    for directory in runtime_dirs():
        path = directory / filename
        if path.is_file():
            return str(path)
    available = shutil.which(filename)
    if available:
        return available
    raise ValueError(f'找不到 {filename}。请按 README 配置媒体运行库。')


def capture(program, args, timeout=30):
    result = subprocess.run([binary(program), *map(str, args)], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', 'replace').strip()[-1800:] or f'{program} 退出，代码 {result.returncode}')
    return result.stdout


def number(value, fallback=0.0):
    try:
        parsed = float(Fraction(str(value)))
        return parsed if math.isfinite(parsed) else fallback
    except (ValueError, ZeroDivisionError):
        return fallback


def seconds(value):
    return f'{value:.6f}'.rstrip('0').rstrip('.') or '0'


def clock(value, milliseconds=False):
    if value is None:
        return '—'
    total = round(max(0, value) * 1000)
    whole, millis = divmod(total, 1000)
    hours, remainder = divmod(whole, 3600)
    mins, secs = divmod(remainder, 60)
    text = f'{hours:02}:{mins:02}:{secs:02}' if hours else f'{mins:02}:{secs:02}'
    return f'{text}.{millis:03}' if milliseconds else text


def natural_key(path):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r'(\d+)', path.name)]


@dataclass
class Media:
    path: Path
    data: dict

    @property
    def streams(self):
        return self.data.get('streams', [])

    def tracks(self, kind):
        return [track for track in self.streams if track.get('codec_type') == kind and not track.get('disposition', {}).get('attached_pic')]

    @property
    def video(self):
        return next(iter(self.tracks('video')), {})

    @property
    def audio(self):
        audio = self.tracks('audio')
        return next((track for track in audio if track.get('disposition', {}).get('default')), audio[0] if audio else {})

    @property
    def duration(self):
        duration = number(self.data.get('format', {}).get('duration')) or max((number(t.get('duration')) for t in self.streams), default=0)
        return duration if duration > 0 else None

    @property
    def fps(self):
        return number(self.video.get('avg_frame_rate')) or number(self.video.get('r_frame_rate'))

    @property
    def hdr(self):
        return {'smpte2084': 'HDR / PQ', 'arib-std-b67': 'HDR / HLG'}.get(self.video.get('color_transfer'))

    @property
    def shape(self):
        video = self.video
        width, height = video.get('width', 0), video.get('height', 0)
        rotation = next((d['rotation'] for d in video.get('side_data_list', []) if 'rotation' in d), video.get('tags', {}).get('rotate', 0))
        sar = number(str(video.get('sample_aspect_ratio', '1:1')).replace(':', '/'), 1) or 1
        return (height, width, 1 / sar) if round(number(rotation)) % 180 == 90 else (width, height, sar)


def probe(path):
    result = capture('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-show_chapters', '-of', 'json', path])
    return Media(path, json.loads(result))


def thumbnail(path, duration):
    seek = min(1, duration * .15) if duration and path.suffix.lower() not in IMAGE else 0
    args = ['-v', 'error', '-threads', '1', '-filter_threads', '1', '-i', path, '-map', '0:V:0', '-an', '-sn', '-frames:v', '1', '-vf', 'scale=176:-2', '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1']
    frame = capture('ffmpeg', args[:6] + ['-ss', seconds(seek)] + args[6:] if seek else args, timeout=20)
    return frame or (capture('ffmpeg', args, timeout=20) if seek else b'')


def track_label(track):
    info = {'video': '视频', 'audio': '音频', 'subtitle': '字幕', 'attachment': '附件', 'data': '数据'}
    tags = track.get('tags', {})
    detail = [f"#{track['index']} {info.get(track.get('codec_type'), '其他')}", track.get('codec_name', '未知').upper(), tags.get('language', ''), tags.get('title') or tags.get('handler_name') or '']
    if track.get('channels'):
        detail.append(f"{track['channels']} 声道")
    return ' · '.join(filter(None, detail))


@dataclass
class Export:
    label: str
    source: Path
    suffix: str
    args: list
    duration: float | None
    playback: bool = True


def require_video(media):
    if not media or not media.video:
        raise ValueError('请选择含视频轨的源文件。')


def map_video(media, audio=None):
    output = ['-map', f"0:{media.video['index']}"]
    if audio:
        output += ['-map', f"0:{audio['index']}"]
    return output


def even(pixels):
    return max(2, round(pixels / 2) * 2)


def canvas_filter(width, height, fill=False):
    if fill:
        return f'scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2:reset_sar=1,crop={width}:{height}'
    return f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2'


def square_pixels(media, height=None):
    width, source_height, sar = media.shape
    if height:
        return f'scale={even(width * sar * height / source_height)}:{even(height)},setsar=1'
    if width % 2 or source_height % 2:
        return 'pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0'
    return None


def tone_map(media):
    if not media.hdr:
        return []
    video = media.video
    matrix = video.get('color_space')
    primaries = video.get('color_primaries')
    matrix = matrix if matrix and matrix != 'unknown' else 'bt2020nc'
    primaries = primaries if primaries and primaries != 'unknown' else 'bt2020'
    return [f"zscale=matrixin={matrix}:primariesin={primaries}:transferin={video['color_transfer']}:transfer=linear:npl=100", 'format=gbrpf32le', 'zscale=primaries=bt709', 'tonemap=mobius:desat=1.5', 'zscale=transfer=bt709:matrix=bt709:range=limited', 'format=yuv420p']


def h264(filters=(), audio=False, bitrate=None, sdr=False, still=False):
    args = []
    if filters:
        args += ['-vf', ','.join(filters)]
    args += ['-c:v', 'libx264', '-preset', 'medium', '-pix_fmt', 'yuv420p']
    if bitrate:
        args += ['-b:v', f'{bitrate}k', '-maxrate', f'{bitrate}k', '-bufsize', f'{bitrate * 2}k']
    else:
        args += ['-crf', '21' if still else '22']
    if still:
        args += ['-tune', 'stillimage']
    if sdr:
        args += ['-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv']
    if audio:
        args += ['-c:a', 'aac', '-b:a', '192k', '-ac', '2'] if audio == 'stereo' else ['-c:a', 'aac', '-b:a', '192k']
    return [*args, '-movflags', '+faststart']


def compression(media, audio, height=None, bitrate=None, fps=None, canvas=None, fill=False):
    require_video(media)
    width, original_height, _ = media.shape
    if not canvas and not any((height, bitrate, fps)):
        raise ValueError('至少选择一个压缩参数。')
    if height and height > original_height:
        raise ValueError('目标高度超过源视频高度。')
    source_bitrate = number(media.video.get('bit_rate')) / 1000
    if bitrate and source_bitrate and bitrate >= source_bitrate:
        raise ValueError(f'视频源码率约 {source_bitrate:.0f} kbps，请选择更低的目标。')
    filters = tone_map(media)
    resize = canvas_filter(*canvas, fill=fill) if canvas else square_pixels(media, height)
    if resize:
        filters.append(resize)
    # A lower fixed rate is used only when the reported source rate exceeds it.
    if fps and (not media.fps or media.fps > fps + .001):
        filters.append(f'fps={fps}')
    args = ['-i', media.path, *map_video(media, audio), '-fps_mode:v', 'vfr']
    args += h264(filters, 'stereo' if canvas and audio else bool(audio), bitrate, sdr=bool(media.hdr))
    label, suffix = ('导出大屏兼容版', '_screen.mp4') if canvas else ('压缩视频', '_compressed.mp4')
    return Export(label, media.path, suffix, args, media.duration)


def clip(media, start, end, quick=False):
    require_video(media)
    if not 0 <= start < end or (media.duration and end > media.duration + .001):
        raise ValueError('请设置有效的起止时间。')
    if quick:
        # Input seeking preserves the seek point for stream copy. Keep the original
        # timeline for the output cutoff, then shift the muxed clip to nonnegative time.
        args = ['-ss', seconds(start), '-copyts', '-start_at_zero', '-i', media.path, '-to', seconds(end),
                *map_video(media, media.audio), '-map_chapters', '-1', '-c', 'copy', '-avoid_negative_ts', 'make_zero']
        return Export('快速截取片段', media.path, '_clip.mkv', args, None)
    filters = tone_map(media)
    resize = square_pixels(media)
    if resize:
        filters.append(resize)
    args = ['-ss', seconds(start), '-i', media.path, '-t', seconds(end - start), *map_video(media, media.audio), '-map_chapters', '-1', '-fps_mode:v', 'vfr']
    args += h264(filters, bool(media.audio), sdr=bool(media.hdr))
    return Export('精确截取片段', media.path, '_clip.mp4', args, end - start)


def playlist_images(items, fps, canvas=None, fade=False):
    if not items or any(not 1 <= duration <= 3600 for _, duration in items):
        raise ValueError('请添加图片，并将每张的时长设为 1–3600 秒。')
    source = items[0][0]
    if len(items) == 1 and source.suffix.lower() == '.gif':
        raise ValueError('请选择静态图片，或在播放器中用「保存当前帧」获取 GIF 的静帧。')
    if any(path.suffix.lower() == '.gif' for path, _ in items):
        raise ValueError('图片轮播使用静态图片。可先保存 GIF 的某一帧。')
    if not canvas:
        width, height, _ = probe(source).shape
        if not width or not height:
            raise ValueError('无法读取首张图片的尺寸。')
        canvas = (width + width % 2, height + height % 2)
    base = canvas_filter(*canvas)
    args, graph = [], []
    for index, (path, duration) in enumerate(items):
        args += ['-loop', '1', '-framerate', str(fps), '-t', seconds(duration), '-i', path]
        filters = [base, 'format=yuv420p', 'setpts=PTS-STARTPTS']
        if fade:
            length = min(.4, duration / 3)
            filters += [f'fade=t=in:d={seconds(length)}', f'fade=t=out:st={seconds(duration - length)}:d={seconds(length)}']
        graph.append(f'[{index}:v]{",".join(filters)}[img{index}]')
    graph.append(''.join(f'[img{i}]' for i in range(len(items))) + f'concat=n={len(items)}:v=1:a=0[out]')
    args += ['-filter_complex_threads', '2', '-filter_complex', ';'.join(graph), '-map', '[out]', '-an', *h264(still=True)]
    return Export('图片轮播转视频' if len(items) > 1 else '图片转视频', source, '_video.mp4', args, sum(duration for _, duration in items))


MP4_COPY = {
    'video': {'h264', 'hevc', 'av1', 'mpeg4', 'mpeg2video', 'vp9'},
    'audio': {'aac', 'mp3', 'ac3', 'eac3', 'alac', 'opus'},
    'subtitle': {'mov_text'},
}
TEXT_SUBS = {'mov_text', 'subrip', 'ass', 'ssa', 'webvtt', 'text', 'ttml'}


def can_copy(track, container):
    if container == 'mp4':
        return track.get('codec_name') in MP4_COPY.get(track.get('codec_type'), set())
    return track.get('codec_type') in {'video', 'audio', 'subtitle', 'attachment'} and track.get('codec_name') != 'mov_text'


def can_convert(track):
    return track.get('codec_type') in {'video', 'audio'} or track.get('codec_name') in TEXT_SUBS


def remux(media, container, selected):
    if not selected:
        raise ValueError('至少选择一条轨道。')
    args = ['-i', media.path, '-c', 'copy']
    for index, (track, action) in enumerate(selected):
        kind = track['codec_type']
        args += ['-map', f"0:{track['index']}"]
        if action == '转码':
            codec = {'video': 'libx264', 'audio': 'aac', 'subtitle': 'mov_text' if container == 'mp4' else 'srt'}[kind]
            args += [f'-c:{index}', codec]
            if kind == 'video':
                vf = tone_map(media) if track['index'] == media.video.get('index') else []
                vf += ['pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0']
                args += [f'-filter:{index}', ','.join(vf), f'-pix_fmt:{index}', 'yuv420p']
                if len(vf) > 1:
                    args += [f'-color_primaries:{index}', 'bt709', f'-color_trc:{index}', 'bt709', f'-colorspace:{index}', 'bt709']
    if container == 'mp4':
        args += ['-movflags', '+faststart']
    return Export('换封装', media.path, f'_remux.{container}', args, media.duration,
                  playback=any(t['codec_type'] == 'video' for t, _ in selected))


def extract(media, track, kind):
    if not track:
        raise ValueError('该文件没有可提取的音轨。')
    extension, codec = {'原编码': ('mka', 'copy'), 'AAC': ('m4a', 'aac'), 'WAV': ('wav', 'pcm_s16le'), 'FLAC': ('flac', 'flac')}[kind]
    return Export('提取音轨', media.path, f"_audio{track['index']}.{extension}",
                  ['-i', media.path, '-map', f"0:{track['index']}", '-c:a', codec, '-map_chapters', '-1'], media.duration, playback=False)


def inspect_media(media):
    require_video(media)
    args = ['-i', media.path, *map_video(media, media.audio), '-vf', 'blackdetect=d=0.5:pix_th=0.1,freezedetect=n=-55dB:d=2']
    if media.audio:
        args += ['-af', 'silencedetect=n=-45dB:d=1']
    return args + ['-sn', '-dn', '-f', 'null', '-']


def findings(log, duration=None):
    results = []
    for start, end in re.findall(r'black_start:([\d.e+\-]+) black_end:([\d.e+\-]+)', log):
        results.append((number(start), number(end), '黑场'))
    for pattern, title in [('silence', '静音'), ('freeze', '冻帧')]:
        start = None
        for event, value in re.findall(rf'{pattern}_(start|end):\s*([\d.e+\-]+)', log):
            if event == 'start':
                start = number(value)
            elif start is not None:
                results.append((start, number(value), title))
                start = None
        if start is not None and duration and duration > start:
            results.append((start, duration, title))
    return sorted(results)
