"""
NCM Studio - v2
在你的第一版代码基础上改的，三个变化：
1. 右下角倒三角 -> 自己画的弹出卡片（格式 FLAC/MP3/WAV + 导出位置：原目录 / 自定义路径+输入框+浏览按钮）
2. 网格里显示 封面 + 文件名，进度条在文件名下面；切到列表视图时进度条在最右边
3. 开始转换后进度条会自己动（假进度，转完变绿，失败变红）
"""

import sys
import os
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QTimer, QSize, QPoint, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFileDialog, QProgressBar,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit,
    QSizePolicy, QGraphicsDropShadowEffect
)

# =========================
# 基础配置
# =========================

BASE_DIR = Path(__file__).resolve().parent


def _find_tool(name):
    """先找 tools/ 目录，再找脚本同级目录。"""
    for candidate in (BASE_DIR / "tools" / name, BASE_DIR / name):
        if candidate.exists():
            return candidate
    return BASE_DIR / "tools" / name


NCM_DUMP = _find_tool("ncmdump.exe")
FFMPEG = _find_tool("ffmpeg.exe")

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

FORMATS = ("FLAC", "MP3", "WAV")


def format_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def make_shadow(widget, blur=20, opacity=45, offset=5):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, offset)
    shadow.setColor(QColor(0, 0, 0, opacity))
    widget.setGraphicsEffect(shadow)


