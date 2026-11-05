import sys
import os
import shutil
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QFrame, 
                               QListWidget, QListView, QListWidgetItem, QGraphicsDropShadowEffect)
from PySide6.QtCore import Qt, QSize, QProcess
from PySide6.QtGui import QIcon, QColor, QFont

# ================= 苹果风深色主题 =================
DARK_QSS = """
    QMainWindow { background-color: #121212; }
    /* 卡片圆角加大到 18px，更圆润 */
    QFrame#Card { background-color: #1E1E1E; border-radius: 18px; border: none; }
    QLabel { color: #E0E0E0; font-family: "Segoe UI", "Microsoft YaHei"; border: none; }
    QLabel#DropLabel { color: #666666; font-size: 22px; font-weight: bold; }
    QListWidget { background-color: transparent; border: none; color: #E0E0E0; outline: none; }
    QListWidget::item { padding: 5px; border-radius: 10px; }
    QListWidget::item:hover { background-color: #2D2D2D; }
    QListWidget::item:selected { background-color: #0A84FF; color: white; }
    
    /* 按钮改为胶囊形状 */
    QPushButton#StartBtn { 
        background-color: #0A84FF; color: white; font-size: 16px; font-weight: bold; 
        border-radius: 22px; padding: 10px 25px; 
    }
    QPushButton#StartBtn:hover { background-color: #0063b1; }
    QPushButton#StartBtn:disabled { background-color: #333333; color: #666666; }
    
    QPushButton#ArrowBtn, QPushButton#ThemeBtn, QPushButton#ViewBtn, QPushButton#AddLrcBtn { 
        background-color: #2D2D2D; color: #E0E0E0; border-radius: 16px; font-size: 13px; padding: 8px 15px; 
    }
    QPushButton#ArrowBtn:hover, QPushButton#ThemeBtn:hover, QPushButton#ViewBtn:hover, QPushButton#AddLrcBtn:hover { 
        background-color: #3E3E3E; 
    }
    QPushButton#ArrowBtn { border-radius: 22px; padding: 0; }
"""

