from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import os
import sys
from unittest.mock import patch

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .media import runtime_dirs


def load_mpv():
    names = ('mpv-2.dll', 'libmpv-2.dll') if os.name == 'nt' else ('libmpv.2.dylib', 'libmpv.dylib', 'libmpv.so.2')
    library = next((directory / name for directory in runtime_dirs() for name in names if (directory / name).is_file()), None)
    if library is None:
        return importlib.import_module('mpv')
    original = ctypes.util.find_library

    def find(name):
        return str(library) if name in (*names, 'mpv') else original(name)

    with patch.object(ctypes.util, 'find_library', find):
        return importlib.import_module('mpv')


def gl_resolver():
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/System/Library/Frameworks/OpenGL.framework/OpenGL')
        extension = None
    elif os.name == 'nt':
        library = ctypes.WinDLL('opengl32.dll')
        extension = library.wglGetProcAddress
    else:
        library = ctypes.CDLL('libGL.so.1')
        extension = library.glXGetProcAddressARB
    if extension:
        extension.argtypes = [ctypes.c_char_p]
        extension.restype = ctypes.c_void_p

    def resolve(_, name):
        symbol = getattr(library, name.decode('ascii'), None)
        if symbol is not None:
            return ctypes.cast(symbol, ctypes.c_void_p).value
        address = extension(name) if extension else None
        return address if address not in (1, 2, 3, ctypes.c_void_p(-1).value) else None

    return resolve


class Playback(QOpenGLWidget):
    render_requested = Signal()
    loaded = Signal(str)
    failed = Signal(str)
    ready = Signal()
    moved = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('playback')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mpv = None
        self.renderer = None
        self.pending = None
        self.logs = []
        self.render_requested.connect(self.update, Qt.ConnectionType.QueuedConnection)

    def initializeGL(self):
        try:
            if self.mpv is None:
                self.binding = load_mpv()
                self.mpv = self.binding.MPV(vo='libmpv', config=False, terminal=False,
                                            osd_level=0, input_default_bindings=False,
                                            input_vo_keyboard=False, loop_file='inf',
                                            hwdec='auto-safe', keep_open=True, keepaspect=True,
                                            image_display_duration='inf', sid='no',
                                            log_handler=self.capture_log, loglevel='warn')
                for option in ('osc', 'ytdl'):
                    try:
                        self.mpv.command('set', option, 'no')
                    except (AttributeError, ValueError, RuntimeError, SystemError):
                        pass  # These options are absent in libmpv builds without Lua.
                self.mpv.event_callback('file-loaded', 'end-file')(self.handle_event)
            self.resolve_gl = self.binding.MpvGlGetProcAddressFn(gl_resolver())
            self.renderer = self.binding.MpvRenderContext(self.mpv, 'opengl', opengl_init_params={'get_proc_address': self.resolve_gl})
            self.renderer.update_cb = self.render_requested.emit
            self.context().aboutToBeDestroyed.connect(self.release_renderer, Qt.ConnectionType.DirectConnection)
            self.ready.emit()
            if self.pending:
                self.open(self.pending)
        except (OSError, ValueError, RuntimeError, SystemError, AttributeError) as error:
            self.failed.emit(f'播放内核初始化失败。{error}')

    def capture_log(self, level, prefix, text):
        if level in ('error', 'fatal'):
            self.logs = [*self.logs[-5:], text.strip()]

    def handle_event(self, event):
        identifier = event.event_id.value
        if identifier == self.binding.MpvEventID.FILE_LOADED:
            try:
                self.loaded.emit(str(self.mpv.path or ''))
            except (AttributeError, SystemError):
                pass
        elif identifier == self.binding.MpvEventID.END_FILE:
            if event.data.reason == self.binding.MpvEventEndFile.ERROR:
                self.failed.emit('无法播放当前媒体。' + ('\n' + self.logs[-1] if self.logs else ''))

    def open(self, path):
        if not self.renderer:
            self.pending = path
            return
        self.pending = None
        self.logs = []
        self.set_loop()
        self.mpv.pause = False
        self.mpv.loadfile(str(path))

    def toggle_pause(self):
        if self.mpv:
            self.mpv.pause = not self.mpv.pause

    def position(self):
        return self.mpv.time_pos if self.mpv else None

    def seek(self, position):
        if self.position() is not None:
            self.mpv.command('seek', position, 'absolute+exact')

    def set_loop(self, start=None, end=None):
        if self.mpv:
            self.mpv.ab_loop_a = 'no' if start is None else start
            self.mpv.ab_loop_b = 'no' if end is None else end
        if start is not None:
            self.seek(start)
            self.mpv.pause = False

    def save_frame(self, path):
        if not self.mpv or not self.mpv.path:
            raise ValueError('请先打开媒体并等待画面显示。')
        frame = self.mpv.command('screenshot-raw', 'video')
        if not frame or frame['format'] != 'bgr0':
            raise ValueError('播放器没有返回可保存的画面。')
        pixels = QImage(frame['data'], frame['w'], frame['h'], frame['stride'], QImage.Format.Format_RGB32)
        if not pixels.convertToFormat(QImage.Format.Format_RGB888).save(str(path)):
            raise ValueError('无法写入截图文件。')

    def paintGL(self):
        if self.renderer:
            ratio = self.devicePixelRatioF()
            self.renderer.render(flip_y=True, opengl_fbo={
                'fbo': self.defaultFramebufferObject(), 'w': round(self.width() * ratio), 'h': round(self.height() * ratio),
            })

    def mouseMoveEvent(self, event):
        self.moved.emit()
        super().mouseMoveEvent(event)

    @Slot()
    def release_renderer(self):
        if self.renderer:
            self.makeCurrent()
            self.renderer.update_cb = None
            self.renderer.free()
            self.renderer = None
            self.doneCurrent()

    def shutdown(self):
        self.release_renderer()
        if self.mpv:
            handle, self.mpv = self.mpv, None
            handle.terminate()
