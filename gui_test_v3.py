"""
NCM Studio - v3

在 v2 基础上的四个变化：
1. 导出格式改成「统一导出格式」开关：默认不勾 = 保留 ncmdump 原样输出（flac/mp3 混合，不需要 ffmpeg）；
   勾上才出现 FLAC/MP3/WAV 三个选项，转完逐个比对，不一致的用 ffmpeg 调成所选格式
2. 每次点「开始转换」建一个以时间戳命名的文件夹（精确到秒），这一批的输出全进这个文件夹；
   自定义路径只填父目录，时间戳文件夹在它下面
3. 默认转换全部导入的文件，不用手动全选；右键某张卡 = 本次不转换（可再点恢复）
4. 结束提示可点，直接打开本次输出文件夹

v3.1 修正（实测 ncmdump 1.5.1 之后）：
5. 成功判定不再看输出文本/退出码 —— ncmdump 成功失败都是 exit 0，成功打印的是带 ANSI 颜色码的
   `[Done] 'a.ncm' -> 'a.mp3'`，之前逐行精确匹配 "done" 永远匹配不上，导致全都标红。现在只看
   临时夹里有没有真的生成音频文件（且不是 0 字节）。
6. 时间戳文件夹改成惰性创建：ncmdump 先往根目录下的 `.ncmstudio_tmp` 吐，确实转出来了才建时间戳
   文件夹并把文件搬进去（同盘 rename，瞬时），所以一首都没成功时不会留下空文件夹。
7. 每次转换会把 ncmdump 的原始输出（去掉 ANSI 颜色码）追加到 `ncmstudio.log`，出问题直接看这个文件。

v3.2（用户机器上「全红」之后重写执行方式）：
8. 不再用 QProcess：改成 subprocess.Popen 直接拉起来，输出重定向到文件（不走管道），
   每 120ms 轮询一次进程结束没有 —— 跟手敲命令的方式一模一样。
9. 不再经过 `.ncmstudio_tmp` 临时夹：ncmdump 直接写进时间戳文件夹，少一次搬运；
   一首都没成功的话，跑完把这批的空文件夹删掉，不留垃圾。
"""

import sys
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize, QPoint, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QFileDialog, QProgressBar, QMenu,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit, QCheckBox,
    QSizePolicy, QGraphicsDropShadowEffect, QGraphicsOpacityEffect
)

# =========================
# 基础配置
# =========================

def _app_dir():
    """程序自己的目录：源码运行时是 .py 所在目录，打包成 exe 后是 exe 所在目录。
    （PyInstaller 单文件模式下 __file__ 指向临时解包目录，exe 一关就没了，
      所以不能用它来存日志和工作文件。）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundle_dir():
    """打包时被塞进 exe 里的资源所在目录（源码运行时就是脚本目录）。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return _app_dir()


BASE_DIR = _app_dir()

# 不弹黑框；每 120ms 看一眼进程结束没有
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POLL_MS = 120


def _find_tool(name):
    """按顺序找：exe 自带资源 → exe 同级 tools/ → exe 同级 → 脚本目录。"""
    candidates = (
        _bundle_dir() / "tools" / name,
        _bundle_dir() / name,
        BASE_DIR / "tools" / name,
        BASE_DIR / name,
    )

    for candidate in candidates:
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
AMBER = "#D9A441"
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


LOG_FILE = BASE_DIR / "ncmstudio.log"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_output(text):
    """ncmdump 的输出带 ANSI 颜色码（\x1b[1m 之类），去掉才看得清。"""
    return _ANSI_RE.sub("", text or "").strip()


