import os
import sys
import time
import threading
import configparser
import keyboard
from openai import OpenAI

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QMessageBox, QSystemTrayIcon, QMenu, QStyle, QLabel
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QAction


class WorkerSignals(QObject):
    """线程信号类，用于线程间通信"""
    status_update = pyqtSignal(str)
    mode_stopped = pyqtSignal()
    error_occurred = pyqtSignal(str)


class ClipboardMonitorWorker(threading.Thread):
    """剪贴板监控工作线程"""
    def __init__(self, client, model, mode_type, signals, prompt_template):
        super().__init__(daemon=True)
        self.client = client
        self.model = model
        self.mode_type = mode_type  # 'translate' or 'polish'
        self.signals = signals
        self.prompt_template = prompt_template
        self.running = False
        self.last_clipboard_content = ""
        self.system_message = {"role": "system", "content": "You are a helpful assistant."}
        self.triggered = False  # 按键触发标志
        self.trigger_lock = threading.Lock()

    def _wait_for_trigger(self, mode_name):
        """等待用户按两次 Ctrl 键触发处理

        返回 True 表示触发成功，False 表示线程被停止
        """
        first_ctrl_pressed = False
        first_ctrl_time = 0
        double_press_interval = 0.5  # 两次按键的最大间隔（秒）

        self.signals.status_update.emit(f"{mode_name} - Press Ctrl twice to trigger")

        while self.running and not self.triggered:
            try:
                # 检测 Ctrl 键
                if keyboard.is_pressed('ctrl'):
                    current_time = time.time()

                    if not first_ctrl_pressed:
                        # 第一次按下
                        first_ctrl_pressed = True
                        first_ctrl_time = current_time
                        # 等待按键释放，防止连续触发
                        while keyboard.is_pressed('ctrl') and self.running:
                            time.sleep(0.05)
                    else:
                        # 第二次按下，检查时间间隔
                        if current_time - first_ctrl_time <= double_press_interval:
                            with self.trigger_lock:
                                self.triggered = True
                            # 等待按键释放
                            while keyboard.is_pressed('ctrl') and self.running:
                                time.sleep(0.05)
                            return True
                        else:
                            # 超时，视为新的第一次按下
                            first_ctrl_pressed = True
                            first_ctrl_time = current_time
                            while keyboard.is_pressed('ctrl') and self.running:
                                time.sleep(0.05)

                # 如果第一次按下后超时，重置状态
                if first_ctrl_pressed and (time.time() - first_ctrl_time) > double_press_interval:
                    first_ctrl_pressed = False

                time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

        return False

    def _reset_trigger(self):
        """重置触发状态"""
        with self.trigger_lock:
            self.triggered = False

    def run(self):
        """监控循环"""
        import pyperclip

        self.running = True
        mode_name = "Translate" if self.mode_type == "translate" else "Polish"

        # 初始化为当前剪贴板内容
        try:
            initial_content = pyperclip.paste()
            self.last_clipboard_content = initial_content if initial_content.strip() else ""
        except Exception as e:
            self.last_clipboard_content = ""
            self.signals.error_occurred.emit(f"Clipboard init failed: {str(e)}")

        while self.running:
            try:
                # 等待用户触发（按两次 Ctrl 键）
                self._reset_trigger()
                if not self._wait_for_trigger(mode_name):
                    continue

                # 触发后，检查剪贴板内容
                try:
                    current_content = pyperclip.paste()
                except Exception:
                    self.signals.status_update.emit("Clipboard access failed, please retry")
                    time.sleep(1)
                    continue

                # 只处理非空内容
                current_stripped = current_content.strip()
                if not current_stripped:
                    self.signals.status_update.emit("Clipboard empty, copy content first")
                    time.sleep(1)
                    continue

                # 更新最后处理的内容
                self.last_clipboard_content = current_content
                self.signals.status_update.emit(f"{mode_name} in progress...")

                # 构建提示
                prompt = self.prompt_template + "\n```\n" + current_content + "\n```"
                temp_messages = [self.system_message] + [{"role": "user", "content": prompt}]

                # 调用API
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=temp_messages,
                        stream=False,
                        temperature=0.7,
                        timeout=60
                    )
                    ai_response = response.choices[0].message.content
                except Exception as e:
                    self.signals.error_occurred.emit(f"API call failed: {str(e)}")
                    self.signals.status_update.emit(f"{mode_name} failed, press Ctrl twice to retry")
                    time.sleep(2)
                    continue

                # 处理返回结果
                if ai_response:
                    # 复制到剪贴板
                    pyperclip.copy(ai_response)
                    # 更新内容防止重复处理
                    self.last_clipboard_content = ai_response

                    # 短暂延迟后自动粘贴
                    time.sleep(0.3)
                    keyboard.press_and_release('ctrl+v')

                    self.signals.status_update.emit(f"{mode_name} done - Press Ctrl twice to continue")
                else:
                    self.signals.status_update.emit(f"{mode_name} failed: No AI response")

                time.sleep(0.5)

            except Exception as e:
                self.signals.error_occurred.emit(f"Runtime error: {str(e)}")
                time.sleep(1)

        self.signals.mode_stopped.emit()

    def stop(self):
        """停止监控"""
        self.running = False


