from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QSize, Qt, QThreadPool, QTimer, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow,
    QProgressBar, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from . import media
from .jobs import FFmpegJob, Worker, WorkerResults, publish, temporary_dir
from .playback import Playback
from .tools import Tools, button, combo, label, page, row


STYLE = '''
QWidget { color: #e5ecf1; font-size: 13px; }
QMainWindow, QWidget#empty { background: #07090b; }
QFrame#sidebar { background: #11171d; border-left: 1px solid #27343c; }
QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget { background: transparent; border: none; }
QLabel#title { font-size: 19px; font-weight: 700; }
QLabel#hero { color: #eef6f7; font-size: 34px; font-weight: 700; }
QLabel#heading { color: #dce8ed; font-size: 14px; font-weight: 700; }
QLabel#hint { color: #a1b2be; }
QLabel#status { color: #accacb; }
QLabel#trackTitle { color: #cbd9e0; }
QFrame#rule { border: none; background: #28353d; }
QPushButton { background: #1b252e; border: 1px solid #34444f; border-radius: 7px; padding: 7px 10px; }
QPushButton:hover { background: #253743; border-color: #56717e; }
QPushButton:pressed { background: #10191d; }
QPushButton:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #72818a; background: #171e24; border-color: #253039; }
QPushButton#primary { background: #297d7e; border-color: #419b99; color: #f5ffff; font-weight: 700; }
QPushButton#primary:hover { background: #338e8e; }
QPushButton#primary:disabled { color: #718889; background: #223538; border-color: #2d4245; }
QPushButton#tab { background: transparent; border-color: transparent; color: #a6b7c2; font-weight: 600; }
QPushButton#tab:checked { background: #1c3237; border-color: #345c5e; color: #9be0d9; }
QPushButton#tab:hover { background: #202d34; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #151e26; border: 1px solid #354650; border-radius: 6px; padding: 6px 8px; min-height: 17px; }
QComboBox QAbstractItemView { background: #1d2931; selection-background-color: #326568; color: #eff8fa; }
QCheckBox { spacing: 7px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { background: #172027; border: 1px solid #26343e; border-radius: 8px; padding: 7px; margin: 0 1px 5px 0; }
QListWidget::item:hover { background: #202e38; }
QListWidget::item:selected { background: #1b3639; color: #eaffff; border-color: #427374; }
QListWidget#compactList::item { margin-bottom: 3px; padding: 4px 7px; }
QProgressBar { border: 0; background: #25333d; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #5cbeb7; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #3a4d58; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { color: #e2edf1; background: #1e2b33; border: 1px solid #4a636f; }
'''


def rule():
    widget = QFrame()
    widget.setObjectName('rule')
    widget.setFixedHeight(1)
    return widget


