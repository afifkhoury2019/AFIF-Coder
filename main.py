import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from afif_coder import diag
from afif_coder.paths import resource
from afif_coder.ui import MainWindow


def main():
    if sys.platform == "win32":   # own taskbar icon instead of python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AFIF-TECH.AFIF-Coder")
    diag.install_crash_hooks()
    app = QApplication(sys.argv)
    app.setApplicationName("AFIF-Coder")
    app.setOrganizationName("AFIF-TECH")
    app.setWindowIcon(QIcon(str(resource("afif_coder/icon.png"))))
    w = MainWindow()
    w.show()
    diag.event("app_start", **diag.system_info(w.s))
    if "--selftest" in sys.argv:          # used by the build: start, render, quit cleanly
        def done():
            out = Path(sys.argv[sys.argv.index("--selftest") + 1]) if len(sys.argv) > sys.argv.index("--selftest") + 1 else None
            if out:
                w.grab().save(str(out))
            w.close()
            app.quit()
        QTimer.singleShot(3000, done)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
