from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from player.window import STYLE, Window


class Application(QApplication):
    def __init__(self, args):
        self.pending = None
        self.window = None
        super().__init__(args)

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or event.url().toLocalFile()
            if path:
                self.pending = Path(path)
                if self.window:
                    QTimer.singleShot(0, self.open_pending)
            return True
        return super().event(event)

    def open_pending(self):
        if self.pending:
            path, self.pending = self.pending, None
            self.window.open_media(path)


def make_application(args):
    surface = QSurfaceFormat()
    surface.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    surface.setVersion(3, 2)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    surface.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface)
    app = Application(args)
    app.setApplicationName('播放器')
    app.setOrganizationName('Fullscreen Player')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.window = Window()
    return app


def main():
    app = make_application(sys.argv)
    app.window.show()
    if len(sys.argv) > 1:
        app.pending = Path(sys.argv[1])
    QTimer.singleShot(0, app.open_pending)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