LIGHT_QSS = """
    QMainWindow { background-color: #F5F6F8; }
    QFrame#Card { background-color: #FFFFFF; border-radius: 18px; border: none; }
    QLabel { color: #333333; font-family: "Segoe UI", "Microsoft YaHei"; border: none; }
    QLabel#DropLabel { color: #999999; font-size: 22px; font-weight: bold; }
    QListWidget { background-color: transparent; border: none; color: #333333; outline: none; }
    QListWidget::item { padding: 5px; border-radius: 10px; }
    QListWidget::item:hover { background-color: #E5E5EA; }
    QListWidget::item:selected { background-color: #0078D4; color: white; }
    QPushButton#StartBtn { 
        background-color: #0078D4; color: white; font-size: 16px; font-weight: bold; 
        border-radius: 22px; padding: 10px 25px; 
    }
    QPushButton#StartBtn:hover { background-color: #0063b1; }
    QPushButton#StartBtn:disabled { background-color: #CCCCCC; color: #888888; }
    QPushButton#ArrowBtn, QPushButton#ThemeBtn, QPushButton#ViewBtn, QPushButton#AddLrcBtn { 
        background-color: #E5E5EA; color: #333333; border-radius: 16px; font-size: 13px; padding: 8px 15px; 
    }
    QPushButton#ArrowBtn:hover, QPushButton#ThemeBtn:hover, QPushButton#ViewBtn:hover, QPushButton#AddLrcBtn:hover { 
        background-color: #D1D1D6; 
    }
    QPushButton#ArrowBtn { border-radius: 22px; padding: 0; }
"""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NCM Studio")
        self.resize(1000, 650)
        
        # --- 核心数据变量 ---
        self.is_dark_mode = True
        self.setStyleSheet(DARK_QSS)
        
        self.ncm_files = []       # 存要转换的 ncm 路径
        self.lrc_library = {}     # 存 lrc 路径的字典 { '歌曲名': '路径' }
        self.output_dir = ""      # 输出目录
        self.current_index = 0    # 当前转换进度
        self.process = None       # 后台跑 ncmdump 的进程

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(25)

        # ================= 左侧主工作区 =================
        left_layout = QVBoxLayout()
        left_layout.setSpacing(20)
        
        self.card_main = QFrame()
        self.card_main.setObjectName("Card")
        self.apply_shadow(self.card_main)
        card_main_layout = QVBoxLayout(self.card_main)
        card_main_layout.setContentsMargins(20, 20, 20, 20)
        
        # 顶部工具栏
        top_tools = QHBoxLayout()
        self.btn_view = QPushButton("切换为列表视图")
        self.btn_view.setObjectName("ViewBtn")
        self.btn_view.clicked.connect(self.toggle_view_mode)
        
        # 手动导入LRC按钮
        self.btn_add_lrc = QPushButton("手动导入 LRC")
        self.btn_add_lrc.setObjectName("AddLrcBtn")
        # (此处预留位置，后面可写手动导入逻辑，目前先用拖拽实现)
        
        top_tools.addWidget(self.btn_add_lrc)
        top_tools.addStretch()
        top_tools.addWidget(self.btn_view)
        card_main_layout.addLayout(top_tools)

        self.label_drop = QLabel("拖入 .ncm 或 .lrc 文件于此")
        self.label_drop.setObjectName("DropLabel")
        self.label_drop.setAlignment(Qt.AlignCenter)
        card_main_layout.addWidget(self.label_drop)
        
        self.file_list = QListWidget()
        self.file_list.setViewMode(QListView.IconMode)
        self.file_list.setResizeMode(QListView.Adjust)
        self.file_list.setIconSize(QSize(80, 80)) # 稍微调小一点点图标
        self.file_list.setGridSize(QSize(110, 120))
        self.file_list.setSpacing(15)
        self.file_list.hide() 
        self.file_list.itemSelectionChanged.connect(self.on_selection_changed)
        card_main_layout.addWidget(self.file_list)
        
        # 左侧底栏（状态与进度）
        self.card_player = QFrame()
        self.card_player.setObjectName("Card")
        self.apply_shadow(self.card_player)
        self.card_player.setFixedHeight(80)
        player_layout = QHBoxLayout(self.card_player)
        
        self.label_status = QLabel("等待导入文件...")
        self.label_status.setFont(QFont("Microsoft YaHei", 10))
        self.label_status.setAlignment(Qt.AlignCenter)
        self.label_status.setStyleSheet("color: #888888;")
        player_layout.addWidget(self.label_status)

        left_layout.addWidget(self.card_main, stretch=8)
        left_layout.addWidget(self.card_player, stretch=1)

        # ================= 右侧信息面板 =================
        right_layout = QVBoxLayout()
        right_layout.setSpacing(20)

        self.card_info = QFrame()
        self.card_info.setObjectName("Card")
        self.apply_shadow(self.card_info)
        card_info_layout = QVBoxLayout(self.card_info)
        card_info_layout.setContentsMargins(20, 20, 20, 20)
        
        theme_layout = QHBoxLayout()
        self.btn_theme = QPushButton("切换浅色 ☀️")
        self.btn_theme.setObjectName("ThemeBtn")
        self.btn_theme.clicked.connect(self.toggle_theme)
        theme_layout.addStretch()
        theme_layout.addWidget(self.btn_theme)
        card_info_layout.addLayout(theme_layout)

        self.label_info = QLabel("未选择文件")
        self.label_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.label_info.setStyleSheet("font-size: 15px; line-height: 1.8;")
        self.label_info.setWordWrap(True)
        card_info_layout.addWidget(self.label_info)
        
        right_layout.addWidget(self.card_info, stretch=8)

        # 右下角操作区
        action_layout = QHBoxLayout()
        action_layout.setSpacing(15)
        
        self.btn_start = QPushButton("开始转换")
        self.btn_start.setObjectName("StartBtn")
        self.btn_start.clicked.connect(self.start_conversion)
        
        self.btn_arrow = QPushButton("▼")
        self.btn_arrow.setObjectName("ArrowBtn")
        self.btn_arrow.setFixedSize(44, 44)
        # 这里后面接弹出设置菜单
        
        action_layout.addWidget(self.btn_start, stretch=3)
        action_layout.addWidget(self.btn_arrow, stretch=1)
        right_layout.addLayout(action_layout, stretch=1)

        main_layout.addLayout(left_layout, stretch=7)
        main_layout.addLayout(right_layout, stretch=3)

        self.setAcceptDrops(True)

    # ================= 核心逻辑区 =================

    def apply_shadow(self, widget):
        shadow = QGraphicsDropShadowEffect(self)
        # 苹果风关键：模糊大一点(30)，偏移小一点(0, 8)，透明度低一点(60)
        shadow.setColor(QColor(0, 0, 0, 60)) 
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        widget.setGraphicsEffect(shadow)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.accept()
        else: event.ignore()

    def dropEvent(self, event):
        has_new_ncm = False
        has_new_lrc = False
        
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            
            if file_path.lower().endswith(".ncm"):
                # 如果已经存在就不重复加了
                if file_path not in self.ncm_files:
                    self.ncm_files.append(file_path)
                    item = QListWidgetItem(QIcon(":/qt-project.org/styles/commonstyle/images/standardbutton-open-32.png"), base_name)
                    item.setData(Qt.UserRole, file_path) 
                    self.file_list.addItem(item)
                    has_new_ncm = True
                    
            elif file_path.lower().endswith(".lrc"):
                # 存入歌词库
                self.lrc_library[base_name] = file_path
                has_new_lrc = True
        
        if has_new_ncm or has_new_lrc:
            self.label_drop.hide()
            self.file_list.show()
            self.label_status.setText(f"已加载 {len(self.ncm_files)} 首歌，{len(self.lrc_library)} 份歌词")

    def on_selection_changed(self):
        selected_items = self.file_list.selectedItems()
        if len(selected_items) == 1:
            base_name = selected_items[0].text()
            has_lrc = "✅ 已匹配" if base_name in self.lrc_library else "❌ 未找到同名 LRC"
            self.label_info.setText(f"<b>🎵 {base_name}</b><br><br>歌词状态：{has_lrc}<br>转换状态：等待中")
        elif len(selected_items) > 1:
            self.label_info.setText(f"<b>📊 批量操作</b><br><br>已选中 {len(selected_items)} 个文件。")
        else:
            self.label_info.setText("未选择文件")

    # ================= 转换逻辑 =================

    def start_conversion(self):
        if not self.ncm_files:
            self.label_status.setText("列表为空，请先拖入 .ncm 文件！")
            return
            
        self.current_index = 0
        self.btn_start.setEnabled(False)
        self.btn_start.setText("转换中...")
        self.btn_view.setEnabled(False)
        self.btn_add_lrc.setEnabled(False)
        
        self.process_next_file()

    def process_next_file(self):
        # 全部转完了
        if self.current_index >= len(self.ncm_files):
            self.label_status.setText(f"✅ 全部转换完成！共 {len(self.ncm_files)} 首")
            self.btn_start.setEnabled(True)
            self.btn_start.setText("开始转换")
            self.btn_view.setEnabled(True)
            self.btn_add_lrc.setEnabled(True)
            return

        # 取当前文件
        file_path = self.ncm_files[self.current_index]
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        
        self.label_status.setText(f"正在转换 ({self.current_index + 1}/{len(self.ncm_files)}): {base_name}")
        
        # 准备 exe 路径
        exe_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ncmdump.exe")
        
        # 组装参数
        args = [file_path]
        if self.output_dir:
            args.extend(["-o", self.output_dir])
            
        self.process = QProcess()
        self.process.finished.connect(self.on_process_finished)
        self.process.start(exe_path, args)

    def on_process_finished(self):
        # 1. 处理当前音频对应的 LRC 复制工作
        file_path = self.ncm_files[self.current_index]
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        
        # 判断是否有同名歌词
        if base_name in self.lrc_library:
            lrc_src = self.lrc_library[base_name]
            # 确定输出目录（如果没指定，就输出在原目录）
            out_dir = self.output_dir if self.output_dir else os.path.dirname(file_path)
            lrc_dst = os.path.join(out_dir, os.path.basename(lrc_src))
            
            try:
                # 复制歌词过去
                os.makedirs(out_dir, exist_ok=True)
                shutil.copy2(lrc_src, lrc_dst)
            except Exception as e:
                print(f"复制歌词失败: {e}")

        # 2. 进度 +1，继续下一个
        self.current_index += 1
        self.process_next_file()

    # ================= 界面切换逻辑 =================

    def toggle_theme(self):
        if self.is_dark_mode:
            self.setStyleSheet(LIGHT_QSS)
            self.btn_theme.setText("切换深色 🌙")
            self.is_dark_mode = False
        else:
            self.setStyleSheet(DARK_QSS)
            self.btn_theme.setText("切换浅色 ☀️")
            self.is_dark_mode = True

    def toggle_view_mode(self):
        if self.file_list.viewMode() == QListView.IconMode:
            self.file_list.setViewMode(QListView.ListMode)
            self.file_list.setGridSize(QSize())
            self.file_list.setIconSize(QSize(30, 30))
            self.btn_view.setText("切换为网格视图")
        else:
            self.file_list.setViewMode(QListView.IconMode)
            self.file_list.setGridSize(QSize(110, 120))
            self.file_list.setIconSize(QSize(80, 80))
            self.btn_view.setText("切换为列表视图")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())