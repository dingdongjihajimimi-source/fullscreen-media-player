from __future__ import annotations

import os
import shutil
import tempfile
from collections import deque
from pathlib import Path
from threading import Lock

from PySide6.QtCore import QObject, QProcess, QRunnable, QTimer, Signal

from .media import binary, number


class WorkerResults:
    def __init__(self, signal):
        self.signal = signal
        self.lock = Lock()

    def deliver(self, callback, result, error):
        with self.lock:
            if self.signal is not None:
                self.signal.emit(callback, result, error)

    def close(self):
        with self.lock:
            self.signal = None


class Worker(QRunnable):
    def __init__(self, task, callback, results):
        super().__init__()
        self.task = task
        self.callback = callback
        self.results = results

    def run(self):
        try:
            result = self.task()
        except Exception as error:
            self.results.deliver(self.callback, None, str(error))
        else:
            self.results.deliver(self.callback, result, None)


def output_path(source, suffix):
    candidate = source.with_name(source.stem + suffix)
    index = 2
    while candidate.exists():
        first = source.with_name(source.stem + suffix)
        candidate = first.with_stem(f'{first.stem}_{index}')
        index += 1
    return candidate


def publish(temporary, source, suffix):
    while True:
        target = output_path(source, suffix)
        try:
            # A same-volume hard link publishes atomically and fails if the name is taken.
            os.link(temporary, target)
        except FileExistsError:
            continue
        except OSError:
            # FAT and some network volumes do not support hard links.
            try:
                destination = target.open('xb')
            except FileExistsError:
                continue
            try:
                with destination, temporary.open('rb') as input_file:
                    shutil.copyfileobj(input_file, destination)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return target


def temporary_dir(source):
    return tempfile.TemporaryDirectory(prefix='.player-', dir=source.parent)


class FFmpegJob(QObject):
    progress = Signal(object, str)
    completed = Signal(object, object, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.readyReadStandardError.connect(self.read_log)
        self.process.finished.connect(self.finish)
        self.process.errorOccurred.connect(self.process_error)
        self.kill_timer = QTimer(self, singleShot=True, interval=2500)
        self.kill_timer.timeout.connect(self.process.kill)
        self.working = False

    def start(self, label, args, duration, export=None):
        if self.working:
            raise ValueError('请等待当前任务完成，或先取消任务。')
        program = binary('ffmpeg')
        self.export = export
        self.directory = None
        self.temporary = None
        self.label = label
        self.duration = duration
        self.stdout = b''
        self.log = deque(maxlen=100)
        self.analysis = []
        self.scan_tail = ''
        self.stopping = False
        self.percent = 0
        if export:
            self.directory = temporary_dir(export.source)
            self.temporary = Path(self.directory.name) / ('output' + Path(export.suffix).suffix)
            args = [*args, self.temporary]
        self.working = True
        self.progress.emit(None if not duration else 0, label)
        self.process.setProgram(program)
        self.process.setArguments(list(map(str, ['-hide_banner', '-nostats', '-progress', 'pipe:1', '-y', *args])))
        self.process.start()

    def read_progress(self):
        self.stdout += bytes(self.process.readAllStandardOutput())
        lines = self.stdout.split(b'\n')
        self.stdout = lines.pop()
        for line in lines:
            if line.startswith(b'out_time_us=') and self.duration and not self.stopping:
                timestamp = number(line.partition(b'=')[2].decode()) / 1_000_000
                self.percent = max(self.percent, min(99, int(100 * timestamp / self.duration)))
                self.progress.emit(self.percent, self.label)

    def read_log(self):
        chunk = bytes(self.process.readAllStandardError()).decode('utf-8', 'replace')
        lines = (self.scan_tail + chunk).split('\n')
        self.scan_tail = lines.pop()
        for line in lines:
            self.log.append(line)
            if any(f'{kind}_' in line for kind in ('black', 'silence', 'freeze')):
                self.analysis.append(line)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.log.append(self.process.errorString())
            self.finish(-1)

    def cancel(self):
        if self.working and not self.stopping:
            self.stopping = True
            self.progress.emit(None, '正在取消')
            self.process.write(b'q\n')
            self.kill_timer.start()

    def shutdown(self):
        if self.working:
            self.stopping = True
            self.process.kill()
            self.process.waitForFinished(3000)
            if self.working:
                self.finish(-1)

    def finish(self, code, _status=None):
        if not self.working:
            return
        self.kill_timer.stop()
        self.read_progress()
        self.read_log()
        result, error = None, None
        if not self.stopping:
            if code != 0:
                error = '\n'.join([*self.log, self.scan_tail]).strip()[-3000:] or f'FFmpeg 退出，代码 {code}'
            elif self.export:
                try:
                    if not self.temporary.is_file() or self.temporary.stat().st_size == 0:
                        raise ValueError('FFmpeg 没有生成可用的文件。')
                    result = publish(self.temporary, self.export.source, self.export.suffix)
                except (OSError, ValueError) as exception:
                    error = str(exception)
        if self.directory:
            self.directory.cleanup()
        self.working = False
        self.completed.emit(result, error, self.stopping, '\n'.join([*self.analysis, self.scan_tail]))