def make_cover(size=150, ratio=1.0):
    """没有真实封面时的占位图：渐变底 + 一个音符。"""
    w = int(size * ratio)
    pix = QPixmap(max(1, w), max(1, size))
    pix.fill(QColor("#2C3444"))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#B7C9E8"))
    d = min(w, size) // 3
    painter.drawEllipse(w // 2 - d // 2, size // 2 - d // 2, d, d)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
    painter.end()
    return pix


# =========================
# 真实封面：从 flac 里抠图
# =========================

def _parse_picture_block(data):
    try:
        p = 4                                   # picture type
        mime_len = int.from_bytes(data[p:p + 4], "big")
        p += 4 + mime_len
        desc_len = int.from_bytes(data[p:p + 4], "big")
        p += 4 + desc_len
        p += 16                                 # width/height/depth/colors
        img_len = int.from_bytes(data[p:p + 4], "big")
        p += 4
        return data[p:p + img_len]
    except Exception:
        return None


def read_flac_cover(path):
    """只读 FLAC 的 METADATA_BLOCK_PICTURE，不依赖第三方库。"""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return None
            while True:
                header = f.read(4)
                if len(header) < 4:
                    return None
                last = header[0] & 0x80
                block_type = header[0] & 0x7F
                length = int.from_bytes(header[1:4], "big")

                if block_type == 6:             # PICTURE
                    return _parse_picture_block(f.read(length))
                if block_type == 127:           # 非法
                    return None

                f.seek(length, 1)
                if last:
                    return None
    except Exception:
        return None


# =========================
# 导出设置 + 弹出卡片
# =========================

class ExportSettings:

    def __init__(self):
        self.format = "FLAC"        # FLAC / MP3 / WAV
        self.output_mode = "source"  # source / custom
        self.output_path = ""

    def path_text(self):
        if self.output_mode == "custom" and self.output_path:
            return self.output_path
        return "原文件所在目录"


# 弹出层想加「半透明 + 投影」就把这里改成 True（透明背景 + 投影更好看），
# 但部分 Windows 显卡/远程桌面下 WA_TranslucentBackground 会失效，弹出层外面套一块黑底。
# 默认走稳妥画法：不透明窗口 + 窗口底色填角，看着就是一张浮起来的圆角卡片。
POPUP_TRANSLUCENT = False


class ExportPopup(QWidget):
    """Qt.Popup：点外面会自动关，想画成什么样都行。"""

    changed = Signal()

    def __init__(self, settings, parent=None):
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        if POPUP_TRANSLUCENT:
            flags |= Qt.WindowType.NoDropShadowWindowHint  # 别让系统再套一圈窗口阴影
        super().__init__(parent, flags)

        self.settings = settings
        self._anchor = None

        if POPUP_TRANSLUCENT:
            # 圆角 + 投影必须靠透明背景，否则弹出层会是一块黑色方块
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.setObjectName("PopupShell")
        self.setFixedWidth(316)

        self._build()
        self._sync()

    # ---------- 构建 ----------

    def _build(self):
        pad = 12 if POPUP_TRANSLUCENT else 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad, pad, pad, pad)

        card = QFrame()
        card.setObjectName("PopupCard")
        if POPUP_TRANSLUCENT:
            make_shadow(card, blur=32, opacity=110, offset=8)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        # --- 导出格式 ---
        lay.addWidget(self._section_label("导出格式"))

        seg = QHBoxLayout()
        seg.setSpacing(6)

        self.format_group = QButtonGroup(self)
        self.format_group.setExclusive(True)
        self.format_buttons = {}

        for fmt in FORMATS:
            btn = QPushButton(fmt)
            btn.setObjectName("SegButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _checked=False, f=fmt: self.set_format(f))
            self.format_group.addButton(btn)
            self.format_buttons[fmt] = btn
            seg.addWidget(btn, 1)

        lay.addLayout(seg)

        lay.addWidget(self._divider())

        # --- 导出位置 ---
        lay.addWidget(self._section_label("导出位置"))

        self.rb_source = QRadioButton("原文件所在目录")
        self.rb_custom = QRadioButton("自定义路径")
        for rb in (self.rb_source, self.rb_custom):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(rb)

        self.rb_source.toggled.connect(
            lambda on: on and self.set_mode("source")
        )
        self.rb_custom.toggled.connect(
            lambda on: on and self.set_mode("custom")
        )

        # 自定义路径那一行：输入框 + 三个点
        self.custom_row = QWidget()
        row = QHBoxLayout(self.custom_row)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("点右侧 ··· 选择文件夹，或直接粘贴路径")
        self.path_edit.setFixedHeight(34)
        self.path_edit.textChanged.connect(self._on_path_typed)

        self.browse_btn = QPushButton("···")
        self.browse_btn.setObjectName("BrowseBtn")
        self.browse_btn.setFixedSize(38, 34)
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self.choose_directory)

        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_btn)

        lay.addWidget(self.custom_row)
        self.custom_row.hide()

        self._apply_style()

    def _apply_style(self):
        # 弹出卡片是独立顶层窗口，样式得自己写一份
        shell_bg = "transparent" if POPUP_TRANSLUCENT else "#17191E"

        self.setStyleSheet(f"""
            QWidget#PopupShell {{
                background: {shell_bg};
            }}

            QFrame#PopupCard {{
                background: #22252D;
                border: 1px solid #343A46;
                border-radius: 16px;
            }}

            QWidget {{
                background: transparent;
            }}

            QLabel#SectionLabel {{
                color: #8A93A3;
                font-size: 11px;
                background: transparent;
            }}

            QFrame#Divider {{
                background: #313846;
                border: none;
                border-radius: 1px;
            }}

            QPushButton#SegButton {{
                background: #2C323D;
                color: #AEB6C4;
                border: none;
                border-radius: 9px;
                padding: 6px 0;
                font-size: 12px;
                font-weight: 600;
            }}

            QPushButton#SegButton:hover {{
                background: #353D4B;
            }}

            QPushButton#SegButton:checked {{
                background: {ACCENT};
                color: #14181F;
            }}

            QRadioButton {{
                color: #E6E9EE;
                font-size: 13px;
                spacing: 9px;
                padding: 5px 0;
                background: transparent;
            }}

            QRadioButton::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 8px;
                border: 2px solid #59637A;
                background: transparent;
            }}

            QRadioButton::indicator:hover {{
                border: 2px solid #7E8AA5;
            }}

            QRadioButton::indicator:checked {{
                border: 5px solid {ACCENT};
                background: {ACCENT};
            }}

            QLineEdit {{
                background: #1B1F27;
                border: 1px solid #343A46;
                border-radius: 9px;
                padding: 6px 10px;
                color: #F2F3F5;
                font-size: 12px;
                selection-background-color: {ACCENT};
                selection-color: #14181F;
            }}

            QLineEdit:focus {{
                border: 1px solid {ACCENT};
            }}

            QPushButton#BrowseBtn {{
                background: #2C323D;
                color: #CBD3E1;
                border: none;
                border-radius: 9px;
                font-size: 14px;
                padding: 0;
            }}

            QPushButton#BrowseBtn:hover {{
                background: #39424F;
            }}
        """)

    @staticmethod
    def _section_label(text):
        label = QLabel(text)
        label.setObjectName("SectionLabel")
        return label

    @staticmethod
    def _divider():
        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        return line

    # ---------- 数据同步 ----------
    def _sync(self):
        for fmt, btn in self.format_buttons.items():
            btn.setChecked(fmt == self.settings.format)

        if self.settings.output_mode == "custom":
            self.rb_custom.setChecked(True)
        else:
            self.rb_source.setChecked(True)

        self.path_edit.blockSignals(True)
        self.path_edit.setText(self.settings.output_path)
        self.path_edit.blockSignals(False)

        self.custom_row.setVisible(self.settings.output_mode == "custom")
        self.adjustSize()

    def set_format(self, fmt):
        self.settings.format = fmt
        self.changed.emit()

    def set_mode(self, mode):
        self.settings.output_mode = mode
        self.custom_row.setVisible(mode == "custom")
        self.adjustSize()
        self.changed.emit()

    def _on_path_typed(self, text):
        self.settings.output_path = text.strip()
        self.changed.emit()

    def choose_directory(self):
        start = self.settings.output_path or ""
        # 先藏起来，否则系统对话框会被 Popup 抢焦点
        self.hide()
        directory = QFileDialog.getExistingDirectory(
            None, "选择导出文件夹", start
        )
        if directory:
            self.path_edit.setText(directory)
            self.settings.output_path = directory
            self.settings.output_mode = "custom"
            self.changed.emit()

        self._sync()

        # 选完文件夹把卡片放回去，不然用户还得再点一次倒三角
        anchor = self._anchor
        if anchor is not None:
            QTimer.singleShot(0, lambda: self.popup_above(anchor))

    # ---------- 定位 ----------
    def popup_above(self, button):
        self._anchor = button
        self._sync()
        pos = button.mapToGlobal(QPoint(0, 0))
        x = pos.x() + button.width() - self.width()
        y = pos.y() - self.height() - 10

        screen = (button.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, y)

        self.move(x, y)
        self.show()

    def reposition(self, button):
        if not self.isVisible():
            return
        self._sync()
        self.popup_above(button)