class Window(QMainWindow):
    worker_finished = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('播放器')
        self.setMinimumSize(720, 440)
        self.resize(1200, 760)
        self.setAcceptDrops(True)
        self.current_path = None
        self.current_media = None
        self.folder_paths = []
        self.cache = OrderedDict()
        self.pending_previews = set()
        self.panel_open = True
        self.scan_media = None
        self.preparing = False
        self.output = None
        self.active = True
        self.closing = False
        self.video_filter = self.make_filter('视频', media.VIDEO)
        self.image_filter = self.make_filter('静态图片', media.IMAGE - {'.gif'})
        self.all_filter = self.make_filter('媒体文件', media.VIDEO | media.IMAGE)
        self.worker_finished.connect(self.worker_result, Qt.ConnectionType.QueuedConnection)
        self.worker_results = WorkerResults(self.worker_finished)
        self.workers = QThreadPool(self)
        self.workers.setMaxThreadCount(3)
        self.previews = QThreadPool(self)
        self.previews.setMaxThreadCount(2)
        self.job = FFmpegJob(self)
        self.job.progress.connect(self.update_progress)
        self.job.completed.connect(self.job_finished)
        self.hide_cursor = QTimer(self, singleShot=True, interval=1500)
        self.hide_cursor.timeout.connect(self.hide_pointer)
        self.position_timer = QTimer(self, interval=160)
        self.position_timer.timeout.connect(self.tick)
        self.position_timer.start()

        root = QWidget()
        self.layout = QHBoxLayout(root)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setCentralWidget(root)
        self.canvas = QStackedWidget()
        self.layout.addWidget(self.canvas, 1)
        self.empty, empty_layout = page()
        self.empty.setObjectName('empty')
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setContentsMargins(30, 30, 30, 30)
        title = label('播放器', 'hero')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt = label('拖入视频或图片，或选择本地文件')
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(title)
        empty_layout.addWidget(prompt)
        empty_layout.addSpacing(6)
        empty_layout.addWidget(button('打开文件', self.choose_media, True), alignment=Qt.AlignmentFlag.AlignHCenter)
        self.canvas.addWidget(self.empty)
        self.video = Playback()
        self.video.loaded.connect(self.on_loaded)
        self.video.failed.connect(self.on_playback_error)
        self.video.moved.connect(self.mouse_visible)
        self.canvas.addWidget(self.video)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('sidebar')
        self.layout.addWidget(self.sidebar)
        self.setup_sidebar()
        self.animation = QVariantAnimation(self)
        self.animation.valueChanged.connect(self.sidebar.setFixedWidth)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self.panel_animation_finished)
        self.sidebar.setFixedWidth(self.sidebar_width())
        self.setup_shortcuts()
        self.tools.set_current(None)

    @staticmethod
    def make_filter(name, suffixes):
        return name + ' (' + ' '.join('*' + s for s in sorted(suffixes)) + ');;所有文件 (*)'

    def setup_sidebar(self):
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label('播放器', 'title'))
        heading.addStretch(1)
        full = button('全屏', self.toggle_fullscreen)
        full.setToolTip('F11 / Esc')
        close = button('隐藏  P', self.toggle_panel)
        heading.addWidget(full)
        heading.addWidget(close)
        layout.addLayout(heading)
        self.files_tab = button('文件', lambda: self.set_tab(0))
        self.tools_tab = button('工具', lambda: self.set_tab(1))
        for tab in (self.files_tab, self.tools_tab):
            tab.setCheckable(True)
            tab.setObjectName('tab')
        layout.addLayout(row(self.files_tab, self.tools_tab))
        layout.addWidget(rule())
        self.file_name = label('尚未打开文件', 'heading')
        self.file_info = label()
        self.details_button = button('详细信息', self.toggle_details)
        self.details_button.setCheckable(True)
        self.frame_format = combo(['PNG', 'JPEG'])
        self.frame_button = button('保存当前帧', self.save_frame)
        self.frame_button.setEnabled(False)
        self.frame_format.setEnabled(False)
        self.details_button.setEnabled(False)
        self.details = label()
        self.details.hide()
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.file_name)
        layout.addWidget(self.file_info)
        layout.addWidget(self.details)
        layout.addLayout(row(self.details_button, self.frame_format, self.frame_button))
        layout.addWidget(rule())
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        files, files_layout = page()
        self.folder_title = label('当前文件夹', 'heading')
        files_layout.addLayout(row(self.folder_title, button('打开文件', self.choose_media)))
        self.folder_info = label('打开媒体后显示同目录视频。')
        files_layout.addWidget(self.folder_info)
        self.file_list = QListWidget()
        self.file_list.setUniformItemSizes(True)
        self.file_list.setIconSize(QSize(83, 48))
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.itemClicked.connect(self.activate_file)
        self.file_list.itemActivated.connect(self.activate_file)
        self.file_list.verticalScrollBar().valueChanged.connect(self.request_previews)
        files_layout.addWidget(self.file_list, 1)
        self.pages.addWidget(files)
        self.tools = Tools(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.tools)
        self.pages.addWidget(self.scroll)
        self.set_tab(0)
        self.status_box, status_layout = page()
        status_layout.addWidget(rule())
        self.status = label('', 'status')
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_layout.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        status_layout.addWidget(self.progress_bar)
        self.cancel = button('取消任务', self.job.cancel)
        self.play_output = button('播放导出文件', self.open_output, True)
        self.reveal_output = button('打开所在文件夹', self.reveal)
        status_layout.addLayout(row(self.cancel, self.play_output, self.reveal_output))
        layout.addWidget(self.status_box)
        self.status_box.hide()

    def set_tab(self, tab):
        self.pages.setCurrentIndex(tab)
        self.files_tab.setChecked(tab == 0)
        self.tools_tab.setChecked(tab == 1)
        if tab == 0:
            QTimer.singleShot(0, self.request_previews)

    def sidebar_width(self):
        return min(max(round(self.width() / 3), 360), max(360, self.width() - 320))

    def panel_animation_finished(self):
        if not self.panel_open:
            self.sidebar.hide()
        elif self.pages.currentIndex() == 0:
            self.highlight()
            self.request_previews()
        self.mouse_visible()

    def toggle_panel(self):
        self.set_panel(not self.panel_open)

    def set_panel(self, visible, animate=True):
        if self.panel_open == visible:
            return
        self.panel_open = visible
        self.animation.stop()
        start = self.sidebar.width() if self.sidebar.isVisible() else 0
        end = self.sidebar_width() if visible else 0
        if visible:
            self.sidebar.show()
            QTimer.singleShot(0, self.request_previews)
        else:
            self.video.setFocus()
        if animate:
            self.animation.setStartValue(start)
            self.animation.setEndValue(end)
            self.animation.start()
        else:
            self.sidebar.setFixedWidth(end)
            self.panel_animation_finished()

    def resizeEvent(self, event):
        if hasattr(self, 'animation') and self.panel_open and self.animation.state() == QAbstractAnimation.State.Stopped:
            self.sidebar.setFixedWidth(self.sidebar_width())
        super().resizeEvent(event)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def choose_media(self):
        filename, _ = QFileDialog.getOpenFileName(self, '打开媒体', str(self.current_path.parent) if self.current_path else '', self.all_filter)
        if filename:
            self.open_media(Path(filename))

    def open_media(self, path, from_list=False):
        path = Path(os.path.abspath(path.expanduser()))
        try:
            exists = path.is_file()
        except OSError:
            exists = False
        if not exists:
            self.set_panel(True)
            self.show_status('无法打开文件', str(path))
            return
        location_changed = not self.current_path or self.current_path.parent != path.parent
        self.current_path = path
        self.current_media = None
        if not from_list:
            self.set_panel(False, animate=False)
            self.showFullScreen()
        self.file_name.setText(path.name)
        self.file_name.setToolTip(str(path))
        self.file_info.setText('正在读取媒体信息…')
        self.details.hide()
        self.details_button.setChecked(False)
        self.details_button.setEnabled(False)
        self.frame_button.setEnabled(False)
        self.frame_format.setEnabled(False)
        self.tools.set_current(None)
        self.canvas.setCurrentWidget(self.video)
        self.video.open(path)
        self.video.setFocus()
        self.mouse_visible()
        if location_changed:
            self.file_list.clear()
            self.folder_paths = []
            self.folder_info.setText('正在读取文件夹…')
            self.work(lambda: self.scan_folder(path.parent), self.folder_ready)
        self.highlight()
        self.work(lambda: (path, media.probe(path)), lambda value, error: self.media_ready(path, value, error))

    def on_loaded(self, filename):
        if self.current_path and Path(filename) == self.current_path:
            self.frame_button.setEnabled(True)
            self.frame_format.setEnabled(True)

    def on_playback_error(self, error):
        self.set_panel(True)
        self.frame_button.setEnabled(False)
        self.frame_format.setEnabled(False)
        self.show_status('播放失败', error)

    def media_ready(self, path, result, error):
        if path != self.current_path:
            return
        if error:
            self.file_info.setText('读取信息失败')
            self.file_info.setToolTip(error)
            return
        self.current_media = result[1]
        data = self.current_media
        width, height, sar = data.shape
        summary = [f'{width} × {height}' if width and height else '', f'{data.fps:.3g} fps' if data.fps and path.suffix.lower() not in media.IMAGE else '', data.video.get('codec_name', '').upper(), data.hdr or ('SDR' if path.suffix.lower() not in media.IMAGE and data.video else ''), f'{len(data.tracks("audio"))} 条音轨']
        self.file_info.setText(' · '.join(filter(None, summary)))
        self.file_info.setToolTip('')
        file_format = data.data.get('format', {})
        details = [f'时长 {media.clock(data.duration, True)}']
        if file_format.get('format_long_name'):
            details.append(file_format['format_long_name'])
        if media.number(file_format.get('bit_rate')):
            details.append(f'总码率 {media.number(file_format["bit_rate"]) / 1000:,.0f} kbps')
        if sar != 1:
            details.append(f'像素宽高比 {sar:.3f}')
        details.extend(media.track_label(track) for track in data.streams)
        for chapter in data.data.get('chapters', []):
            details.append(f'{media.clock(media.number(chapter.get("start_time")))}  {chapter.get("tags", {}).get("title", "章节")}')
        self.details.setText('\n'.join(details))
        self.details_button.setEnabled(True)
        self.tools.set_current(data)
        self.request_previews()

    def toggle_details(self, checked):
        self.details.setVisible(checked)
        self.details_button.setText('收起信息' if checked else '详细信息')

    @staticmethod
    def scan_folder(directory):
        return directory, sorted((path for path in directory.iterdir() if path.suffix.lower() in media.VIDEO and path.is_file()), key=media.natural_key)

    def folder_ready(self, result, error):
        if error:
            if not self.folder_paths:
                self.folder_info.setText('无法读取目录。')
                self.folder_info.setToolTip(error)
            return
        if not self.current_path or result[0] != self.current_path.parent:
            return
        folder, self.folder_paths = result
        self.folder_info.setText(f'{folder.name} · {len(self.folder_paths)} 部视频')
        self.folder_info.setToolTip(str(folder))
        self.file_list.clear()
        for path in self.folder_paths:
            item = QListWidgetItem(f'{path.name}\n—')
            item.setToolTip(str(path))
            item.setSizeHint(QSize(0, 64))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)
        self.highlight()
        self.request_previews()

    def highlight(self):
        index = self.folder_paths.index(self.current_path) if self.current_path in self.folder_paths else -1
        self.file_list.setCurrentRow(index)
        if index >= 0:
            self.file_list.scrollToItem(self.file_list.item(index))

    def activate_file(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path != self.current_path:
            self.open_media(path, from_list=True)

    def adjacent(self, direction):
        if self.folder_paths:
            if self.current_path in self.folder_paths:
                index = self.folder_paths.index(self.current_path) + direction
            else:
                index = 0 if direction > 0 else -1
            self.open_media(self.folder_paths[index % len(self.folder_paths)], from_list=True)

    def request_previews(self, _=None):
        if not self.panel_open or self.pages.currentIndex() != 0 or not self.folder_paths:
            return
        viewport = self.file_list.viewport()
        start = self.file_list.indexAt(viewport.rect().topLeft()).row()
        start = max(0, start if start >= 0 else self.file_list.verticalScrollBar().value())
        stop = min(len(self.folder_paths), start + max(3, viewport.height() // 57 + 3))
        for index in range(start, stop):
            item = self.file_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole + 1):
                continue
            path = self.folder_paths[index]
            try:
                info = path.stat()
            except OSError:
                continue
            key = (path, info.st_mtime_ns, info.st_size)
            if key in self.cache:
                self.cache.move_to_end(key)
                self.apply_preview(item, path, self.cache[key])
            elif key not in self.pending_previews and len(self.pending_previews) < 14:
                self.pending_previews.add(key)
                self.previews.start(Worker(lambda p=path: self.preview_for(p), lambda data, error, k=key: self.preview_ready(k, data), self.worker_results))

    @staticmethod
    def preview_for(path):
        duration = media.probe(path).duration
        try:
            frame = media.thumbnail(path, duration)
        except (OSError, ValueError):
            frame = b''
        return duration, frame

    def preview_ready(self, key, result):
        self.pending_previews.discard(key)
        self.cache[key] = result or (None, b'')
        if len(self.cache) > 320:
            self.cache.popitem(last=False)
        path = key[0]
        if path in self.folder_paths:
            self.apply_preview(self.file_list.item(self.folder_paths.index(path)), path, self.cache[key])
        self.request_previews()

    @staticmethod
    def apply_preview(item, path, preview):
        duration, frame = preview
        item.setData(Qt.ItemDataRole.UserRole + 1, True)
        item.setText(f'{path.name}\n{media.clock(duration)}')
        if frame:
            preview_pixmap = QPixmap()
            if preview_pixmap.loadFromData(frame):
                item.setIcon(QIcon(preview_pixmap))

    def tick(self):
        if self.panel_open and self.pages.currentIndex() == 1 and self.tools.picker.currentIndex() == 2:
            self.tools.update_position(self.video.position())

    def work(self, task, callback):
        self.workers.start(Worker(task, callback, self.worker_results))

    @Slot(object, object, object)
    def worker_result(self, callback, result, error):
        if not self.closing:
            callback(result, error)

    def busy(self):
        return self.job.working or self.preparing

    def export(self, planner):
        if self.busy():
            self.show_status('请等待当前任务完成，或先取消。')
            return
        try:
            export = planner()
            self.start_job(export.label, export.args, export.duration, export)
        except (OSError, ValueError) as error:
            self.show_status('无法开始导出', str(error))

    def prepare_export(self, planner):
        if self.busy():
            self.show_status('请等待当前任务完成，或先取消。')
            return
        self.preparing = True
        self.show_status('正在读取源文件…')
        self.work(planner, self.prepared)

    def prepared(self, export, error):
        self.preparing = False
        if error:
            self.show_status('无法开始导出', error)
        else:
            self.export(lambda: export)

    def start_job(self, title, args, duration, export=None, scan=None):
        self.scan_media = scan
        self.output = None
        self.job.start(title, args, duration, export)

    def update_progress(self, percent, name):
        self.status_box.show()
        self.play_output.hide()
        self.reveal_output.hide()
        self.status.setText(name if percent is None else f'{name} · {percent}%')
        self.status.setToolTip('')
        self.progress_bar.setRange(0, 0 if percent is None else 100)
        if percent is not None:
            self.progress_bar.setValue(percent)
        self.progress_bar.show()
        self.cancel.show()
        self.cancel.setEnabled(not self.job.stopping)

    def job_finished(self, output, error, canceled, log):
        if canceled:
            self.show_status('任务已取消')
        elif error:
            self.show_status('处理失败', error)
        elif output:
            self.show_status(f'已导出 {output.name}', output=output, playable=self.job.export.playback)
            if self.current_path and output.parent == self.current_path.parent and output.suffix.lower() in media.VIDEO:
                self.work(lambda: self.scan_folder(output.parent), self.folder_ready)
        elif self.scan_media:
            self.tools.show_findings(self.scan_media, log)
            self.show_status('素材体检完成')
        self.scan_media = None

    def show_status(self, title, detail='', output=None, playable=False):
        self.status_box.show()
        if self.job.working:
            self.status.setToolTip(detail or title)
            return
        self.output = output
        self.status.setText(title + ('\n' + '\n'.join(detail.strip().splitlines()[-2:])[:260] if detail else ''))
        self.status.setToolTip(detail or (str(output) if output else ''))
        self.progress_bar.hide()
        self.cancel.hide()
        self.play_output.setVisible(bool(output and playable))
        self.reveal_output.setVisible(bool(output))

    def open_output(self):
        if self.output:
            self.open_media(self.output, from_list=True)

    def reveal(self):
        if self.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output.parent)))

    def save_frame(self):
        source = self.current_path
        if not source or self.busy():
            self.show_status('请等待当前任务完成，或先取消。')
            return
        try:
            suffix = '_frame.png' if self.frame_format.currentData() == 'PNG' else '_frame.jpg'
            with temporary_dir(source) as directory:
                temporary = Path(directory) / suffix
                self.video.save_frame(temporary)
                output = publish(temporary, source, suffix)
            self.show_status(f'已保存 {output.name}', output=output, playable=True)
        except (OSError, ValueError, RuntimeError, SystemError) as error:
            self.show_status('保存当前帧失败', str(error))

    def mouse_visible(self):
        self.video.unsetCursor()
        if self.active and not self.panel_open and self.current_path:
            self.hide_cursor.start()
        else:
            self.hide_cursor.stop()

    def hide_pointer(self):
        if self.active and not self.panel_open:
            self.video.setCursor(Qt.CursorShape.BlankCursor)

    def setup_shortcuts(self):
        window_shortcuts = {
            'P': self.toggle_panel, 'Shift+P': self.toggle_panel, 'O': self.choose_media,
            'F11': self.toggle_fullscreen, 'Esc': self.toggle_fullscreen,
        }
        playback_shortcuts = {'Space': self.video.toggle_pause,
                              'Left': lambda: self.adjacent(-1), 'Right': lambda: self.adjacent(1)}
        self.shortcuts = []
        for mapping, widget, context in (
            (window_shortcuts, self, Qt.ShortcutContext.WindowShortcut),
            (playback_shortcuts, self.video, Qt.ShortcutContext.WidgetShortcut),
        ):
            for keys, action in mapping.items():
                shortcut = QShortcut(QKeySequence(keys), widget)
                shortcut.setContext(context)
                shortcut.setAutoRepeat(False)
                shortcut.activated.connect(action)
                self.shortcuts.append(shortcut)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and hasattr(self, 'video'):
            self.active = self.isActiveWindow()
            self.mouse_visible()
        super().changeEvent(event)

    def dragEnterEvent(self, event):
        if any(url.isLocalFile() and Path(url.toLocalFile()).is_file() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).is_file():
                self.open_media(Path(url.toLocalFile()))
                event.acceptProposedAction()
                break

    def closeEvent(self, event):
        self.closing = True
        self.worker_results.close()
        self.job.shutdown()
        self.video.shutdown()
        self.workers.clear()
        self.previews.clear()
        super().closeEvent(event)
