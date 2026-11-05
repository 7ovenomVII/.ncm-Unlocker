
import sys
import os
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFileDialog, QProgressBar, QMenu,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit,
    QStackedWidget, QSizePolicy, QGraphicsDropShadowEffect
)


# =========================
# 基础配置
# =========================

BASE_DIR = Path(__file__).resolve().parent
NCM_DUMP = BASE_DIR / "tools" / "ncmdump.exe"

BG = "#17191E"
CARD = "#22252D"
CARD_HOVER = "#2B303A"
BORDER = "#343A46"
TEXT = "#F2F3F5"
SUBTEXT = "#929AA9"
ACCENT = "#8BAEFF"
GREEN = "#36B878"
RED = "#D45B5B"
GRAY = "#737B89"


def format_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def make_shadow(widget, blur=20, opacity=45):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(0, 0, 0, opacity))
    widget.setGraphicsEffect(shadow)


def make_cover(size=150):
    """第一版默认封面，后续替换成真实专辑图片。"""
    pix = QPixmap(size, size)
    pix.fill(QColor("#30394B"))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#B7C9E8"))
    painter.drawEllipse(size // 2 - 27, size // 2 - 27, 54, 54)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(
        pix.rect(),
        Qt.AlignmentFlag.AlignCenter,
        "♪"
    )
    painter.end()
    return pix


# =========================
# 单首歌曲卡片
# =========================

class SongCard(QFrame):

    def __init__(self, path, parent=None):
        super().__init__(parent)

        self.path = Path(path)
        self.status = "waiting"
        self.selected = False
        self.setFixedSize(166, 215)

        self.setObjectName("SongCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)

        self.cover = QLabel()
        self.cover.setFixedSize(150, 150)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setPixmap(make_cover())

        layout.addWidget(self.cover)

        self.name = QLabel(self.path.name)
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setFixedHeight(30)
        self.name.setToolTip(str(self.path))

        layout.addWidget(self.name)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)

        layout.addWidget(self.progress)

        self.update_style()

    def update_style(self):
        if self.selected:
            border = ACCENT
            background = "#30394A"
        else:
            border = BORDER
            background = CARD

        self.setStyleSheet(f"""
            QFrame#SongCard {{
                background: {background};
                border: 2px solid {border};
                border-radius: 13px;
            }}
            QLabel {{
                color: {TEXT};
                background: transparent;
                border: none;
                font-size: 11px;
            }}
            QProgressBar {{
                background: #424854;
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {GREEN if self.status == 'success' else GRAY};
                border-radius: 2px;
            }}
        """)

    def set_status(self, status):
        self.status = status

        if status == "success":
            self.progress.setValue(100)
        else:
            self.progress.setValue(0)

        self.update_style()


# =========================
# 自适应网格
# =========================