# =========================
# 单首歌曲卡片
# =========================

class SongCard(QFrame):

    GRID_W = 166
    GRID_H = 215
    LIST_H = 72

    def __init__(self, path, parent=None):
        super().__init__(parent)

        self.path = Path(path)
        self.status = "waiting"
        self.selected = False
        self.hovered = False
        self.mode = "grid"
        self._value = 0
        self.on_click = None
        self._cover_source = None

        self.setObjectName("SongCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # 假进度：每 180ms 往前挪一点，最多到 92%，真转完了才 100%
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._tick)

        # --- 控件 ---
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setStyleSheet("background: transparent; border: none;")

        self.name = QLabel(self.path.name)
        self.name.setToolTip(str(self.path))
        self.name.setStyleSheet("background: transparent; border: none;")

        self.meta = QLabel("等待转换")
        self.meta.setObjectName("CardMeta")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)

        # 只有一套网格排版，切视图时把控件挪到不同格子里（不销毁控件，最稳）
        self.body = QGridLayout(self)
        self.body.setSpacing(5)

        self.apply_mode("grid")
        self.update_style()

    # ---------- 排版 ----------
    def apply_mode(self, mode):
        self.mode = mode

        for column in range(4):
            self.body.setColumnStretch(column, 0)
        for row in range(4):
            self.body.setRowStretch(row, 0)

        if mode == "grid":
            self.body.setContentsMargins(8, 8, 8, 8)
            self.body.setSpacing(5)

            self.cover.setFixedSize(148, 148)
            self._render_cover()

            self.name.setAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self.name.setWordWrap(True)
            self.name.setFixedHeight(34)
            self.name.setStyleSheet(
                "background: transparent; border: none; color: #E9ECF2; font-size: 11px;"
            )

            self.progress.setFixedWidth(148)
            self.progress.setFixedHeight(5)

            self.body.addWidget(self.cover, 0, 0, 1, 1, Qt.AlignmentFlag.AlignHCenter)
            self.body.addWidget(self.name, 1, 0)
            self.body.addWidget(self.progress, 2, 0)
            self.body.setColumnStretch(0, 1)

            self.meta.hide()
            self.setFixedSize(self.GRID_W, self.GRID_H)
        else:
            self.body.setContentsMargins(12, 8, 16, 8)
            self.body.setSpacing(2)
            self.body.setHorizontalSpacing(10)

            self.cover.setFixedSize(48, 48)
            self._render_cover()

            self.name.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
            )
            self.name.setWordWrap(False)
            self.name.setFixedHeight(20)
            self.name.setStyleSheet(
                "background: transparent; border: none; color: #E9ECF2; font-size: 13px; font-weight: 600;"
            )

            self.meta.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            self.meta.setFixedHeight(15)

            self.progress.setFixedWidth(170)
            self.progress.setFixedHeight(6)

            self.body.addWidget(self.cover, 0, 0, 2, 1)
            self.body.addWidget(self.name, 0, 1)
            self.body.addWidget(self.meta, 1, 1)
            self.body.addWidget(
                self.progress, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter
            )
            self.body.setColumnStretch(1, 1)
            self.body.setRowStretch(0, 1)
            self.body.setRowStretch(1, 1)

            self.meta.show()
            self.setFixedHeight(self.LIST_H)

        self._elide_name()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_name()

    def _elide_name(self):
        if self.mode != "list":
            self.name.setText(self.path.name)
            return
        width = max(80, self.width() - 48 - 170 - 60)
        metrics = self.name.fontMetrics()
        self.name.setText(
            metrics.elidedText(self.path.name, Qt.TextElideMode.ElideMiddle, width)
        )

    # ---------- 封面 ----------
    def _render_cover(self):
        size = self.cover.width(), self.cover.height()
        if self._cover_source is not None and not self._cover_source.isNull():
            pix = self._cover_source.scaled(
                size[0], size[1],
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            self.cover.setPixmap(pix)
        else:
            self.cover.setPixmap(make_cover(size[1]))

    def set_cover(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._cover_source = pixmap
        self._render_cover()

    # ---------- 状态 ----------
    def set_status(self, status):
        self.status = status

        if status == "processing":
            self._value = 4
            self.progress.setValue(self._value)
            self.meta.setText("转换中")
            self._timer.start()
        elif status == "success":
            self._timer.stop()
            self._value = 100
            self.progress.setValue(100)
            self.meta.setText("已完成")
        elif status == "failed":
            self._timer.stop()
            self._value = 100
            self.progress.setValue(100)
            self.meta.setText("转换失败")
        else:
            self._timer.stop()
            self._value = 0
            self.progress.setValue(0)
            self.meta.setText("等待转换")

        self.update_style()

    def _tick(self):
        if self._value >= 92:
            return
        step = max(1, int((92 - self._value) / 7) + 1)
        self._value = min(92, self._value + step)
        self.progress.setValue(self._value)
        self.meta.setText(f"转换中 {self._value}%")

    def update_style(self):
        if self.selected:
            background, border = "#2C3648", ACCENT
        elif self.hovered:
            background, border = CARD_HOVER, "#42506A"
        else:
            background, border = CARD, BORDER

        if self.status == "failed":
            chunk = RED
        elif self.status == "success":
            chunk = GREEN
        else:
            chunk = ACCENT

        # 没开始转换时轨道透明，等于“进度条还没出现”
        track = "transparent" if self.status == "waiting" else "#3A4150"

        self.setStyleSheet(f"""
            QFrame#SongCard {{
                background: {background};
                border: 2px solid {border};
                border-radius: 14px;
            }}
            QLabel#CardMeta {{
                color: {SUBTEXT};
                font-size: 11px;
                background: transparent;
                border: none;
            }}
            QProgressBar {{
                background: {track};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {chunk};
                border-radius: 3px;
            }}
        """)

    # ---------- 鼠标 ----------
    def enterEvent(self, event):
        self.hovered = True
        self.update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_click:
            self.on_click(self, event)
        super().mousePressEvent(event)


# =========================
# 自适应网格 / 列表
# =========================

class SongGrid(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.cards = []
        self.selected = []
        self.last_clicked = -1
        self.mode = "grid"
        self._refreshing = False

        self.layout_grid = QGridLayout(self)
        self.layout_grid.setSpacing(16)
        self.layout_grid.setContentsMargins(10, 10, 10, 10)
        self.layout_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.setAcceptDrops(True)

    # ---------- 文件 ----------
    def add_files(self, paths):
        existing = {str(card.path.resolve()) for card in self.cards}
        added = 0

        for path in paths:
            path = Path(path)

            if path.suffix.lower() != ".ncm":
                continue
            if not path.is_file():
                continue
            if str(path.resolve()) in existing:
                continue

            card = SongCard(path)
            card.on_click = self.select_card

            self.cards.append(card)
            existing.add(str(path.resolve()))
            added += 1

        self.refresh()
        return added

    def clear_all(self):
        self._clear_layout()
        self.cards = []
        self.selected = []
        self.last_clicked = -1

    def _clear_layout(self):
        while self.layout_grid.count():
            item = self.layout_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    # ---------- 排版 ----------
    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        self.refresh()

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True

        try:
            self._clear_layout()

            usable = max(240, self.width() - 20)

            if self.mode == "list":
                for index, card in enumerate(self.cards):
                    card.apply_mode("list")
                    card.setFixedWidth(usable)
                    self.layout_grid.addWidget(card, index, 0)
                self.layout_grid.setRowStretch(len(self.cards) + 1, 1)
            else:
                spacing = 16
                columns = max(1, (usable + spacing) // (SongCard.GRID_W + spacing))
                rows = (len(self.cards) + columns - 1) // columns

                for index, card in enumerate(self.cards):
                    card.apply_mode("grid")
                    card.setFixedSize(SongCard.GRID_W, SongCard.GRID_H)
                    self.layout_grid.addWidget(card, index // columns, index % columns)

                self.layout_grid.setRowStretch(rows, 1)

            # 卡片可能被重新加回来，选中态要对上
            for card in self.cards:
                card.selected = card in self.selected
                card.update_style()
        finally:
            self._refreshing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    # ---------- 选中 ----------
    def select_card(self, card, event):
        index = self.cards.index(card)
        modifiers = event.modifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if card in self.selected:
                self.selected.remove(card)
            else:
                self.selected.append(card)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            start = self.last_clicked if self.last_clicked >= 0 else index
            low, high = sorted((start, index))
            self.selected = self.cards[low:high + 1]
        else:
            self.selected = [card]

        self.last_clicked = index
        self.update_selection()

    def update_selection(self):
        for card in self.cards:
            card.selected = card in self.selected
            card.update_style()

        window = self.window()
        if hasattr(window, "update_panels"):
            window.update_panels()

    def clear_selection(self):
        self.selected.clear()
        self.update_selection()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_A and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.selected = self.cards.copy()
            self.update_selection()
        else:
            super().keyPressEvent(event)

    # ---------- 拖拽 ----------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        added = self.add_files(paths)
        if added:
            window = self.window()
            if hasattr(window, "sync_empty_state"):
                window.sync_empty_state()


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
        self._stage = None

        self.export = ExportSettings()

        self.build_ui()
        self.apply_theme()
        self.update_export_hint()

        self.setAcceptDrops(True)

    # ---------- 界面 ----------
    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 20)
        root.setSpacing(18)

        # 顶栏
        header = QHBoxLayout()

        title = QLabel("NCM Studio")
        title.setStyleSheet("font-size: 23px; font-weight: bold; color: white;")

        subtitle = QLabel("Audio Converter")
        subtitle.setStyleSheet(f"color: {SUBTEXT};")

        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(subtitle)
        header.addStretch()

        self.view_button = QPushButton("列表视图")
        self.view_button.setObjectName("GhostButton")
        self.view_button.setFixedHeight(38)
        self.view_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_button.setToolTip("在网格视图和列表视图之间切换")
        self.view_button.clicked.connect(self.toggle_view_mode)
        header.addWidget(self.view_button)

        header.addSpacing(8)

        self.theme_button = QPushButton("◐")
        self.theme_button.setObjectName("GhostButton")
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
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.grid = SongGrid()
        self.scroll.setWidget(self.grid)

        self.empty = QFrame()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("♫")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 60px; color: {ACCENT};")

        empty_title = QLabel("拖放 NCM 文件到这里")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size: 18px; font-weight: bold;")

        empty_hint = QLabel("或点击下方按钮选择文件")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setStyleSheet(f"color: {SUBTEXT};")

        choose_button = QPushButton("选择文件")
        choose_button.setObjectName("AccentButton")
        choose_button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.info_title.setWordWrap(True)
        self.info_title.setStyleSheet("font-size: 17px; font-weight: bold;")

        self.info_detail = QLabel("选择歌曲查看详细信息")
        self.info_detail.setWordWrap(True)

        self.info_layout.addWidget(self.cover_info, 0, Qt.AlignmentFlag.AlignCenter)
        self.info_layout.addWidget(self.info_title)
        self.info_layout.addWidget(self.info_detail)
        self.info_layout.addStretch()

        # 转换按钮 + 导出设置
        self.convert_card = QFrame()
        convert_layout = QVBoxLayout(self.convert_card)
        convert_layout.setContentsMargins(8, 8, 8, 6)
        convert_layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(4)

        self.convert_button = QPushButton("开始转换")
        self.convert_button.setObjectName("AccentButton")
        self.convert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.convert_button.clicked.connect(self.start_conversion)

        self.menu_button = QPushButton("⌄")
        self.menu_button.setFixedWidth(42)
        self.menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_button.setToolTip("导出设置")
        self.menu_button.clicked.connect(self.open_export_menu)

        row.addWidget(self.convert_button, 1)
        row.addWidget(self.menu_button)

        self.export_hint = QLabel()
        self.export_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.export_hint.setStyleSheet(f"color: {SUBTEXT}; font-size: 11px;")

        convert_layout.addLayout(row)
        convert_layout.addWidget(self.export_hint)

        right.addWidget(self.info, 1)
        right.addWidget(self.convert_card)

        body.addLayout(left, 7)
        body.addLayout(right, 3)

        root.addLayout(body, 1)

        self.scroll.hide()
        self.player.hide()

        # 弹出卡片（只建一次，后面反复显示）
        self.export_popup = ExportPopup(self.export, self)
        self.export_popup.changed.connect(self.on_export_changed)

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

            QPushButton#AccentButton {{
                background: {ACCENT};
                color: #12151B;
            }}

            QPushButton#AccentButton:hover {{
                background: #A6C1FF;
            }}

            QPushButton#AccentButton:disabled {{
                background: #3A4457;
                color: #8A93A3;
            }}

            QPushButton#GhostButton {{
                background: #272C36;
                padding: 6px 12px;
            }}

            QPushButton#GhostButton:hover {{
                background: #333A47;
            }}

            QScrollArea {{
                background: transparent;
                border: none;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 0;
            }}

            QScrollBar::handle:vertical {{
                background: #3B4557;
                border-radius: 4px;
                min-height: 30px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: #4C5971;
            }}

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}

            QLabel {{
                background: transparent;
                border: none;
            }}
        """)

        for widget in (self.info, self.convert_card, self.player, self.empty):
            make_shadow(widget)

    def open_export_menu(self):
        if self.export_popup.isVisible():
            self.export_popup.hide()
            return
        self.export_popup.popup_above(self.menu_button)

    def on_export_changed(self):
        self.update_export_hint()
        self.export_popup.reposition(self.menu_button)

    def update_export_hint(self):
        self.export_hint.setText(f"{self.export.format} · {self.export.path_text()}")
        self.menu_button.setToolTip(
            f"导出格式：{self.export.format}\n导出位置：{self.export.path_text()}"
        )

    def toggle_view_mode(self):
        if self.grid.mode == "grid":
            self.grid.set_mode("list")
            self.view_button.setText("网格视图")
        else:
            self.grid.set_mode("grid")
            self.view_button.setText("列表视图")

    # ---------- 文件 ----------
    def sync_empty_state(self):
        has_files = bool(self.grid.cards)
        self.empty.setVisible(not has_files)
        self.scroll.setVisible(has_files)

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 NCM 文件", "", "NCM Files (*.ncm)"
        )
        self.add_files(files)

    def add_files(self, files):
        self.grid.add_files(files)
        self.sync_empty_state()

    # ---------- 拖拽（空白页也能收） ----------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        self.add_files(paths)

    # ---------- 右侧面板 ----------
    def update_panels(self):
        selected = self.grid.selected
        count = len(selected)

        self.player.hide()

        if count == 0:
            self.info_title.setText("文件详情")
            self.info_detail.setText("选择歌曲查看详细信息")
            self.cover_info.show()
            self.cover_info.setPixmap(make_cover(145))
            self.info_detail.show()
            return

        if count == 1:
            card = selected[0]
            path = card.path

            self.info_title.setText(path.stem)

            try:
                size_text = format_size(path.stat().st_size)
            except OSError:
                size_text = "未知"

            self.info_detail.setText(
                f"文件格式：NCM\n\n"
                f"文件大小：{size_text}\n\n"
                f"文件位置：\n{path.parent}"
            )

            if card._cover_source is not None:
                self.cover_info.setPixmap(
                    card._cover_source.scaled(
                        145, 145,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                )
            else:
                self.cover_info.setPixmap(make_cover(145))

            self.cover_info.show()
            self.info_detail.show()

            self.player_name.setText(path.stem)
            self.player.show()
        else:
            total = 0
            for card in selected:
                try:
                    total += card.path.stat().st_size
                except OSError:
                    pass

            self.cover_info.hide()
            self.info_title.setText(f"已选择 {count} 个文件")
            self.info_detail.setText(
                f"文件数量：{count}\n\n"
                f"总大小：{format_size(total)}"
            )

    # =========================
    # 转换队列
    # =========================

    def output_dir_for(self, card):
        if self.export.output_mode == "custom" and self.export.output_path:
            return Path(self.export.output_path)
        return card.path.parent

    def start_conversion(self):
        selected = [c for c in self.grid.selected if c.path.exists()]

        if not selected:
            self.flash_status("请先在左侧选中要转换的文件")
            return

        if not NCM_DUMP.exists():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "找不到 ncmdump",
                f"请把 ncmdump.exe 放到：\n{BASE_DIR / 'tools'}\n或与脚本同一目录。"
            )
            return

        if (self.export.output_mode == "custom"
                and not self.export.output_path):
            self.flash_status("请先设置自定义导出路径")
            return

        self.queue = list(selected)

        for card in self.queue:
            card.set_status("waiting")

        self.convert_button.setEnabled(False)
        self.convert_button.setText("转换中...")

        self.run_next()

    def flash_status(self, text):
        self.export_hint.setText(text)
        QTimer.singleShot(2600, self.update_export_hint)

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
        self._stage = "dump"

        process = QProcess(self)
        self.current_process = process

        process.readyReadStandardOutput.connect(self.read_output)
        process.readyReadStandardError.connect(self.read_error)
        process.finished.connect(self.process_finished)
        process.errorOccurred.connect(self.process_error)

        args = [str(card.path)]
        if self.export.output_mode == "custom" and self.export.output_path:
            out_dir = Path(self.export.output_path)
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                card.set_status("failed")
                self.flash_status("导出目录不可用，已跳过")
                self.current_job = None
                self.current_process = None
                QTimer.singleShot(180, self.run_next)
                return
            args.extend(["-o", str(out_dir)])

        process.start(str(NCM_DUMP), args)

    def process_error(self, error):
        # 进程根本没起来（exe 丢了 / 被杀软拦了），finished 不会触发，得自己收尾
        if error != QProcess.ProcessError.FailedToStart:
            return

        card = self.current_job
        if card is not None:
            card.set_status("failed")

        self.flash_status("无法启动 ncmdump.exe，请检查 tools 目录")
        self.current_job = None
        self.current_process = None
        QTimer.singleShot(220, self.run_next)

    def read_output(self):
        if not self.current_process:
            return

        data = bytes(self.current_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.output_buffer += data

    def read_error(self):
        if not self.current_process:
            return

        data = bytes(self.current_process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        self.output_buffer += data

    def process_finished(self, exit_code, exit_status):
        card = self.current_job
        self.current_process = None

        if card is None:
            QTimer.singleShot(150, self.run_next)
            return

        lines = [line.strip() for line in self.output_buffer.lower().splitlines()]
        has_done = "done" in lines
        has_error = "error" in lines

        ok = (
            has_done
            and not has_error
            and exit_code == 0
            and exit_status == QProcess.ExitStatus.NormalExit
        )

        if not ok:
            card.set_status("failed")
            self.current_job = None
            QTimer.singleShot(180, self.run_next)
            return

        produced = self.find_output(card)
        if produced is not None:
            self.attach_cover(card, produced)

        target = self.export.format.lower()
        needs_convert = (
            produced is not None
            and target in ("mp3", "wav")
            and produced.suffix.lower().lstrip(".") != target
        )

        if needs_convert and FFMPEG.exists():
            self.transcode(card, produced, target)
            return

        if needs_convert:
            self.flash_status(f"未找到 ffmpeg，{target.upper()} 已保留为 FLAC")

        card.set_status("success")
        self.current_job = None
        QTimer.singleShot(180, self.run_next)

    def find_output(self, card):
        out_dir = self.output_dir_for(card)
        stem = card.path.stem

        for ext in (".flac", ".mp3", ".wav"):
            candidate = out_dir / f"{stem}{ext}"
            if candidate.exists() and candidate.resolve() != card.path.resolve():
                return candidate

        try:
            candidates = [
                p for p in out_dir.glob(f"{stem}.*")
                if p.suffix.lower() in (".flac", ".mp3", ".wav")
            ]
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_mtime)
        except OSError:
            pass

        return None

    def attach_cover(self, card, produced):
        image = read_flac_cover(produced)
        if not image:
            return

        pixmap = QPixmap()
        if pixmap.loadFromData(image):
            card.set_cover(pixmap)
            if card in self.grid.selected:
                self.update_panels()

    def transcode(self, card, source, target):
        """ncmdump 只会吐 flac，要 mp3/wav 就得再走一遍 ffmpeg。"""
        destination = source.with_suffix(f".{target}")

        process = QProcess(self)
        self.current_process = process
        self._stage = "ffmpeg"

        def finish(success):
            if success:
                try:
                    source.unlink()
                except OSError:
                    pass
                card.set_status("success")
            else:
                card.set_status("failed")

            self.current_process = None
            self.current_job = None
            QTimer.singleShot(180, self.run_next)

        def on_finished(code, status):
            finish(
                code == 0
                and status == QProcess.ExitStatus.NormalExit
                and destination.exists()
            )

        process.finished.connect(on_finished)
        process.errorOccurred.connect(
            lambda error: error == QProcess.ProcessError.FailedToStart and finish(False)
        )
        process.start(str(FFMPEG), [
            "-y", "-i", str(source), "-map_metadata", "0", str(destination)
        ])

    def closeEvent(self, event):
        if self.current_process is not None:
            try:
                self.current_process.kill()
            except Exception:
                pass
        super().closeEvent(event)


# =========================
# 启动
# =========================

if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