def log_line(text):
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            LOG_FILE.unlink()
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def unique_path(path):
    """目标已存在就加 (2)(3)…，绝不覆盖已有文件。"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


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
        self.unify = False          # 是否统一导出格式
        self.format = "FLAC"        # 统一时才用：FLAC / MP3 / WAV
        self.output_mode = "source"  # source / custom
        self.output_path = ""

    def path_text(self):
        if self.output_mode == "custom" and self.output_path:
            return self.output_path
        return "原文件所在目录"

    def describe(self):
        if self.unify:
            return f"统一为 {self.format} · {self.path_text()}"
        return f"保持原样 · {self.path_text()}"


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

        self.unify_box = QCheckBox("统一导出格式")
        self.unify_box.setObjectName("UnifyBox")
        self.unify_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unify_box.setToolTip(
            "不勾：ncmdump 吐什么就是什么（flac/mp3 可能混着来，不需要 ffmpeg）\n"
            "勾上：全部转成同一个格式，缺的格式用 ffmpeg 补"
        )
        self.unify_box.toggled.connect(self.set_unify)
        lay.addWidget(self.unify_box)

        # 勾了才出现的那排格式按钮
        self.format_seg = QWidget()
        seg = QHBoxLayout(self.format_seg)
        seg.setContentsMargins(0, 2, 0, 0)
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

        lay.addWidget(self.format_seg)
        self.format_seg.hide()

        self.warn_label = QLabel("tools 里没有 ffmpeg.exe，补不了其他格式")
        self.warn_label.setObjectName("WarnLabel")
        self.warn_label.setWordWrap(True)
        self.warn_label.hide()
        lay.addWidget(self.warn_label)

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

            QLabel#WarnLabel {{
                color: {AMBER};
                font-size: 11px;
                background: transparent;
            }}

            QCheckBox {{
                color: #E6E9EE;
                font-size: 13px;
                spacing: 9px;
                padding: 2px 0;
                background: transparent;
            }}

            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 2px solid #59637A;
                background: transparent;
            }}

            QCheckBox::indicator:hover {{
                border: 2px solid #7E8AA5;
            }}

            QCheckBox::indicator:checked {{
                background: {ACCENT};
                border: 2px solid {ACCENT};
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
        self.unify_box.blockSignals(True)
        self.unify_box.setChecked(self.settings.unify)
        self.unify_box.blockSignals(False)
        self.format_seg.setVisible(self.settings.unify)

        need_ffmpeg = self.settings.unify and not FFMPEG.exists()
        self.warn_label.setVisible(need_ffmpeg)

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

    def set_unify(self, on):
        self.settings.unify = bool(on)
        self.format_seg.setVisible(bool(on))
        self.adjustSize()
        self.changed.emit()

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
        self.excluded = False        # 本次不转换
        self.mode = "grid"
        self._value = 0
        self.on_click = None
        self.on_context = None
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

        # 右上角的「已排除」角标，直接浮在卡片上，不进布局
        self.badge = QLabel("已排除", self)
        self.badge.setObjectName("CardBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.hide()

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
            self._apply_name_style()

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
            self._apply_name_style()

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
        self._place_badge()

    def _apply_name_style(self):
        color = "#6F7787" if self.excluded else "#E9ECF2"
        if self.mode == "grid":
            self.name.setStyleSheet(
                f"background: transparent; border: none; color: {color}; font-size: 11px;"
            )
        else:
            self.name.setStyleSheet(
                f"background: transparent; border: none; color: {color};"
                " font-size: 13px; font-weight: 600;"
            )

    def _place_badge(self):
        if not self.badge.isVisible():
            return
        self.badge.adjustSize()
        if self.mode == "grid":
            self.badge.move(self.width() - self.badge.width() - 8, 8)
        else:
            # 列表里放在进度条那块儿的竖直中间，正好顶替该出现进度条的位置
            self.badge.move(
                self.width() - self.badge.width() - 12,
                (self.height() - self.badge.height()) // 2,
            )
        self.badge.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_name()
        self._place_badge()

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

    # ---------- 排除 ----------
    def set_excluded(self, on):
        self.excluded = bool(on)
        self.badge.setVisible(self.excluded)
        self._place_badge()

        font = self.name.font()
        font.setStrikeOut(self.excluded)
        self.name.setFont(font)

        if self.excluded:
            effect = QGraphicsOpacityEffect(self.cover)
            effect.setOpacity(0.45)
            self.cover.setGraphicsEffect(effect)
        else:
            self.cover.setGraphicsEffect(None)

        self._apply_name_style()
        self.update_style()

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
        elif status == "mismatch":
            self._timer.stop()
            self._value = 100
            self.progress.setValue(100)
            self.meta.setText("格式未统一")
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

        if self.excluded:
            background = "#1B1E25"

        if self.status == "failed":
            chunk = RED
        elif self.status == "mismatch":
            chunk = AMBER
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
            QLabel#CardBadge {{
                background: #3A2F33;
                color: #D08C8C;
                border: 1px solid #4A3A3F;
                border-radius: 7px;
                padding: 1px 7px;
                font-size: 10px;
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
        if event.button() == Qt.MouseButton.RightButton and self.on_context:
            self.on_context(self, event)
            return
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
            card.on_context = self.show_context_menu

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

    # ---------- 排除（本次不转换） ----------
    def cards_for_run(self):
        """默认全转：所有导入的、没被排除的、还存在的文件。"""
        return [c for c in self.cards if not c.excluded and c.path.exists()]

    def show_context_menu(self, card, event):
        # 右键点在未选中的卡上，先把它变成唯一选中（资源管理器那套习惯）
        if card not in self.selected:
            self.selected = [card]
            self.last_clicked = self.cards.index(card)
            self.update_selection()

        targets = [c for c in self.selected if c in self.cards]
        if not targets:
            return

        all_excluded = all(c.excluded for c in targets)
        window = self.window()
        busy = (
            any(c.status == "processing" for c in targets)
            or bool(getattr(window, "_running", False))
        )

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #292D36;
                color: #F2F3F5;
                border: 1px solid #414754;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 20px;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background: #414B60;
            }
            QMenu::item:disabled {
                color: #6B7382;
            }
        """)
        action = menu.addAction("恢复转换" if all_excluded else "本次不转换")
        if busy:
            action.setText("转换中，先别改")
            action.setEnabled(False)

        chosen = menu.exec(event.globalPosition().toPoint())
        if chosen is not action:
            return

        state = not all_excluded
        for target in targets:
            if target.status == "processing":
                continue
            target.set_excluded(state)

        if hasattr(window, "update_run_hint"):
            window.update_run_hint()

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