class SongGrid(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.cards = []
        self.selected = []
        self.last_clicked = -1

        self.layout_grid = QGridLayout(self)
        self.layout_grid.setSpacing(18)
        self.layout_grid.setContentsMargins(8, 8, 8, 8)
        self.setAcceptDrops(True)

    def add_files(self, paths):
        existing = {str(card.path.resolve()) for card in self.cards}

        for path in paths:
            path = Path(path)

            if path.suffix.lower() != ".ncm":
                continue

            if not path.is_file():
                continue

            if str(path.resolve()) in existing:
                continue

            card = SongCard(path)
            card.mousePressEvent = (
                lambda event, c=card: self.select_card(c, event)
            )

            self.cards.append(card)
            existing.add(str(path.resolve()))

        self.refresh()

    def refresh(self):
        while self.layout_grid.count():
            item = self.layout_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        width = self.width()
        columns = max(1, (width + 18) // 184)

        for index, card in enumerate(self.cards):
            row = index // columns
            col = index % columns
            self.layout_grid.addWidget(card, row, col)

        self.layout_grid.setRowStretch(
            len(self.cards) // columns + 1, 1
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    def select_card(self, card, event):
        index = self.cards.index(card)
        modifiers = event.modifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if card in self.selected:
                self.selected.remove(card)
            else:
                self.selected.append(card)

        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            start = self.last_clicked if self.last_clicked >= 0 else 0
            low, high = sorted((start, index))
            self.selected = self.cards[low:high + 1]

        else:
            self.selected = [card]

        self.last_clicked = index

        self.update_selection()

        window = self.window()
        if hasattr(window, "update_panels"):
            window.update_panels()

    def update_selection(self):
        for card in self.cards:
            card.selected = card in self.selected
            card.update_style()

    def clear_selection(self):
        self.selected.clear()
        self.update_selection()

        window = self.window()
        if hasattr(window, "update_panels"):
            window.update_panels()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_A and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.selected = self.cards.copy()
            self.update_selection()

            window = self.window()
            if hasattr(window, "update_panels"):
                window.update_panels()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        self.add_files(paths)


# =========================
# 转换设置菜单
# =========================

class ExportMenu(QMenu):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.format = "MP3"
        self.output_mode = "source"
        self.output_path = ""

        self.setStyleSheet("""
            QMenu {
                background: #292D36;
                color: #F2F3F5;
                border: 1px solid #414754;
                border-radius: 10px;
                padding: 10px;
            }
            QMenu::item {
                padding: 9px 24px;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background: #414B60;
            }
        """)

        self.format_menu = self.addMenu("导出格式")
        self.mp3_action = self.format_menu.addAction("MP3")
        self.flac_action = self.format_menu.addAction("FLAC")

        self.mp3_action.triggered.connect(
            lambda: self.set_format("MP3")
        )
        self.flac_action.triggered.connect(
            lambda: self.set_format("FLAC")
        )

        self.addSeparator()

        self.source_action = self.addAction("原文件所在目录")
        self.custom_action = self.addAction("自定义输出目录...")

        self.source_action.triggered.connect(
            lambda: self.set_source()
        )
        self.custom_action.triggered.connect(
            self.choose_directory
        )

    def set_format(self, fmt):
        self.format = fmt

    def set_source(self):
        self.output_mode = "source"
        self.output_path = ""

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择输出目录"
        )
        if directory:
            self.output_mode = "custom"
            self.output_path = directory


# =========================
# 主窗口
# =========================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("NCM Studio")
        self.resize(1200, 760)
        self.setMinimumSize(850, 600)

        self.queue = []
        self.current_process = None
        self.current_job = None
        self.output_buffer = ""

        self.export_menu = ExportMenu(self)

        self.build_ui()
        self.apply_theme()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 20)
        root.setSpacing(18)

        # 顶栏
        header = QHBoxLayout()

        title = QLabel("NCM Studio")
        title.setStyleSheet(
            "font-size: 23px; font-weight: bold; color: white;"
        )

        subtitle = QLabel("Audio Converter")
        subtitle.setStyleSheet(f"color: {SUBTEXT};")

        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(subtitle)
        header.addStretch()

        self.theme_button = QPushButton("◐")
        self.theme_button.setFixedSize(38, 38)
        header.addWidget(self.theme_button)

        root.addLayout(header)

        # 主体
        body = QHBoxLayout()
        body.setSpacing(20)

        # 左侧
        left = QVBoxLayout()
        left.setSpacing(15)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.grid = SongGrid()
        self.scroll.setWidget(self.grid)

        self.empty = QFrame()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("♫")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"font-size: 60px; color: {ACCENT};"
        )

        empty_title = QLabel("拖放 NCM 文件到这里")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )

        empty_hint = QLabel("或点击下方按钮选择文件")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setStyleSheet(f"color: {SUBTEXT};")

        choose_button = QPushButton("选择文件")
        choose_button.clicked.connect(self.choose_files)

        empty_layout.addWidget(icon)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_hint)
        empty_layout.addSpacing(15)
        empty_layout.addWidget(choose_button)

        self.player = QFrame()
        player_layout = QHBoxLayout(self.player)

        self.player_name = QLabel("尚未选择歌曲")
        self.player_name.setStyleSheet("font-weight: bold;")

        self.play_button = QPushButton("▶")
        self.play_button.setFixedSize(42, 42)
        self.play_button.setEnabled(False)

        player_layout.addWidget(self.player_name)
        player_layout.addStretch()
        player_layout.addWidget(self.play_button)

        left.addWidget(self.empty, 1)
        left.addWidget(self.scroll, 1)
        left.addWidget(self.player)

        # 右侧
        right = QVBoxLayout()
        right.setSpacing(15)

        self.info = QFrame()
        self.info.setMinimumWidth(220)
        self.info.setMaximumWidth(310)

        self.info_layout = QVBoxLayout(self.info)
        self.info_layout.setContentsMargins(18, 20, 18, 20)
        self.info_layout.setSpacing(12)

        self.cover_info = QLabel()
        self.cover_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_info.setPixmap(make_cover(145))

        self.info_title = QLabel("文件详情")
        self.info_title.setStyleSheet(
            "font-size: 17px; font-weight: bold;"
        )

        self.info_detail = QLabel("选择歌曲查看详细信息")
        self.info_detail.setWordWrap(True)

        self.info_layout.addWidget(self.cover_info, 0,
                                   Qt.AlignmentFlag.AlignCenter)
        self.info_layout.addWidget(self.info_title)
        self.info_layout.addWidget(self.info_detail)
        self.info_layout.addStretch()

        # 转换按钮
        self.convert_card = QFrame()
        convert_layout = QHBoxLayout(self.convert_card)
        convert_layout.setContentsMargins(8, 8, 8, 8)
        convert_layout.setSpacing(4)

        self.convert_button = QPushButton("开始转换")
        self.convert_button.clicked.connect(self.start_conversion)

        self.menu_button = QPushButton("⌄")
        self.menu_button.setFixedWidth(42)
        self.menu_button.clicked.connect(
            lambda: self.export_menu.exec(
                self.menu_button.mapToGlobal(
                    self.menu_button.rect().bottomLeft()
                )
            )
        )

        convert_layout.addWidget(self.convert_button, 1)
        convert_layout.addWidget(self.menu_button)

        right.addWidget(self.info, 1)
        right.addWidget(self.convert_card)

        body.addLayout(left, 7)
        body.addLayout(right, 3)

        root.addLayout(body, 1)

        self.scroll.hide()
        self.player.hide()

    def apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
            }}

            QFrame {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 17px;
            }}

            QPushButton {{
                background: #343D4F;
                color: white;
                border: none;
                border-radius: 10px;
                padding: 10px 16px;
                font-weight: bold;
            }}

            QPushButton:hover {{
                background: #46536C;
            }}

            QPushButton:disabled {{
                color: #777777;
            }}

            QScrollArea {{
                background: transparent;
                border: none;
            }}

            QLabel {{
                background: transparent;
                border: none;
            }}
        """)

        for widget in (
            self.info, self.convert_card, self.player,
            self.empty
        ):
            make_shadow(widget)

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 NCM 文件",
            "",
            "NCM Files (*.ncm)"
        )
        self.add_files(files)

    def add_files(self, files):
        self.grid.add_files(files)

        if self.grid.cards:
            self.empty.hide()
            self.scroll.show()

    def update_panels(self):
        selected = self.grid.selected
        count = len(selected)

        self.player.hide()

        if count == 0:
            self.info_title.setText("文件详情")
            self.info_detail.setText("选择歌曲查看详细信息")
            self.cover_info.show()
            self.info_detail.show()
            self.cover_info.setPixmap(make_cover(145))
            return

        if count == 1:
            card = selected[0]
            path = card.path

            self.info_title.setText(path.stem)
            self.info_detail.setText(
                f"文件格式：NCM\n\n"
                f"文件大小：{format_size(path.stat().st_size)}\n\n"
                f"文件位置：\n{path.parent}"
            )

            self.cover_info.show()
            self.cover_info.setPixmap(make_cover(145))
            self.info_detail.show()

            self.player_name.setText(path.stem)
            self.player.show()

        else:
            total = sum(
                card.path.stat().st_size
                for card in selected
                if card.path.exists()
            )

            self.cover_info.hide()
            self.info_title.setText(f"已选择 {count} 个文件")
            self.info_detail.setText(
                f"文件数量：{count}\n\n"
                f"总大小：{format_size(total)}"
            )

    # =========================
    # 转换队列
    # =========================

    def start_conversion(self):
        if not self.grid.selected:
            return

        if not NCM_DUMP.exists():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "找不到 ncmdump",
                f"请将 ncmdump.exe 放到：\n{NCM_DUMP}"
            )
            return

        self.queue = list(self.grid.selected)

        for card in self.queue:
            card.set_status("waiting")

        self.convert_button.setEnabled(False)
        self.convert_button.setText("转换中...")

        self.run_next()

    def run_next(self):
        if not self.queue:
            self.current_process = None
            self.current_job = None
            self.convert_button.setEnabled(True)
            self.convert_button.setText("开始转换")
            return

        card = self.queue.pop(0)

        if not card.path.exists():
            card.set_status("failed")
            self.run_next()
            return

        self.current_job = card
        card.set_status("processing")
        self.output_buffer = ""

        process = QProcess(self)
        self.current_process = process

        process.readyReadStandardOutput.connect(
            self.read_output
        )
        process.readyReadStandardError.connect(
            self.read_error
        )
        process.finished.connect(
            self.process_finished
        )

        process.start(
            str(NCM_DUMP),
            [str(card.path)]
        )

    def read_output(self):
        if not self.current_process:
            return

        data = bytes(
            self.current_process.readAllStandardOutput()
        ).decode("utf-8", errors="replace")

        self.output_buffer += data
        self.check_output(data)

    def read_error(self):
        if not self.current_process:
            return

        data = bytes(
            self.current_process.readAllStandardError()
        ).decode("utf-8", errors="replace")

        self.output_buffer += data
        self.check_output(data)

    def check_output(self, data):
        if not self.current_job:
            return

        lines = data.lower().splitlines()

        if any(line.strip() == "error" for line in lines):
            self.current_job.set_status("failed")

    def process_finished(self, exit_code, exit_status):
        card = self.current_job

        if card:
            output = self.output_buffer.lower()

            lines = [
                line.strip()
                for line in output.splitlines()
            ]

            has_done = "done" in lines
            has_error = "error" in lines

            # 成功必须有 done，且不能出现 error。
            # 同时要求进程正常退出。
            if (
                has_done
                and not has_error
                and exit_code == 0
                and exit_status == QProcess.ExitStatus.NormalExit
            ):
                card.set_status("success")
            else:
                card.set_status("failed")

        self.current_process = None
        self.current_job = None

        QTimer.singleShot(250, self.run_next)


# =========================
# 启动
# =========================

if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