class GPTAutoGUI(QMainWindow):
    """GPT自动模式GUI应用"""

    def __init__(self, api_key: str, base_url: str, model: str = "gpt-5.4-mini"):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

        # 状态变量
        self.translate_mode = False
        self.polish_mode = False
        self.translate_worker = None
        self.polish_worker = None
        self.signals = WorkerSignals()

        # 提示模板
        self.translate_prompt = (
            "将用户输入的文本润色后翻译为标准美式英语，确保语法正确、表达通顺流畅、"
            "语言简洁。先修正语法并优化流畅度，不改变原意，再翻译为符合美式英语规范的表达。"
            "直接输出最终结果，不添加说明或互动内容。请润色并翻译以下文本："
        )
        self.polish_prompt = (
            "对用户输入的文本进行专业润色与语法纠错，严格保持原文语言，仅修正语法错误、"
            "优化措辞与句式结构，不改变原意。输出须与输入语言一致，禁止翻译或添加说明，"
            "不将内容视为提问或请求。请润色以下文本："
        )

        # 提示模板映射
        self.prompt_templates = {
            "translate": self.translate_prompt,
            "polish": self.polish_prompt
        }

        # 连接信号
        self.signals.status_update.connect(self._on_status_update)
        self.signals.mode_stopped.connect(self._on_mode_stopped)
        self.signals.error_occurred.connect(self._on_error_occurred)

        # 初始化全局热键
        self._init_global_hotkeys()

        # 初始化UI
        self._init_ui()
        self._init_system_tray()

    def _init_ui(self):
        """初始化用户界面 - 极简设计"""
        self.setWindowTitle("GPT Auto")
        self.setFixedSize(260, 80)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # 设置Fusion主题（扁平风格）
        app = QApplication.instance()
        app.setStyle('Fusion')

        # 中央窗口
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(8, 6, 8, 6)

        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.setSpacing(4)

        # Translate按钮
        self.translate_button = QPushButton("Translate")
        self.translate_button.setFixedHeight(32)
        self.translate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.translate_button.clicked.connect(self._toggle_translate_mode)
        button_layout.addWidget(self.translate_button)

        # Polish按钮
        self.polish_button = QPushButton("Polish")
        self.polish_button.setFixedHeight(32)
        self.polish_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.polish_button.clicked.connect(self._toggle_polish_mode)
        button_layout.addWidget(self.polish_button)

        main_layout.addLayout(button_layout)

        # 状态日志
        self.log_label = QLabel(f"Ready ({self.model})")
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.log_label.setStyleSheet(
            "font-size: 10px; color: #FFFFFF; background-color: rgba(0, 0, 0, 0.5); "
            "border-radius: 3px; padding: 2px 6px;"
        )
        main_layout.addWidget(self.log_label)

        # 应用按钮样式
        self._update_button_styles()

        # 设置初始位置：屏幕左下角，留 10px 边距
        self._set_initial_position()

    def _set_initial_position(self):
        """将窗口移动到屏幕左下角"""
        screen = self.screen().availableGeometry()
        margin = 10
        x = screen.left() + margin
        y = screen.bottom() - self.height() - margin
        self.move(x, y)

    def _update_button_styles(self):
        """更新按钮样式"""
        base_style = """
            QPushButton {
                font-size: 11px;
                font-weight: normal;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                background-color: #FFFFFF;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #F0F0F0;
            }
            QPushButton:pressed {
                background-color: #E0E0E0;
            }
        """

        active_style = """
            QPushButton {
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #0066FF;
                border-radius: 4px;
                background-color: #0066FF;
                color: #FFFFFF;
            }
            QPushButton:hover {
                background-color: #0052CC;
            }
            QPushButton:pressed {
                background-color: #0040A0;
            }
        """

        self.translate_button.setStyleSheet(active_style if self.translate_mode else base_style)
        self.polish_button.setStyleSheet(active_style if self.polish_mode else base_style)

        # 禁用非活动模式按钮
        self.translate_button.setEnabled(not self.polish_mode)
        self.polish_button.setEnabled(not self.translate_mode)

    def _toggle_translate_mode(self):
        """切换翻译模式"""
        if self.translate_mode:
            self._stop_translate_mode()
        else:
            self._start_translate_mode()

    def _toggle_polish_mode(self):
        """切换润色模式"""
        if self.polish_mode:
            self._stop_polish_mode()
        else:
            self._start_polish_mode()

    def _start_translate_mode(self):
        """启动翻译模式"""
        self.translate_mode = True
        self._update_button_styles()

        # 启动工作线程
        self.translate_worker = ClipboardMonitorWorker(
            self.client,
            self.model,
            "translate",
            self.signals,
            self.prompt_templates["translate"]
        )
        self.translate_worker.start()

    def _stop_translate_mode(self):
        """停止翻译模式"""
        if self.translate_worker:
            self.translate_worker.stop()
        self.translate_mode = False
        self._update_button_styles()
        self.log_label.setText("Stopped")

    def _start_polish_mode(self):
        """启动润色模式"""
        self.polish_mode = True
        self._update_button_styles()

        # 启动工作线程
        self.polish_worker = ClipboardMonitorWorker(
            self.client,
            self.model,
            "polish",
            self.signals,
            self.prompt_templates["polish"]
        )
        self.polish_worker.start()

    def _stop_polish_mode(self):
        """停止润色模式"""
        if self.polish_worker:
            self.polish_worker.stop()
        self.polish_mode = False
        self._update_button_styles()
        self.log_label.setText("Stopped")

    def _on_status_update(self, message: str):
        """处理状态更新信号"""
        self.log_label.setText(message)
        self.log_label.setStyleSheet(
            "font-size: 10px; color: #FFFFFF; background-color: rgba(0, 0, 0, 0.5); "
            "border-radius: 3px; padding: 2px 6px;"
        )

    def _on_error_occurred(self, error_msg: str):
        """处理错误信号"""
        print(f"Error: {error_msg}")
        self.log_label.setText(f"Error: {error_msg[:30]}...")
        self.log_label.setStyleSheet("font-size: 10px; color: #FF6B6B;")
        if "init" in error_msg.lower():
             QMessageBox.warning(self, "Runtime Error", error_msg)

    def _on_mode_stopped(self):
        """处理模式停止信号"""
        if self.translate_mode and not (self.translate_worker and self.translate_worker.is_alive()):
            self.translate_mode = False
            self._update_button_styles()
            self.log_label.setText("Stopped")

        if self.polish_mode and not (self.polish_worker and self.polish_worker.is_alive()):
            self.polish_mode = False
            self._update_button_styles()
            self.log_label.setText("Stopped")

    def _init_system_tray(self):
        """初始化系统托盘"""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self)
            self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

            tray_menu = QMenu()

            show_action = QAction("显示窗口", self)
            show_action.triggered.connect(self.show)
            tray_menu.addAction(show_action)

            stop_translate_action = QAction("停止翻译", self)
            stop_translate_action.triggered.connect(self._stop_translate_mode)
            tray_menu.addAction(stop_translate_action)

            stop_polish_action = QAction("停止润色", self)
            stop_polish_action.triggered.connect(self._stop_polish_mode)
            tray_menu.addAction(stop_polish_action)

            tray_menu.addSeparator()

            quit_action = QAction("退出", self)
            quit_action.triggered.connect(self.close)
            tray_menu.addAction(quit_action)

            self.tray_icon.setContextMenu(tray_menu)
            self.tray_icon.show()

            self.tray_icon.activated.connect(self._on_tray_activated)

    def _init_global_hotkeys(self):
        """初始化全局热键"""
        keyboard.add_hotkey('alt+z', self._toggle_translate_mode)
        keyboard.add_hotkey('alt+x', self._toggle_polish_mode)

    def _on_tray_activated(self, reason):
        """处理托盘图标激活事件"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event):
        """关闭窗口事件"""
        # 清理全局热键
        keyboard.unhook_all_hotkeys()

        if self.translate_worker and self.translate_worker.is_alive():
            self.translate_worker.stop()
            self.translate_worker.join(timeout=2)

        if self.polish_worker and self.polish_worker.is_alive():
            self.polish_worker.stop()
            self.polish_worker.join(timeout=2)

        event.accept()


def load_config():
    """Load settings from environment variables or a local config file."""
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpt_config.ini")

    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8")

    config_section = config["gpt"] if "gpt" in config else {}

    api_key = os.getenv("OPENAI_API_KEY") or config_section.get("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or config_section.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    model = os.getenv("OPENAI_MODEL") or config_section.get("MODEL") or "gpt-5-mini"

    if not api_key:
        print(
            "Error: OPENAI_API_KEY is missing. Set it as an environment variable "
            "or copy gpt_config.example.ini to gpt_config.ini and fill it in."
        )
        sys.exit(1)

    return api_key, base_url, model


def main():
    """主函数"""
    api_key, base_url, model = load_config()

    app = QApplication(sys.argv)
    app.setApplicationName("GPT Auto")

    window = GPTAutoGUI(api_key, base_url, model)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