class ClickableLabel(QLabel):

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
        self.current_job = None
        self.output_buffer = ""
        self._stage = None

        # 跑 ncmdump / ffmpeg 用的（Popen + 定时轮询，不用 QProcess）
        self._proc = None
        self._proc_out = None
        self._proc_tag = "dump"
        self._proc_cb = None
        self._proc_timer = QTimer(self)
        self._proc_timer.setInterval(POLL_MS)
        self._proc_timer.timeout.connect(self.poll_process)

        self.batch_name = None          # 本次的时间戳文件夹名
        self.batch_dirs = {}            # 根目录 -> 本次的时间戳文件夹（惰性创建）
        self._running = False
        self._last_out_dir = None       # 上一次的输出目录，用来「点这里打开文件夹」
        self.run_total = 0
        self.run_success = 0
        self.run_failed = 0
        self.run_mismatch = 0

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

        empty_hint = QLabel("或点击下方按钮选择文件 · 导入后右键可临时排除")
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

        self.export_hint = ClickableLabel()
        self.export_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.export_hint.setStyleSheet(f"color: {SUBTEXT}; font-size: 11px;")
        self.export_hint.clicked.connect(self.open_output_folder)

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

    def idle_hint_text(self):
        base = self.export.describe()
        total = len(self.grid.cards)
        if not total:
            return base
        pending = len(self.grid.cards_for_run())
        return f"{base} · {pending}/{total} 首待转换"

    def update_export_hint(self):
        if self._running:
            return
        self.export_hint.setText(self.idle_hint_text())
        self.menu_button.setToolTip(f"导出设置：{self.export.describe()}")

    def update_run_hint(self):
        if self._running:
            done = min(
                self.run_total,
                self.run_success + self.run_failed + self.run_mismatch,
            )
            self.export_hint.setText(f"转换中 {done}/{self.run_total}")
            self.convert_button.setText(f"转换中 {done}/{self.run_total}")
        else:
            self.update_export_hint()

    def open_output_folder(self):
        target = self._last_out_dir
        if not target:
            return
        path = Path(target)
        if not path.exists():
            self.flash_status("输出文件夹已经不在了")
            return
        try:
            os.startfile(str(path))
        except (OSError, AttributeError):
            self.flash_status("打不开文件夹")

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
        self.update_export_hint()

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

            note = "\n\n状态：已排除，本次不转换" if card.excluded else ""
            self.info_detail.setText(
                f"文件格式：NCM\n\n"
                f"文件大小：{size_text}\n\n"
                f"文件位置：\n{path.parent}" + note
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

            skipped = len([c for c in selected if c.excluded])
            self.cover_info.hide()
            self.info_title.setText(f"已选择 {count} 个文件")
            self.info_detail.setText(
                f"文件数量：{count}\n\n"
                f"总大小：{format_size(total)}"
                + (f"\n\n其中 {skipped} 首标记为不转换" if skipped else "")
            )

    # =========================
    # 转换队列
    # =========================

    def root_for(self, card):
        if self.export.output_mode == "custom" and self.export.output_path:
            return Path(self.export.output_path)
        return card.path.parent

    def batch_dir_for(self, card):
        """时间戳文件夹的路径 —— 注意这里只是算出来，还不创建。"""
        root = self.root_for(card)
        key = str(root)

        if key not in self.batch_dirs:
            name = self.batch_name or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            target = root / name

            # 同一秒重复跑不该往同一个文件夹里塞，重名就加后缀
            index = 2
            while target.exists() and (not target.is_dir() or any(target.iterdir())):
                target = root / f"{name}-{index}"
                index += 1

            self.batch_dirs[key] = target

        return self.batch_dirs[key]

    def work_dir_for(self, card):
        """先让 ncmdump 往程序自己的目录里写 —— 网易云那个下载文件夹有时候不认子进程的写入，
        写到自己的地盘上最保险；转完用 Python 搬到时间戳文件夹里。"""
        name = self.batch_name or "batch"
        target = BASE_DIR / "_work" / name
        return target

    def cleanup_work(self):
        """跑完把程序目录里的工作夹清掉（里面还剩东西说明搬运失败过，留着看日志）。"""
        if not self.batch_name:
            return

        work = BASE_DIR / "_work" / self.batch_name

        try:
            if work.exists():
                if any(work.iterdir()):
                    log_line(f"工作夹里还有没搬走的文件：{work}")
                else:
                    work.rmdir()

            parent = work.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    def cleanup_empty_dirs(self):
        """一首都没成功的目录不该留下：空的时间戳文件夹删掉，剩下的记住最后一个。"""
        kept = []

        for path in self.batch_dirs.values():
            try:
                if not path.exists():
                    continue
                if any(path.iterdir()):
                    kept.append(path)
                    continue
                path.rmdir()
            except OSError:
                pass

        if kept:
            self._last_out_dir = kept[-1]

        self.batch_dirs.clear()

    def start_conversion(self):
        if self._running:
            return

        queue = self.grid.cards_for_run()

        if not queue:
            if self.grid.cards:
                self.flash_status("没有可转换的文件（都标记成不转换了）")
            else:
                self.flash_status("先导入 NCM 文件再开始")
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

        # 时间戳只生成一次，整批共用
        self.batch_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.batch_dirs.clear()
        self._last_out_dir = None

        self.queue = list(queue)
        self.run_total = len(queue)
        self.run_success = 0
        self.run_failed = 0
        self.run_mismatch = 0
        self._running = True

        for card in self.queue:
            card.set_status("waiting")

        self.convert_button.setEnabled(False)
        self.update_run_hint()

        self.run_next()

    def flash_status(self, text, hold=False):
        self.export_hint.setText(text)
        self.export_hint.setCursor(Qt.CursorShape.ArrowCursor)
        if hold or self._running:
            return
        QTimer.singleShot(2600, self.update_export_hint)

    def run_next(self):
        if not self.queue:
            self.current_job = None
            self.finish_run()
            return

        card = self.queue.pop(0)

        if not card.path.exists():
            card.set_status("failed")
            self.run_failed += 1
            self.update_run_hint()
            QTimer.singleShot(80, self.run_next)
            return

        out_dir = self.work_dir_for(card)

        self.current_job = card
        card.set_status("processing")
        self.output_buffer = ""
        self.claimed_output = None
        self._stage = "dump"
        self.update_run_hint()

        argv = [str(card.path), "-o", str(out_dir)]
        log_line(f"start | exe={NCM_DUMP} | out={out_dir} | argv={argv}")

        error = self.spawn(NCM_DUMP, argv, "dump", self.on_dump_done)

        if error is not None:
            log_line(f"start failed | {error}")
            card.set_status("failed")
            self.run_failed += 1
            self.current_job = None
            self.flash_status("启动 ncmdump.exe 失败，看下 ncmstudio.log")
            self.update_run_hint()
            QTimer.singleShot(120, self.run_next)

    def finish_run(self):
        self._running = False
        self.cleanup_work()
        self.cleanup_empty_dirs()

        # 排除标记只在这一次生效，跑完清掉，免得下次莫名其妙漏转
        for card in self.grid.cards:
            if card.excluded:
                card.set_excluded(False)

        parts = [f"完成 {self.run_success} 首"]
        if self.run_failed:
            parts.append(f"失败 {self.run_failed}")
        if self.run_mismatch:
            parts.append(f"未统一 {self.run_mismatch}")

        text = " · ".join(parts)

        if self._last_out_dir and Path(self._last_out_dir).exists():
            text += " · 点这里打开文件夹"
            self.export_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.export_hint.setCursor(Qt.CursorShape.ArrowCursor)

        self.export_hint.setText(text)
        self.convert_button.setEnabled(True)
        self.convert_button.setText("开始转换")

    def spawn(self, program, args, tag, on_done):
        """直接用 Popen 起进程，输出重定向到文件 —— 不走管道、不走 QProcess，
        跟手敲命令一模一样。返回 None = 起来了；返回字符串 = 没起来的原因。"""
        program = Path(program)
        out_path = BASE_DIR / f".ncmstudio_{tag}.out"

        try:
            handle = open(out_path, "wb")
        except OSError as exc:
            return f"打不开输出文件 {out_path}：{exc}"

        try:
            proc = subprocess.Popen(
                [str(program), *[str(a) for a in args]],
                cwd=str(program.parent),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            handle.close()
            return f"启动失败：{exc}"

        self._proc = proc
        self._proc_out = handle
        self._proc_tag = tag
        self._proc_cb = on_done
        self._proc_timer.start(POLL_MS)
        return None

    def poll_process(self):
        """QTimer 每 POLL_MS 看一眼进程还在不在，出来了就去读输出文件。"""
        proc = self._proc

        if proc is None:
            self._proc_timer.stop()
            return

        code = proc.poll()

        if code is None:
            return

        self._proc_timer.stop()
        self._proc = None

        handle = self._proc_out
        self._proc_out = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

        out_path = BASE_DIR / f".ncmstudio_{self._proc_tag}.out"
        try:
            text = out_path.read_text(encoding="utf-8", errors="replace")
            out_path.unlink()
        except OSError:
            text = ""

        callback = self._proc_cb
        self._proc_cb = None

        if callback is not None:
            callback(code, text)

    def on_dump_done(self, exit_code, raw_output):
        card = self.current_job

        if card is None:
            QTimer.singleShot(150, self.run_next)
            return

        output = clean_output(raw_output)
        self.claimed_output = self.parse_done_target(output)
        log_line(
            f"dump {card.path.name} | src={card.path} | exit={exit_code} "
            f"| out={str(self.work_dir_for(card))} | 看到={self.list_staging(card)} "
            f"| 声称={self.claimed_output} "
            f"| {output.replace(chr(10), ' / ') or '(no output)'}"
        )

        # 判断成功只看一件事：临时夹里有没有真的生成音频文件
        # （ncmdump 成功失败的 exit code 都是 0，输出文本还带 ANSI 颜色码，不可靠）
        produced = self.find_output(card)

        if produced is not None:
            self.finish_with_file(card, produced)
            return

        # 没找到也别急着判死：杀软扫描有时会让文件晚一两百毫秒才落地，重试三次
        self.retry_find(card, 1)

    def parse_done_target(self, text):
        # ncmdump 成功时会打印 [Done] 'src' -> 'target'，把 target 抠出来
        match = re.search(r"\[Done\][^\n]*->\s*'(.*)'", text)
        if not match:
            return None

        raw = match.group(1).strip()
        if not raw:
            return None

        path = Path(raw)
        if path.is_absolute():
            return path

        # 相对路径是相对 ncmdump 自己的工作目录写的
        card = self.current_job
        base = self.work_dir_for(card) if card is not None else Path.cwd()
        return (base / path).resolve()

    def list_staging(self, card):
        # 日志里记一眼输出目录当前的样子，排查「[Done] 但文件不见了」用
        staging = self.work_dir_for(card)
        try:
            if not staging.exists():
                return "无目录"
            items = [(p.name, p.stat().st_size) for p in staging.iterdir()]
            return items or "空目录"
        except OSError as exc:
            return f"<读不到：{exc}>"

    def retry_find(self, card, attempt):
        produced = self.find_output(card)

        if produced is not None:
            self.finish_with_file(card, produced)
            return

        if attempt <= 3:
            QTimer.singleShot(300, lambda: self.retry_find(card, attempt + 1))
            return

        # 真的没有了：把现场写进日志，方便排查
        staging = self.work_dir_for(card)
        try:
            listing = [(p.name, p.stat().st_size) for p in staging.iterdir()]
        except OSError as exc:
            listing = f"<读不到目录：{exc}>"

        log_line(f"FAIL {card.path.name} | dir={staging} | exists={staging.exists()} | 内容={listing}")
        self.fail_current(card)

    def finish_with_file(self, card, produced):
        # 到这一步才创建时间戳文件夹，把文件搬进去（同盘移动，瞬间）
        batch_dir = self.batch_dir_for(card)

        try:
            batch_dir.mkdir(parents=True, exist_ok=True)
            final = unique_path(batch_dir / produced.name)
            shutil.move(str(produced), str(final))
        except OSError as exc:
            log_line(f"move failed: {produced} -> {batch_dir} | {exc}")
            card.set_status("failed")
            self.run_failed += 1
            self.current_job = None
            self.flash_status("转好了，但搬不进目标文件夹（权限？），换个导出路径试试")
            self.update_run_hint()
            QTimer.singleShot(140, self.run_next)
            return

        log_line(f"moved {produced.name} -> {final} | {final.stat().st_size} B")
        self._last_out_dir = batch_dir
        self.attach_cover(card, final)

        # 没勾统一格式 = 原样收下，ncmdump 吐什么就是什么
        if not self.export.unify:
            self.succeed_current(card)
            return

        want = self.export.format.lower()
        have = final.suffix.lower().lstrip(".")

        if want == have:
            self.succeed_current(card)
            return

        if not FFMPEG.exists():
            card.set_status("mismatch")
            self.run_mismatch += 1
            self.current_job = None
            self.update_run_hint()
            QTimer.singleShot(120, self.run_next)
            return

        self.transcode(card, final, want)

    def fail_current(self, card):
        card.set_status("failed")
        self.run_failed += 1
        self.current_job = None
        self.update_run_hint()
        QTimer.singleShot(140, self.run_next)

    def succeed_current(self, card):
        card.set_status("success")
        self.run_success += 1
        self.current_job = None
        self.update_run_hint()
        QTimer.singleShot(140, self.run_next)

    def find_output(self, card):
        staging = self.work_dir_for(card)
        stem = card.path.stem

        # 先看 ncmdump 自己报的那个路径 —— 万一它写的不是我们以为的那个目录
        claimed = getattr(self, "claimed_output", None)
        if claimed is not None:
            try:
                if claimed.is_file() and claimed.stat().st_size > 0:
                    return claimed
            except OSError:
                pass

        try:
            audio = [
                p for p in staging.iterdir()
                if p.suffix.lower() in (".flac", ".mp3", ".wav")
                and p.stat().st_size > 0
            ]
        except OSError:
            return None

        # 正常情况下 ncmdump 用源文件名，直接对上
        for path in audio:
            if path.stem == stem:
                return path

        # 对不上但只有一个音频文件，那多半是改名了，也认
        if len(audio) == 1:
            log_line(f"文件名对不上，按唯一产物收下：{audio[0].name}（源 {card.path.name}）")
            return audio[0]

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
        """把 ncmdump 吐出来的格式，用 ffmpeg 调成用户选的那个。"""
        destination = unique_path(source.with_suffix(f".{target}"))
        tmp = destination.with_name(
            f"{destination.stem}.__tmp{destination.suffix}"
        )

        if target == "wav":
            args = [
                "-y", "-i", str(source),
                "-map", "0:a", "-c:a", "pcm_s16le",
                str(tmp),
            ]
        elif target == "mp3":
            args = [
                "-y", "-i", str(source),
                "-map", "0:a", "-map", "0:v?",
                "-c:v", "copy",
                "-c:a", "libmp3lame", "-q:a", "0",
                "-id3v2_version", "3",
                str(tmp),
            ]
        else:
            args = [
                "-y", "-i", str(source),
                "-map", "0:a", "-map", "0:v?",
                "-c:v", "copy",
                "-c:a", "flac",
                str(tmp),
            ]

        self._stage = "ffmpeg"

        def finish(success):
            self.current_job = None

            if success:
                try:
                    tmp.replace(destination)
                    source.unlink()
                except OSError:
                    success = False

            if success:
                card.set_status("success")
                self.run_success += 1
            else:
                # 半成品一定要清掉，否则下次会被当成正常产出
                try:
                    tmp.unlink()
                except OSError:
                    pass
                card.set_status("failed")
                self.run_failed += 1

            self.update_run_hint()
            QTimer.singleShot(120, self.run_next)

        def on_ffmpeg_done(code, raw_output):
            log_line(f"ffmpeg {source.name} | exit={code} | {clean_output(raw_output)[:300]}")
            good = (
                code == 0
                and tmp.exists()
                and tmp.stat().st_size > 1024
            )
            finish(good)

        error = self.spawn(FFMPEG, args, "ffmpeg", on_ffmpeg_done)

        if error is not None:
            log_line(f"ffmpeg start failed | {error}")
            finish(False)

    def closeEvent(self, event):
        # 窗口关了就别再让 ncmdump 在背后跑
        self._proc_timer.stop()

        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

        handle = self._proc_out
        if handle is not None:
            try:
                handle.close()
            except OSError:
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
