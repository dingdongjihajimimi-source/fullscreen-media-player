from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import media as engine


def label(text='', role='hint'):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(role)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(callback)
    return widget


def combo(values):
    widget = QComboBox()
    for item in values:
        name, data = item if isinstance(item, tuple) else (item, item)
        widget.addItem(name, data)
    return widget


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


def spin(minimum, maximum, value, suffix='', decimals=0):
    widget = QDoubleSpinBox() if decimals else QSpinBox()
    if decimals:
        widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    widget.setSuffix(suffix)
    widget.setKeyboardTracking(False)
    return widget


def page():
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    return widget, layout


def form(layout):
    result = QFormLayout()
    result.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    result.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    result.setVerticalSpacing(10)
    layout.addLayout(result)
    return result


class Tools(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.current = None
        self.alternate = None
        self.images = []
        self.track_actions = []
        self.loop_active = False
        self.results_source = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.picker = combo(['视频压缩', '图片转视频', '截取片段', '换封装', '提取音轨', '素材体检'])
        self.picker.setAccessibleName('切换媒体工具')
        layout.addWidget(self.picker)
        self.source_label = label('请先打开视频')
        self.choose_source = button('另选视频', self.select_source)
        self.reset_source = button('使用播放文件', self.use_current)
        self.source_widget, source_layout = page()
        source_layout.addWidget(self.source_label)
        source_layout.addLayout(row(self.choose_source, self.reset_source))
        layout.addWidget(self.source_widget)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        for build in (self.build_compress, self.build_images, self.build_clip, self.build_remux, self.build_extract, self.build_inspection):
            self.stack.addWidget(build())
        self.picker.currentIndexChanged.connect(self.change_tool)
        self.change_tool(0)
        layout.addStretch(1)

    @property
    def source(self):
        return self.alternate or self.current

    def change_tool(self, index):
        self.stack.setCurrentIndex(index)
        self.source_widget.setVisible(index in (0, 3, 4))

    def set_current(self, media):
        self.current = media
        self.loop_active = False
        self.preview.setText('循环预览')
        duration = media.duration if media and media.path.suffix.lower() not in engine.IMAGE else None
        self.timeline.setEnabled(bool(duration))
        self.start.setMaximum(duration or 0)
        self.end.setMaximum(duration or 0)
        self.start.setValue(0)
        self.end.setValue(duration or 0)
        self.results.clear()
        self.results_source = None
        self.scan_hint.setText('点击检测，定位素材中疑似黑场、静音或冻帧的区间。')
        self.scan_button.setEnabled(bool(media and media.video and media.path.suffix.lower() not in engine.IMAGE))
        self.update_clip()
        if media and media.path.suffix.lower() in engine.IMAGE - {'.gif'} and not self.images:
            self.images = [(media.path, 5)]
            self.refresh_images(0)
        self.update_source()

    def select_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择处理源文件', str(self.window.current_path.parent) if self.window.current_path else '', self.window.video_filter)
        if path:
            self.choose_source.setEnabled(False)
            self.window.work(lambda: engine.probe(Path(path)), self.source_probed)

    def source_probed(self, result, error):
        self.choose_source.setEnabled(True)
        if error:
            self.window.show_status('读取源文件失败', error)
            return
        self.alternate = result
        self.update_source()

    def use_current(self):
        self.alternate = None
        self.update_source()

    def update_source(self):
        source = self.source
        self.source_label.setText(source.path.name if source else '请先打开视频')
        self.reset_source.setVisible(self.alternate is not None)
        for widget in (self.audio, self.extract_track):
            widget.clear()
        self.audio.addItem('无音轨', None)
        if source:
            for track in source.tracks('audio'):
                self.audio.addItem(engine.track_label(track), track)
                self.extract_track.addItem(engine.track_label(track), track)
            selected = next((i for i in range(self.audio.count()) if self.audio.itemData(i) == source.audio), 0)
            self.audio.setCurrentIndex(selected)
            self.extract_track.setCurrentIndex(max(0, selected - 1))
        self.extract_button.setEnabled(bool(self.extract_track.count()))
        self.refresh_remux()
        self.compression_state()

    def build_compress(self):
        widget, layout = page()
        self.profile = combo(['自定义', '大屏兼容版'])
        form(layout).addRow('方案', self.profile)
        self.custom, custom_layout = page()
        grid = form(custom_layout)
        self.use_size = QCheckBox('降低分辨率')
        self.size = combo([('保持 50%', 50), ('720p', 720), ('480p', 480), ('1080p', 1080), ('自定义高度', 0)])
        self.height = spin(2, 16384, 720, ' px')
        self.use_rate = QCheckBox('降低码率')
        self.bitrate = spin(1, 1000000, 2000, ' kbps')
        self.use_fps = QCheckBox('降低帧率')
        self.fps = spin(1, 240, 30, ' fps', 3)
        grid.addRow(self.use_size, self.size)
        grid.addRow('', self.height)
        grid.addRow(self.use_rate, self.bitrate)
        grid.addRow(self.use_fps, self.fps)
        layout.addWidget(self.custom)
        self.screen, screen_layout = page()
        grid = form(screen_layout)
        self.screen_size = combo([('横屏 1920 × 1080', (1920, 1080)), ('竖屏 1080 × 1920', (1080, 1920)), ('横屏 3840 × 2160', (3840, 2160)), ('竖屏 2160 × 3840', (2160, 3840))])
        self.screen_fit = combo([('等比留黑边', False), ('裁切铺满', True)])
        self.screen_rate = combo([('最高 30 fps', 30), ('最高 60 fps', 60), ('保持源帧率', None)])
        grid.addRow('输出画布', self.screen_size)
        grid.addRow('画面适配', self.screen_fit)
        grid.addRow('帧率', self.screen_rate)
        layout.addWidget(self.screen)
        self.audio = combo([])
        form(layout).addRow('音轨', self.audio)
        self.compress_hint = label()
        layout.addWidget(self.compress_hint)
        self.compress_button = button('导出压缩视频', self.compress, True)
        layout.addWidget(self.compress_button)
        for control in (self.profile, self.screen_size, self.screen_rate, self.audio, self.size):
            control.currentIndexChanged.connect(self.compression_state)
        for control in (self.use_size, self.use_rate, self.use_fps):
            control.toggled.connect(self.compression_state)
        for control in (self.height, self.bitrate, self.fps):
            control.valueChanged.connect(self.compression_state)
        return widget

    def compression_parameters(self):
        source = self.source
        if self.profile.currentIndex() == 1:
            return dict(canvas=self.screen_size.currentData(), fill=self.screen_fit.currentData(), fps=self.screen_rate.currentData())
        selected_height = None
        if self.use_size.isChecked():
            size = self.size.currentData()
            selected_height = (source.shape[1] / 2 if source else 0) if size == 50 else size or self.height.value()
        return dict(height=selected_height, bitrate=self.bitrate.value() if self.use_rate.isChecked() else None,
                    fps=self.fps.value() if self.use_fps.isChecked() else None)

    def compression_state(self):
        screen = self.profile.currentIndex() == 1
        self.custom.setVisible(not screen)
        self.screen.setVisible(screen)
        self.size.setEnabled(self.use_size.isChecked())
        self.height.setVisible(self.use_size.isChecked() and self.size.currentData() == 0)
        self.bitrate.setEnabled(self.use_rate.isChecked())
        self.fps.setEnabled(self.use_fps.isChecked())
        self.compress_button.setText('导出大屏兼容版' if screen else '导出压缩视频')
        source = self.source
        try:
            if source and source.path.suffix.lower() in engine.IMAGE:
                raise ValueError('请选择视频文件。')
            params = self.compression_parameters()
            engine.compression(source, self.audio.currentData(), **params)
        except ValueError as error:
            self.compress_button.setEnabled(False)
            self.compress_hint.setText(str(error))
            return
        self.compress_button.setEnabled(True)
        width, height, sar = source.shape
        if params.get('canvas'):
            width, height = params['canvas']
        elif params.get('height'):
            width, height = engine.even(width * sar * params['height'] / height), engine.even(params['height'])
        else:
            width, height = width + width % 2, height + height % 2
        rate = params.get('fps')
        effective = min(source.fps, rate) if source.fps and rate else rate or source.fps
        rate_text = f'约 {effective:g} fps' if effective else '帧率随源文件'
        conversion = ' · HDR 转 SDR' if source.hdr else ''
        audio = self.audio.currentData()
        audio_text = (f"AAC{' 立体声' if screen else ''} · {engine.track_label(audio)}") if audio else '无音轨'
        self.compress_hint.setText(f'{width} × {height} · {rate_text} · H.264{conversion}\n{audio_text}')

    def compress(self):
        self.window.export(lambda: engine.compression(self.source, self.audio.currentData(), **self.compression_parameters()))

    def build_images(self):
        widget, layout = page()
        self.image_list = QListWidget()
        self.image_list.setObjectName('compactList')
        self.image_list.setIconSize(QSize(42, 28))
        self.image_list.setFixedHeight(150)
        self.image_list.currentRowChanged.connect(self.image_selected)
        layout.addWidget(self.image_list)
        layout.addLayout(row(button('添加图片', self.add_images), button('使用当前图片', self.use_image)))
        layout.addLayout(row(button('上移', lambda: self.move_image(-1)), button('下移', lambda: self.move_image(1)), button('移除', self.remove_image)))
        grid = form(layout)
        self.image_duration = spin(1, 3600, 5, ' 秒', 2)
        self.image_duration.valueChanged.connect(self.save_image_duration)
        self.image_rate = combo([('24 fps', 24), ('30 fps', 30), ('60 fps', 60)])
        self.image_rate.setCurrentIndex(1)
        self.image_canvas = combo([('保持首张图片尺寸', None), ('横屏 1920 × 1080', (1920, 1080)), ('横屏 1280 × 720', (1280, 720)), ('竖屏 1080 × 1920', (1080, 1920))])
        self.image_fade = QCheckBox('每张图片淡入淡出')
        grid.addRow('选中图片时长', self.image_duration)
        grid.addRow('输出帧率', self.image_rate)
        grid.addRow('输出画布', self.image_canvas)
        layout.addWidget(self.image_fade)
        self.image_total = label()
        layout.addWidget(self.image_total)
        self.image_button = button('生成 MP4 视频', self.export_images, True)
        layout.addWidget(self.image_button)
        self.refresh_images()
        return widget

    def add_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加静态图片', '', self.window.image_filter)
        for path in paths:
            image = Path(path)
            if image.suffix.lower() in engine.IMAGE - {'.gif'}:
                self.images.append((image, 5))
        self.refresh_images(len(self.images) - 1)

    def use_image(self):
        path = self.window.current_path
        if not path or path.suffix.lower() not in engine.IMAGE - {'.gif'}:
            self.window.show_status('请先打开一张静态图片。')
            return
        self.images = [(path, 5)]
        self.refresh_images(0)

    def refresh_images(self, selected=-1):
        self.image_list.blockSignals(True)
        self.image_list.clear()
        for path, duration in self.images:
            item = QListWidgetItem(f'{path.name}  ·  {duration:g} 秒')
            item.setToolTip(str(path))
            self.image_list.addItem(item)
        self.image_list.setCurrentRow(selected)
        self.image_list.blockSignals(False)
        self.image_selected(selected)
        self.image_total.setText(f'{len(self.images)} 张 · 共 {engine.clock(sum(d for _, d in self.images))}' if self.images else '添加一张图片生成静帧视频，或添加多张生成轮播。')
        self.image_button.setEnabled(bool(self.images))

    def image_selected(self, index):
        self.image_duration.setEnabled(index >= 0)
        if index >= 0:
            self.image_duration.blockSignals(True)
            self.image_duration.setValue(self.images[index][1])
            self.image_duration.blockSignals(False)

    def save_image_duration(self, duration):
        index = self.image_list.currentRow()
        if index >= 0:
            self.images[index] = (self.images[index][0], duration)
            self.refresh_images(index)

    def move_image(self, direction):
        index = self.image_list.currentRow()
        target = index + direction
        if index >= 0 and 0 <= target < len(self.images):
            self.images[index], self.images[target] = self.images[target], self.images[index]
            self.refresh_images(target)

    def remove_image(self):
        index = self.image_list.currentRow()
        if index >= 0:
            self.images.pop(index)
            self.refresh_images(min(index, len(self.images) - 1))

    def export_images(self):
        items = list(self.images)
        rate, canvas, fade = self.image_rate.currentData(), self.image_canvas.currentData(), self.image_fade.isChecked()
        self.window.prepare_export(lambda: engine.playlist_images(items, rate, canvas, fade))

    def build_clip(self):
        widget, layout = page()
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setAccessibleName('播放位置')
        self.timeline.setRange(0, 10000)
        self.timeline.sliderMoved.connect(self.timeline_seek)
        self.timeline.sliderReleased.connect(lambda: self.timeline_seek(self.timeline.value()))
        layout.addWidget(self.timeline)
        self.position_label = label('00:00 / 00:00')
        layout.addWidget(self.position_label)
        self.start = spin(0, 0, 0, ' 秒', 3)
        self.end = spin(0, 0, 0, ' 秒', 3)
        grid = form(layout)
        grid.addRow('起点', self.start)
        grid.addRow('终点', self.end)
        layout.addLayout(row(button('当前帧设为起点', lambda: self.mark(self.start)), button('设为终点', lambda: self.mark(self.end))))
        self.clip_mode = combo([('精确截取 · MP4', False), ('快速截取 · MKV', True)])
        grid = form(layout)
        grid.addRow('导出方式', self.clip_mode)
        self.clip_hint = label()
        layout.addWidget(self.clip_hint)
        self.preview = button('循环预览', self.toggle_loop)
        self.clip_button = button('导出片段', self.export_clip, True)
        layout.addLayout(row(self.preview, self.clip_button))
        self.clip_mode.currentIndexChanged.connect(self.update_clip)
        self.start.valueChanged.connect(self.change_points)
        self.end.valueChanged.connect(self.change_points)
        return widget

    def change_points(self):
        if self.loop_active:
            self.window.video.set_loop()
            self.loop_active = False
            self.preview.setText('循环预览')
        self.update_clip()

    def update_clip(self):
        duration = self.end.value() - self.start.value()
        enabled = bool(self.current and self.current.video and self.current.path.suffix.lower() not in engine.IMAGE and duration > 0)
        self.preview.setEnabled(enabled)
        self.clip_button.setEnabled(enabled)
        text = f'所选片段 {engine.clock(duration, True)}' if enabled else '打开带有时长信息的视频，然后设置起止点。'
        if self.clip_mode.currentData() and enabled:
            text += '\n快速截取沿用关键帧，实际起点可能早于设定时间。'
        self.clip_hint.setText(text)

    def timeline_seek(self, value):
        if self.current and self.current.duration:
            self.window.video.seek(self.current.duration * value / self.timeline.maximum())

    def update_position(self, position):
        if self.picker.currentIndex() != 2:
            return
        duration = self.current.duration if self.current else None
        self.position_label.setText(f'{engine.clock(position, True)} / {engine.clock(duration)}')
        if not self.timeline.isSliderDown():
            self.timeline.setValue(round((position or 0) / duration * self.timeline.maximum()) if duration else 0)

    def mark(self, control):
        position = self.window.video.position()
        if position is not None:
            control.setValue(position)

    def toggle_loop(self):
        if self.loop_active:
            self.window.video.set_loop()
            self.preview.setText('循环预览')
        else:
            self.window.video.set_loop(self.start.value(), self.end.value())
            self.preview.setText('停止循环预览')
        self.loop_active = not self.loop_active

    def export_clip(self):
        self.window.export(lambda: engine.clip(self.current, self.start.value(), self.end.value(), self.clip_mode.currentData()))

    def build_remux(self):
        widget, layout = page()
        self.container = combo([('MP4', 'mp4'), ('MKV', 'mkv')])
        form(layout).addRow('输出容器', self.container)
        self.remux_hint = label('选择要保留、转码或跳过的轨道。')
        layout.addWidget(self.remux_hint)
        self.remux_list, self.remux_tracks = page()
        layout.addWidget(self.remux_list)
        self.remux_button = button('导出换封装文件', self.export_remux, True)
        layout.addWidget(self.remux_button)
        self.container.currentIndexChanged.connect(self.refresh_remux)
        return widget

    def refresh_remux(self):
        while self.remux_tracks.count():
            item = self.remux_tracks.takeAt(0)
            item.widget().deleteLater()
        self.track_actions = []
        container = self.container.currentData()
        incompatible = False
        if self.source:
            for track in self.source.streams:
                copy = engine.can_copy(track, container)
                incompatible |= not copy
                choices = (['保留'] if copy else ['跳过']) + (['转码'] if not copy and engine.can_convert(track) else []) + (['跳过'] if copy else [])
                entry, layout = page()
                track_title = label(engine.track_label(track), 'trackTitle')
                action = combo(choices)
                action.setAccessibleName(engine.track_label(track))
                layout.addLayout(row(track_title, action))
                self.remux_tracks.addWidget(entry)
                self.track_actions.append((track, action))
                action.currentIndexChanged.connect(self.remux_state)
        self.remux_hint.setText('部分轨道与容器不兼容。可选转码、跳过，或切换输出容器。' if incompatible else '「保留」直接复制该轨道，速度取决于读写性能。')
        self.remux_state()

    def remux_state(self):
        self.remux_button.setEnabled(any(action.currentData() != '跳过' for _, action in self.track_actions))

    def export_remux(self):
        selected = [(track, action.currentData()) for track, action in self.track_actions if action.currentData() != '跳过']
        self.window.export(lambda: engine.remux(self.source, self.container.currentData(), selected))

    def build_extract(self):
        widget, layout = page()
        self.extract_track = combo([])
        self.extract_kind = combo([('原编码 · MKA', '原编码'), 'AAC', 'WAV', 'FLAC'])
        grid = form(layout)
        grid.addRow('音轨', self.extract_track)
        grid.addRow('输出格式', self.extract_kind)
        self.extract_button = button('导出音轨', lambda: self.window.export(lambda: engine.extract(self.source, self.extract_track.currentData(), self.extract_kind.currentData())), True)
        layout.addWidget(self.extract_button)
        return widget

    def build_inspection(self):
        widget, layout = page()
        self.scan_hint = label()
        layout.addWidget(self.scan_hint)
        self.scan_button = button('检测当前视频', self.scan, True)
        layout.addWidget(self.scan_button)
        self.results = QListWidget()
        self.results.setObjectName('compactList')
        self.results.setMinimumHeight(210)
        self.results.itemClicked.connect(self.jump_to_result)
        self.results.itemActivated.connect(self.jump_to_result)
        layout.addWidget(self.results)
        return widget

    def scan(self):
        if self.window.busy():
            self.window.show_status('请等待当前任务完成，或先取消。')
            return
        try:
            args = engine.inspect_media(self.current)
            self.window.start_job('素材体检', args, self.current.duration, scan=self.current)
        except (OSError, ValueError) as error:
            self.window.show_status('无法开始检测', str(error))

    def show_findings(self, source, log):
        if self.current != source:
            return
        self.results_source = source.path
        self.results.clear()
        results = engine.findings(log, source.duration)
        for start, end, kind in results:
            item = QListWidgetItem(f'{kind}  ·  {engine.clock(start, True)} – {engine.clock(end, True)}')
            item.setData(Qt.ItemDataRole.UserRole, start)
            self.results.addItem(item)
        text = f'发现 {len(results)} 个疑似区间，点击时间戳跳转。' if results else '未发现符合阈值的区间。'
        self.scan_hint.setText(text)

    def jump_to_result(self, item):
        if self.results_source == self.window.current_path:
            self.window.video.set_loop()
            self.loop_active = False
            self.preview.setText('循环预览')
            self.window.video.seek(item.data(Qt.ItemDataRole.UserRole))
