import ctypes
import io
import os
import sys
import json
import time
import threading
import queue
import logging
import traceback


def configure_dpi_awareness():
    """必须在创建任何 Tk/Win32 窗口之前设置 DPI 模式。"""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "Per-Monitor V2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "Per-Monitor"
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "System"
    except Exception:
        pass
    return "Unknown"


DPI_MODE = configure_dpi_awareness()

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from PIL import Image, ImageGrab, ImageTk
import cv2
import numpy as np
import win32api
import win32gui
import win32con

# 导入我们的后台引擎
import engine
import vision
from display_dimmer import DisplayDimmer
from preview_dialog import PreviewDialog
from profile_store import ProfileStore, ProfileStoreError
from generic_script_ui import GenericScriptUI

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
LOG_FILE = os.path.join(BASE_DIR, "automation.log")
DIAGNOSTICS_DIR = os.path.join(BASE_DIR, "diagnostics")
NIGHT_SCREEN_FILE = os.path.join(BASE_DIR, "night_screen.json")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
LOGGER = logging.getLogger("roblox_ae_automation")

class AEAutomationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Roblox 动漫远征 (AE) 关卡自动刷图配置器")
        self.root.geometry("1280x820")
        self.root.minsize(980, 680)
        self.root.resizable(True, True)
        self.root.report_callback_exception = self.report_callback_exception
        
        # 属性配置
        self.hwnd = None
        self.window_lookup = {}
        self.steps = []
        self.running = False
        self.loop_thread = None
        self.log_queue = queue.Queue()
        self.steps_snapshot = []
        self.recognition_poll_number = 0
        self.diagnostic_saved_count = 0
        self.last_diagnostic_save_at = 0.0
        self.last_cursor_inside = None
        self.last_resource_check_at = 0.0
        self.last_resource_log_at = 0.0
        self.resource_warning_emitted = False
        self.display_dimmer = DisplayDimmer()
        self.night_level_var = tk.IntVar(value=0)
        self.night_auto_var = tk.BooleanVar(value=False)
        self.night_dimmed_by_farming = False
        self.emergency_hotkey_was_down = False
        self.selection_in_progress = False
        self.active_selection_overlay = None
        self.main_window_restore_rect = None
        self.main_window_restore_state = "normal"
        self.profile_store = None
        self.active_profile_id = None
        self.profile_lookup = {}
        self.profile_dirty = False
        self.loading_profile = False
        self.preview_capture_in_progress = False
        self.workspace_layout_job = None
        
        # 创建模板文件夹
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        os.makedirs(DIAGNOSTICS_DIR, exist_ok=True)
        
        # 现代暗黑皮肤主题样式
        self.setup_styles()
        
        # 构建 UI 布局
        self.create_widgets()
        
        # 载入配置
        self.load_config()
        self.load_night_screen_config()
        self.log(f"DPI 模式: {DPI_MODE}；日志文件: {LOG_FILE}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # 启动 GUI 定时器更新日志
        self.root.after(100, self.process_logs)
        self.root.after(100, self.poll_emergency_brightness_hotkey)
        # 启动定时器刷新窗口列表
        self.refresh_windows()
        # 保持 1280×820 的默认窗口尺寸，并初始化可拖动分隔栏位置。
        self.root.after(120, self.set_initial_pane_positions)

    def report_callback_exception(self, exception_type, exception, traceback_object):
        """pythonw 没有控制台；Tk 回调异常必须写入日志并在 GUI 中提示。"""
        formatted = "".join(
            traceback.format_exception(
                exception_type,
                exception,
                traceback_object,
            )
        )
        LOGGER.error("Tk 回调异常:\n%s", formatted)
        messagebox.showerror(
            "程序发生异常",
            f"{exception}\n\n详细信息已写入:\n{LOG_FILE}",
        )
        
    def setup_styles(self):
        # 柔和夜间控制台：避免纯黑、纯白与高饱和大色块造成视觉疲劳。
        self.bg_dark = "#121722"
        self.bg_card = "#1a2130"
        self.bg_surface = "#20293a"
        self.bg_input = "#151c29"
        self.border_soft = "#303b50"
        self.fg_white = "#e8edf5"
        self.fg_gray = "#96a3b7"
        self.fg_dim = "#6f7d92"
        self.btn_primary = "#7277d9"
        self.btn_primary_hover = "#8287e6"
        self.btn_danger = "#c96573"
        self.btn_danger_hover = "#d67682"
        self.btn_success = "#43a982"
        self.btn_success_hover = "#52b991"
        self.btn_neutral = "#334056"
        self.btn_neutral_hover = "#414f67"
        self.font_ui = ("Microsoft YaHei UI", 10)
        self.font_small = ("Microsoft YaHei UI", 9)
        self.font_title = ("Microsoft YaHei UI", 16, "bold")
        
        self.root.configure(bg=self.bg_dark)
        
        style = ttk.Style()
        style.theme_use("clam")
        
        style.configure("TFrame", background=self.bg_dark)
        style.configure("Card.TFrame", background=self.bg_card)
        style.configure(
            "TLabel",
            background=self.bg_dark,
            foreground=self.fg_white,
            font=self.font_ui,
        )
        style.configure(
            "Card.TLabel",
            background=self.bg_card,
            foreground=self.fg_white,
            font=self.font_ui,
        )
        style.configure(
            "Surface.TLabel",
            background=self.bg_surface,
            foreground=self.fg_white,
            font=self.font_ui,
        )
        style.configure(
            "Title.TLabel",
            background=self.bg_dark,
            foreground=self.fg_white,
            font=self.font_title,
        )
        style.configure(
            "Muted.TLabel",
            background=self.bg_dark,
            foreground=self.fg_gray,
            font=self.font_small,
        )
        style.configure(
            "CardMuted.TLabel",
            background=self.bg_card,
            foreground=self.fg_gray,
            font=self.font_small,
        )
        style.configure(
            "Soft.TCombobox",
            fieldbackground=self.bg_input,
            background=self.bg_surface,
            foreground=self.fg_white,
            arrowcolor=self.fg_gray,
            bordercolor=self.border_soft,
            lightcolor=self.border_soft,
            darkcolor=self.border_soft,
            padding=6,
        )
        style.map(
            "Soft.TCombobox",
            fieldbackground=[("readonly", self.bg_input)],
            foreground=[("readonly", self.fg_white)],
            selectbackground=[("readonly", self.bg_input)],
            selectforeground=[("readonly", self.fg_white)],
        )
        style.configure(
            "Soft.TEntry",
            fieldbackground=self.bg_input,
            foreground=self.fg_white,
            insertcolor=self.fg_white,
            bordercolor=self.border_soft,
            lightcolor=self.border_soft,
            darkcolor=self.border_soft,
            padding=6,
        )
        style.configure(
            "Soft.Vertical.TScrollbar",
            background=self.btn_neutral,
            troughcolor=self.bg_card,
            bordercolor=self.bg_card,
            arrowcolor=self.fg_gray,
        )
        style.configure(
            "Soft.Horizontal.TScale",
            background=self.bg_card,
            troughcolor=self.bg_input,
        )
        style.configure(
            "Card.TCheckbutton",
            background=self.bg_card,
            foreground=self.fg_gray,
            font=self.font_small,
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", self.bg_card)],
            foreground=[("active", self.fg_white)],
        )

    def set_initial_pane_positions(self):
        try:
            self.content_pane.sash_place(0, 350, 0)
            available_height = self.body_pane.winfo_height()
            self.body_pane.sash_place(0, 0, max(430, available_height - 190))
            self.schedule_workspace_layout_guard()
        except (tk.TclError, AttributeError):
            pass

    def schedule_workspace_layout_guard(self, event=None):
        """分隔栏或窗口尺寸变化后，保证固定控制区不会被挤成残影。"""
        if self.workspace_layout_job is not None:
            try:
                self.root.after_cancel(self.workspace_layout_job)
            except tk.TclError:
                pass
        self.workspace_layout_job = self.root.after(
            60,
            self.ensure_workspace_layout,
        )

    def ensure_workspace_layout(self):
        self.workspace_layout_job = None
        try:
            self.root.update_idletasks()
            fixed_panels = (self.profile_panel, self.night_panel)
            deficit = max(
                (
                    panel.winfo_reqheight() - panel.winfo_height()
                    for panel in fixed_panels
                ),
                default=0,
            )
            if deficit > 0:
                _sash_x, sash_y = self.body_pane.sash_coord(0)
                body_height = self.body_pane.winfo_height()
                # 日志仍至少保留标题和一行内容；优先保证上方操作控件完整。
                maximum_sash_y = max(0, body_height - 120)
                corrected_y = min(maximum_sash_y, sash_y + deficit + 8)
                if corrected_y > sash_y:
                    self.body_pane.sash_place(0, 0, corrected_y)
                    self.root.update_idletasks()

            # 部分 Windows/Tk DPI 组合在拖动 PanedWindow 后只重绘容器，
            # 子控件会暂时留下空色块；显式让整个窗口及子窗口失效重绘。
            redraw_flags = 0x0001 | 0x0004 | 0x0080 | 0x0100
            ctypes.windll.user32.RedrawWindow(
                self.get_main_window_hwnd(),
                None,
                None,
                redraw_flags,
            )
        except (tk.TclError, AttributeError):
            pass

    def make_card(self, parent):
        return tk.Frame(
            parent,
            bg=self.bg_card,
            highlightbackground=self.border_soft,
            highlightcolor=self.border_soft,
            highlightthickness=1,
            bd=0,
        )

    def make_button(self, parent, text, command, variant="primary", **kwargs):
        palette = {
            "primary": (self.btn_primary, self.btn_primary_hover),
            "success": (self.btn_success, self.btn_success_hover),
            "danger": (self.btn_danger, self.btn_danger_hover),
            "neutral": (self.btn_neutral, self.btn_neutral_hover),
        }
        normal_color, hover_color = palette[variant]
        options = {
            "text": text,
            "command": command,
            "bg": normal_color,
            "fg": self.fg_white,
            "activebackground": hover_color,
            "activeforeground": self.fg_white,
            "disabledforeground": self.fg_dim,
            "relief": "flat",
            "bd": 0,
            "cursor": "hand2",
            "font": self.font_small,
            "padx": 12,
            "pady": 7,
            "highlightthickness": 0,
        }
        options.update(kwargs)
        button = tk.Button(parent, **options)
        button.bind(
            "<Enter>",
            lambda event: (
                button.config(bg=hover_color)
                if str(button.cget("state")) != "disabled"
                else None
            ),
        )
        button.bind(
            "<Leave>",
            lambda event: (
                button.config(bg=normal_color)
                if str(button.cget("state")) != "disabled"
                else None
            ),
        )
        return button
        
    def create_widgets(self):
        # 顶部模式切换：AE 部署 / 通用脚本
        self.mode_notebook = ttk.Notebook(self.root)
        self.mode_notebook.pack(fill="both", expand=True)

        self.ae_tab = ttk.Frame(self.mode_notebook)
        self.mode_notebook.add(self.ae_tab, text="  AE 自动部署  ")

        header = ttk.Frame(self.ae_tab)
        header.pack(fill="x", padx=22, pady=(18, 12))

        title_stack = ttk.Frame(header)
        title_stack.pack(side="left")
        ttk.Label(
            title_stack,
            text="AE 自动部署控制台",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_stack,
            text="锁定 Roblox 窗口、编排放置动作，并监测下一局开始",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        lbl_status = tk.Label(
            header,
            text="●  状态：空闲",
            bg=self.bg_surface,
            fg="#e6b566",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=16,
            pady=8,
            relief="flat",
        )
        lbl_status.pack(side="right")
        self.lbl_status = lbl_status

        # 上方工作区与下方日志均可拖动分隔线调整高度。
        self.body_pane = tk.PanedWindow(
            self.ae_tab,
            orient=tk.VERTICAL,
            bg=self.bg_dark,
            bd=0,
            sashwidth=7,
            sashrelief="flat",
            showhandle=False,
        )
        self.body_pane.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        self.body_pane.bind(
            "<ButtonRelease-1>",
            self.schedule_workspace_layout_guard,
            add="+",
        )
        self.body_pane.bind(
            "<Configure>",
            self.schedule_workspace_layout_guard,
            add="+",
        )

        workspace = tk.Frame(self.body_pane, bg=self.bg_dark)
        self.content_pane = tk.PanedWindow(
            workspace,
            orient=tk.HORIZONTAL,
            bg=self.bg_dark,
            bd=0,
            sashwidth=8,
            sashrelief="flat",
            showhandle=False,
        )
        self.content_pane.pack(fill="both", expand=True)

        left_frame = tk.Frame(self.content_pane, bg=self.bg_dark)
        right_frame = tk.Frame(self.content_pane, bg=self.bg_dark)
        self.content_pane.add(left_frame, minsize=300, width=350, stretch="never")
        self.content_pane.add(right_frame, minsize=560, stretch="always")

        # 目标窗口
        win_card = self.make_card(left_frame)
        win_card.pack(fill="x", padx=(0, 10), pady=(0, 10))
        ttk.Label(
            win_card,
            text="目标窗口",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(
            win_card,
            text="仅显示真实 RobloxPlayerBeta 进程",
            style="CardMuted.TLabel",
        ).pack(anchor="w", padx=16, pady=(0, 10))

        self.cb_windows = ttk.Combobox(
            win_card,
            postcommand=self.refresh_windows,
            state="readonly",
            style="Soft.TCombobox",
        )
        self.cb_windows.pack(fill="x", padx=16, pady=(0, 16))
        self.cb_windows.bind("<<ComboboxSelected>>", self.on_window_select)

        # 识别模板
        temp_card = self.make_card(left_frame)
        temp_card.pack(fill="x", padx=(0, 10), pady=(0, 10))
        ttk.Label(
            temp_card,
            text="开始状态识别",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(
            temp_card,
            text="模板匹配失败时会自动启用绿色按钮识别",
            style="CardMuted.TLabel",
        ).pack(anchor="w", padx=16, pady=(0, 10))
        btn_crop_start = self.make_button(
            temp_card,
            "重新截取开始按钮",
            lambda: self.start_crop_template("start_btn.png"),
            "primary",
        )
        btn_crop_start.pack(fill="x", padx=16, pady=(0, 10))
        self.lbl_has_start = ttk.Label(
            temp_card,
            text="开始按钮模板：检测中…",
            style="CardMuted.TLabel",
            foreground=self.fg_gray,
        )
        self.lbl_has_start.pack(anchor="w", padx=16, pady=(0, 14))

        # 运行控制
        control_card = self.make_card(left_frame)
        control_card.pack(fill="both", expand=True, padx=(0, 10))
        ttk.Label(
            control_card,
            text="运行控制",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(
            control_card,
            text="执行期间会短暂接管焦点和鼠标",
            style="CardMuted.TLabel",
        ).pack(anchor="w", padx=16, pady=(0, 12))
        run_buttons = tk.Frame(control_card, bg=self.bg_card)
        run_buttons.pack(fill="x", padx=16, pady=(0, 16))
        self.btn_start = self.make_button(
            run_buttons,
            "开始挂机",
            self.start_farming,
            "success",
            font=("Microsoft YaHei UI", 11, "bold"),
            pady=10,
        )
        self.btn_stop = self.make_button(
            run_buttons,
            "停止挂机",
            self.stop_farming,
            "danger",
            state="disabled",
            font=("Microsoft YaHei UI", 11, "bold"),
            pady=10,
        )
        self.btn_start.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 4),
        )
        self.btn_stop.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(4, 0),
        )

        # 部署动作卡片
        steps_card = self.make_card(right_frame)
        steps_card.pack(fill="both", expand=True, padx=(2, 0))
        steps_header = tk.Frame(steps_card, bg=self.bg_card)
        steps_header.pack(fill="x", padx=16, pady=(14, 10))
        title_group = tk.Frame(steps_header, bg=self.bg_card)
        title_group.pack(side="left")
        ttk.Label(
            title_group,
            text="部署动作",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text="按顺序选择单位、落点和动作间隔",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        btn_add_step = self.make_button(
            steps_header,
            "＋ 添加步骤",
            self.add_step,
            "primary",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        btn_add_step.pack(side="right", anchor="n")
        self.btn_test_all = self.make_button(
            steps_header,
            "测试全部",
            self.test_all_steps,
            "neutral",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.btn_test_all.pack(side="right", anchor="n", padx=(0, 8))

        # 部署方案库：主行负责选择与保存，次行放低频管理及预览操作。
        profile_panel = tk.Frame(
            steps_card,
            bg=self.bg_surface,
            highlightbackground=self.border_soft,
            highlightthickness=1,
        )
        self.profile_panel = profile_panel
        profile_panel.pack(fill="x", padx=12, pady=(0, 10))
        profile_primary = tk.Frame(profile_panel, bg=self.bg_surface)
        profile_primary.pack(fill="x", padx=12, pady=(9, 4))
        ttk.Label(
            profile_primary,
            text="部署方案",
            style="Surface.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", padx=(0, 10))
        self.cb_profiles = ttk.Combobox(
            profile_primary,
            state="readonly",
            style="Soft.TCombobox",
            width=24,
        )
        self.cb_profiles.pack(side="left", fill="x", expand=True)
        self.cb_profiles.bind("<<ComboboxSelected>>", self.on_profile_selected)
        self.lbl_profile_state = tk.Label(
            profile_primary,
            text="● 载入中",
            bg=self.bg_surface,
            fg=self.fg_gray,
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
        )
        self.lbl_profile_state.pack(side="left")
        self.btn_profile_save = self.make_button(
            profile_primary,
            "保存",
            self.save_active_profile,
            "primary",
            pady=5,
        )
        self.btn_profile_save.pack(side="left", padx=(0, 6))
        self.make_button(
            profile_primary,
            "另存为",
            self.save_profile_as,
            "neutral",
            pady=5,
        ).pack(side="left")

        profile_secondary = tk.Frame(profile_panel, bg=self.bg_surface)
        profile_secondary.pack(fill="x", padx=12, pady=(0, 8))
        self.make_button(
            profile_secondary,
            "新建",
            self.create_new_profile,
            "neutral",
            pady=4,
        ).pack(side="left", padx=(0, 5))
        self.make_button(
            profile_secondary,
            "重命名",
            self.rename_active_profile,
            "neutral",
            pady=4,
        ).pack(side="left", padx=(0, 5))
        self.make_button(
            profile_secondary,
            "删除",
            self.delete_active_profile,
            "danger",
            pady=4,
        ).pack(side="left")
        self.btn_capture_preview = self.make_button(
            profile_secondary,
            "截取预览",
            self.capture_profile_preview,
            "neutral",
            pady=4,
        )
        self.btn_capture_preview.pack(side="right")
        self.btn_view_preview = self.make_button(
            profile_secondary,
            "查看预览",
            self.view_profile_preview,
            "neutral",
            pady=4,
            state="disabled",
        )
        self.btn_view_preview.pack(side="right", padx=(0, 5))
        self.lbl_preview_state = ttk.Label(
            profile_secondary,
            text="尚无预览",
            style="Surface.TLabel",
            foreground=self.fg_dim,
        )
        self.lbl_preview_state.pack(side="right", padx=(0, 10))

        night_panel = tk.Frame(
            steps_card,
            bg=self.bg_surface,
            highlightbackground=self.border_soft,
            highlightthickness=1,
        )
        self.night_panel = night_panel
        night_panel.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        night_controls = tk.Frame(night_panel, bg=self.bg_surface)
        night_controls.pack(fill="x", padx=12, pady=(9, 3))
        ttk.Label(
            night_controls,
            text="夜间屏幕",
            style="Surface.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", padx=(0, 12))
        self.scale_night = ttk.Scale(
            night_controls,
            from_=0,
            to=100,
            variable=self.night_level_var,
            command=self.on_night_level_preview,
            style="Soft.Horizontal.TScale",
            length=210,
        )
        self.scale_night.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.lbl_night_level = ttk.Label(
            night_controls,
            text="极暗",
            style="Surface.TLabel",
            width=5,
        )
        self.lbl_night_level.pack(side="left", padx=(0, 8))
        self.make_button(
            night_controls,
            "应用",
            self.apply_night_screen,
            "neutral",
            pady=5,
        ).pack(side="left", padx=(0, 6))
        self.make_button(
            night_controls,
            "恢复",
            self.restore_night_screen,
            "neutral",
            pady=5,
        ).pack(side="left")
        night_options = tk.Frame(night_panel, bg=self.bg_surface)
        night_options.pack(fill="x", padx=12, pady=(0, 7))
        ttk.Checkbutton(
            night_options,
            text="开始挂机后自动变暗",
            variable=self.night_auto_var,
            command=self.save_night_screen_config,
            style="Card.TCheckbutton",
        ).pack(side="left")
        ttk.Label(
            night_options,
            text="紧急恢复：Ctrl + Alt + Home",
            style="Surface.TLabel",
            foreground=self.fg_gray,
        ).pack(side="right")

        list_shell = tk.Frame(steps_card, bg=self.bg_card)
        list_shell.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas_steps = tk.Canvas(
            list_shell,
            bg=self.bg_card,
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(
            list_shell,
            orient="vertical",
            command=self.canvas_steps.yview,
            style="Soft.Vertical.TScrollbar",
        )
        self.scrollable_frame = tk.Frame(self.canvas_steps, bg=self.bg_card)
        self.scrollable_frame.bind(
            "<Configure>",
            self.refresh_steps_scrollregion,
        )
        self.steps_window_id = self.canvas_steps.create_window(
            (0, 0),
            window=self.scrollable_frame,
            anchor="nw",
        )
        self.canvas_steps.bind(
            "<Configure>",
            self.on_steps_canvas_resize,
        )
        self.canvas_steps.configure(yscrollcommand=scrollbar.set)
        self.canvas_steps.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for wheel_widget in (
            list_shell,
            self.canvas_steps,
            self.scrollable_frame,
            scrollbar,
        ):
            self.bind_steps_mousewheel(wheel_widget)

        # 日志卡片，可通过横向分隔线收起或展开。
        log_card = self.make_card(self.body_pane)
        log_header = tk.Frame(log_card, bg=self.bg_card)
        log_header.pack(fill="x", padx=14, pady=(10, 6))
        ttk.Label(
            log_header,
            text="运行记录",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        ttk.Label(
            log_header,
            text="拖动上方分隔线可调整高度",
            style="CardMuted.TLabel",
        ).pack(side="right")
        log_body = tk.Frame(log_card, bg=self.bg_card)
        log_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        log_y = ttk.Scrollbar(
            log_body,
            orient="vertical",
            style="Soft.Vertical.TScrollbar",
        )
        log_x = ttk.Scrollbar(log_body, orient="horizontal")
        self.txt_log = tk.Text(
            log_body,
            height=7,
            bg=self.bg_input,
            fg="#8fd6b5",
            selectbackground=self.btn_neutral,
            selectforeground=self.fg_white,
            font=("Cascadia Mono", 9),
            insertbackground=self.fg_white,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            wrap="none",
            yscrollcommand=log_y.set,
            xscrollcommand=log_x.set,
        )
        log_y.config(command=self.txt_log.yview)
        log_x.config(command=self.txt_log.xview)
        log_y.pack(side="right", fill="y")
        log_x.pack(side="bottom", fill="x")
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.config(state="disabled")

        self.body_pane.add(workspace, minsize=430, stretch="always")
        self.body_pane.add(log_card, minsize=120, height=190, stretch="never")
        self.check_template_status()

        # 通用脚本 Tab（渐进迁移：与旧 AE 模式并列，互不影响）
        self.script_tab = ttk.Frame(self.mode_notebook)
        self.mode_notebook.add(self.script_tab, text="  通用脚本  ")
        self.generic_script_ui = GenericScriptUI(
            self.script_tab,
            app=self,
            scripts_dir=SCRIPTS_DIR,
        )
        self.generic_script_ui.pack(fill="both", expand=True)

    # --- 辅助方法 ---
    def log(self, message):
        LOGGER.info(message)
        self.log_queue.put(message)

    def on_close(self):
        if not self.resolve_unsaved_profile(
            "关闭程序",
            "当前部署方案有未保存的修改。",
        ):
            return
        # 通用脚本未保存保护 + 停止正在运行的脚本
        if hasattr(self, "generic_script_ui") and not self.generic_script_ui.on_close():
            return
        self.running = False
        self.selection_in_progress = False
        if self.active_selection_overlay is not None:
            try:
                self.active_selection_overlay.destroy()
            except tk.TclError:
                pass
            self.active_selection_overlay = None
        self.save_night_screen_config()
        try:
            self.display_dimmer.restore()
        except Exception:
            LOGGER.exception("退出时恢复屏幕亮度失败")
        self.root.destroy()

    def get_main_window_hwnd(self):
        self.root.update_idletasks()
        return win32gui.GetAncestor(self.root.winfo_id(), win32con.GA_ROOT)

    def get_monitor_work_areas(self):
        return [
            win32api.GetMonitorInfo(monitor)["Work"]
            for monitor, _dc, _rect in win32api.EnumDisplayMonitors()
        ]

    @staticmethod
    def rect_intersects_any(rect, monitor_rects):
        left, top, right, bottom = rect
        return any(
            min(right, mon_right) > max(left, mon_left)
            and min(bottom, mon_bottom) > max(top, mon_top)
            for mon_left, mon_top, mon_right, mon_bottom in monitor_rects
        )

    def remember_main_window_position(self):
        """在 Tk withdraw 前保存 Win32 物理坐标，避免负坐标被 Tk 重新解释。"""
        hwnd = self.get_main_window_hwnd()
        rect = win32gui.GetWindowRect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if (
            width >= 300
            and height >= 200
            and self.rect_intersects_any(rect, self.get_monitor_work_areas())
        ):
            self.main_window_restore_rect = rect
        self.main_window_restore_state = self.root.state()
        self.log(
            f"[窗口恢复] 隐藏前物理矩形={rect}, "
            f"saved={self.main_window_restore_rect}"
        )

    def hide_main_window_for_selection(self):
        self.remember_main_window_position()
        self.root.withdraw()

    def get_safe_restore_rect(self):
        monitor_rects = self.get_monitor_work_areas()
        rect = self.main_window_restore_rect
        if rect and self.rect_intersects_any(rect, monitor_rects):
            return rect

        # 没有可信历史位置时居中到主显示器；绝不使用 Tk 的负 geometry 语义。
        primary = next(
            (
                win32api.GetMonitorInfo(monitor)["Work"]
                for monitor, _dc, _rect in win32api.EnumDisplayMonitors()
                if win32api.GetMonitorInfo(monitor).get("Flags", 0) & 1
            ),
            monitor_rects[0],
        )
        left, top, right, bottom = primary
        width = min(1280, max(980, right - left - 80))
        height = min(820, max(680, bottom - top - 80))
        x = left + max(20, (right - left - width) // 2)
        y = top + max(20, (bottom - top - height) // 2)
        return x, y, x + width, y + height

    def restore_main_window(self):
        """通过 Win32 物理坐标恢复控制台，绕开 Tk 对负 geometry 的解释。"""
        if not self.root.winfo_exists():
            return
        self.root.deiconify()
        self.root.update_idletasks()
        hwnd = self.get_main_window_hwnd()
        left, top, right, bottom = self.get_safe_restore_rect()
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            left,
            top,
            max(300, right - left),
            max(200, bottom - top),
            win32con.SWP_SHOWWINDOW,
        )
        if self.main_window_restore_state == "zoomed":
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        self.root.focus_force()
        restored_rect = win32gui.GetWindowRect(hwnd)
        self.log(f"[窗口恢复] 已恢复物理矩形={restored_rect}")

        def release_topmost():
            try:
                if self.root.winfo_exists():
                    hwnd = self.get_main_window_hwnd()
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_NOTOPMOST,
                        0,
                        0,
                        0,
                        0,
                        win32con.SWP_NOMOVE
                        | win32con.SWP_NOSIZE
                        | win32con.SWP_NOACTIVATE,
                    )
            except tk.TclError:
                pass

        self.root.after(250, release_topmost)

    def finish_selection_ui(self, overlay=None):
        """关闭选点遮罩并带回主窗口，不改变用户当前设置的尺寸和位置。"""
        target = overlay or self.active_selection_overlay
        if target is not None:
            try:
                if target.winfo_exists():
                    target.destroy()
            except tk.TclError:
                pass
        self.active_selection_overlay = None
        self.selection_in_progress = False
        self.bring_main_window_to_front()

    def bring_main_window_to_front(self):
        """只调整 Z 顺序，不调用 withdraw/deiconify，也不改窗口矩形。"""
        if not self.root.winfo_exists():
            return
        hwnd = self.get_main_window_hwnd()
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_SHOWWINDOW,
        )
        self.root.focus_force()

        def release_topmost():
            try:
                if self.root.winfo_exists():
                    win32gui.SetWindowPos(
                        self.get_main_window_hwnd(),
                        win32con.HWND_NOTOPMOST,
                        0,
                        0,
                        0,
                        0,
                        win32con.SWP_NOMOVE
                        | win32con.SWP_NOSIZE
                        | win32con.SWP_NOACTIVATE,
                    )
            except tk.TclError:
                pass

        self.root.after(250, release_topmost)

    def on_night_level_preview(self, value):
        level = int(round(float(value)))
        label = "极暗" if level == 0 else ("正常" if level == 100 else f"{level}%")
        self.lbl_night_level.config(text=label)

    def load_night_screen_config(self):
        try:
            with open(NIGHT_SCREEN_FILE, "r", encoding="utf-8") as file:
                settings = json.load(file)
            self.night_level_var.set(
                max(0, min(100, int(settings.get("level", 0))))
            )
            self.night_auto_var.set(bool(settings.get("auto_dim", False)))
        except FileNotFoundError:
            pass
        except Exception as error:
            self.log(f"夜间屏幕配置读取失败，已使用默认值: {error}")
        self.on_night_level_preview(self.night_level_var.get())

    def save_night_screen_config(self):
        settings = {
            "level": int(round(self.night_level_var.get())),
            "auto_dim": bool(self.night_auto_var.get()),
        }
        try:
            with open(NIGHT_SCREEN_FILE, "w", encoding="utf-8") as file:
                json.dump(settings, file, ensure_ascii=False, indent=2)
        except Exception as error:
            self.log(f"夜间屏幕配置保存失败: {error}")

    def apply_night_screen(self, automatic=False):
        level = int(round(self.night_level_var.get()))
        try:
            monitor_count = self.display_dimmer.apply(level)
            self.save_night_screen_config()
            source = "挂机自动" if automatic else "手动"
            self.log(
                f"[夜间屏幕] {source}应用成功：档位={level}，"
                f"显示器={monitor_count}；截图像素不受影响。"
            )
            return True
        except Exception as error:
            self.log(f"[夜间屏幕] 应用失败: {error}")
            if not automatic:
                messagebox.showerror("夜间屏幕", f"无法调节屏幕：\n{error}")
            return False

    def restore_night_screen(self, emergency=False):
        try:
            monitor_count = self.display_dimmer.restore()
            self.night_dimmed_by_farming = False
            prefix = "紧急快捷键" if emergency else "手动"
            self.log(f"[夜间屏幕] {prefix}恢复正常，共 {monitor_count} 台显示器。")
            return True
        except Exception as error:
            self.log(f"[夜间屏幕] 恢复失败: {error}")
            return False

    def poll_emergency_brightness_hotkey(self):
        try:
            user32 = ctypes.windll.user32
            ctrl_down = bool(user32.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)
            alt_down = bool(user32.GetAsyncKeyState(win32con.VK_MENU) & 0x8000)
            home_down = bool(user32.GetAsyncKeyState(win32con.VK_HOME) & 0x8000)
            hotkey_down = ctrl_down and alt_down and home_down
            if hotkey_down and not self.emergency_hotkey_was_down:
                self.restore_night_screen(emergency=True)
            self.emergency_hotkey_was_down = hotkey_down
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self.poll_emergency_brightness_hotkey)

    def get_target_client_bounds(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            raise ValueError("目标 Roblox 窗口句柄无效")
        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        if width <= 0 or height <= 0:
            raise ValueError(f"Roblox 客户区尺寸无效: {width}x{height}")
        return left, top, width, height

    def create_target_overlay(self, alpha, cursor, bg):
        """创建只覆盖所选 Roblox 客户区的遮罩，使用 Win32 绝对坐标支持负坐标显示器。"""
        left, top, width, height = self.get_target_client_bounds()
        overlay = tk.Toplevel(self.root)
        overlay.withdraw()
        overlay.overrideredirect(True)
        overlay.geometry(f"{width}x{height}+0+0")
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", alpha)
        overlay.config(cursor=cursor)
        canvas = tk.Canvas(overlay, cursor=cursor, bg=bg, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        overlay.update_idletasks()
        overlay.deiconify()
        overlay.update_idletasks()

        # Tk 的负 geometry 坐标代表“距右/下边缘”，不能用于虚拟桌面的绝对负坐标。
        # 直接通过 Win32 把遮罩放到 Roblox 客户区的物理屏幕矩形。
        overlay_hwnd = win32gui.GetAncestor(overlay.winfo_id(), win32con.GA_ROOT)
        win32gui.SetWindowPos(
            overlay_hwnd,
            win32con.HWND_TOPMOST,
            left,
            top,
            width,
            height,
            win32con.SWP_SHOWWINDOW,
        )
        overlay.focus_force()
        self.log(
            f"选点遮罩已绑定 Roblox: hwnd={self.hwnd}, "
            f"screen=({left},{top}), size={width}x{height}"
        )
        return overlay, canvas
        
    def process_logs(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self.root.after(100, self.process_logs)
        
    def refresh_windows(self):
        hwnds = engine.find_roblox_hwnd()
        current_hwnd = self.hwnd
        self.window_lookup = {}
        displays = []
        for hwnd, title in hwnds:
            pid, process_path = engine.get_window_process_info(hwnd)
            display = f"{title}  [PID {pid} | HWND {hwnd}]"
            self.window_lookup[display] = hwnd
            displays.append(display)

        self.cb_windows["values"] = displays

        if displays:
            selected = next(
                (display for display, hwnd in self.window_lookup.items() if hwnd == current_hwnd),
                displays[0],
            )
            self.cb_windows.set(selected)
            self.on_window_select(None)
        else:
            self.cb_windows.set("未检测到 Roblox 游戏窗口")
            self.hwnd = None
            
    def on_window_select(self, event):
        val = self.cb_windows.get()
        hwnd = self.window_lookup.get(val)
        if hwnd and win32gui.IsWindow(hwnd):
            self.hwnd = hwnd
            pid, process_path = engine.get_window_process_info(hwnd)
            self.log(
                f"已锁定 RobloxPlayerBeta.exe: hwnd={hwnd}, pid={pid}, path={process_path}"
            )
        else:
            self.hwnd = None
            self.log(f"目标窗口选择无效: {val}")
                
    def check_template_status(self):
        start_path = os.path.join(TEMPLATES_DIR, "start_btn.png")
        metadata_path = os.path.splitext(start_path)[0] + ".json"
        if os.path.exists(start_path):
            template_img = cv2.imread(start_path)
            valid_size = (
                template_img is not None
                and template_img.shape[1] >= 20
                and template_img.shape[0] >= 10
            )
            if valid_size and os.path.exists(metadata_path):
                text = "开始按钮模板与点击锚点: [ 已就绪 ✅ ]"
            else:
                text = "开始按钮模板: [ 旧版或尺寸无效，请重新截取 ⚠ ]"
            color = self.btn_success if valid_size and os.path.exists(metadata_path) else "#f59e0b"
            self.lbl_has_start.config(text=text, foreground=color)
        else:
            self.lbl_has_start.config(text="开始按钮模板: [ 未截取 ❌ ]", foreground=self.btn_danger)

    # --- 步骤添加与管理 ---
    def refresh_steps_scrollregion(self, event=None):
        bbox = self.canvas_steps.bbox("all")
        if bbox is None:
            self.canvas_steps.configure(scrollregion=(0, 0, 0, 0))
            return
        self.canvas_steps.configure(scrollregion=bbox)
        content_height = max(0, bbox[3] - bbox[1])
        if content_height <= self.canvas_steps.winfo_height():
            self.canvas_steps.yview_moveto(0)

    def on_steps_canvas_resize(self, event):
        self.canvas_steps.itemconfigure(
            self.steps_window_id,
            width=event.width,
        )
        self.refresh_steps_scrollregion()

    def on_steps_mousewheel(self, event):
        """仅滚动部署动作画布，不把滚轮事件泄漏给下拉框或页面其他区域。"""
        bbox = self.canvas_steps.bbox("all")
        if bbox is None:
            return "break"
        content_height = max(0, bbox[3] - bbox[1])
        if content_height <= self.canvas_steps.winfo_height():
            self.canvas_steps.yview_moveto(0)
            return "break"

        delta = getattr(event, "delta", 0)
        if delta:
            direction = -1 if delta > 0 else 1
        else:
            direction = -1 if getattr(event, "num", 0) == 4 else 1
        self.canvas_steps.yview_scroll(direction * 3, "units")
        return "break"

    def bind_steps_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self.on_steps_mousewheel, add="+")
        widget.bind("<Button-4>", self.on_steps_mousewheel, add="+")
        widget.bind("<Button-5>", self.on_steps_mousewheel, add="+")
        for child in widget.winfo_children():
            self.bind_steps_mousewheel(child)

    def add_step(self, key="1", rx=0.5, ry=0.5, delay=0.5, mark_dirty=True):
        step_idx = len(self.steps)
        step_frame = tk.Frame(
            self.scrollable_frame,
            bg=self.bg_surface,
            highlightbackground=self.border_soft,
            highlightcolor=self.border_soft,
            highlightthickness=1,
            bd=0,
        )
        step_frame.pack(fill="x", padx=2, pady=5)
        step_frame.grid_columnconfigure(2, weight=1)

        lbl_num = tk.Label(
            step_frame,
            text=f"{step_idx + 1:02d}",
            bg=self.btn_neutral,
            fg=self.fg_white,
            font=("Cascadia Mono", 10, "bold"),
            padx=9,
            pady=7,
        )
        lbl_num.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=12, sticky="ns")

        ttk.Label(
            step_frame,
            text="单位槽",
            style="Surface.TLabel",
            foreground=self.fg_gray,
            font=self.font_small,
        ).grid(row=0, column=1, padx=(0, 8), pady=(10, 2), sticky="w")
        cb_key = ttk.Combobox(
            step_frame,
            values=["1", "2", "3", "4", "5", "6"],
            width=4,
            state="readonly",
            style="Soft.TCombobox",
        )
        cb_key.set(key)
        cb_key.grid(row=1, column=1, padx=(0, 8), pady=(0, 10), sticky="w")
        cb_key.bind("<<ComboboxSelected>>", self.mark_profile_dirty, add="+")

        ttk.Label(
            step_frame,
            text="放置坐标",
            style="Surface.TLabel",
            foreground=self.fg_gray,
            font=self.font_small,
        ).grid(row=0, column=2, padx=4, pady=(10, 2), sticky="w")
        lbl_coord = ttk.Label(
            step_frame,
            text=f"X {rx:.3f}   Y {ry:.3f}",
            style="Surface.TLabel",
            font=("Cascadia Mono", 9),
        )
        lbl_coord.grid(row=1, column=2, padx=4, pady=(0, 10), sticky="w")

        btn_pick = self.make_button(
            step_frame,
            "选取位置",
            lambda: self.pick_relative_coordinate(step_idx, lbl_coord),
            "primary",
        )
        btn_pick.grid(row=0, column=3, rowspan=2, padx=8, pady=12)

        ttk.Label(
            step_frame,
            text="间隔 / 秒",
            style="Surface.TLabel",
            foreground=self.fg_gray,
            font=self.font_small,
        ).grid(row=0, column=4, padx=4, pady=(10, 2), sticky="w")
        ent_delay = ttk.Entry(step_frame, width=7, style="Soft.TEntry")
        ent_delay.insert(0, str(delay))
        ent_delay.grid(row=1, column=4, padx=4, pady=(0, 10), sticky="w")
        ent_delay.bind("<KeyRelease>", self.mark_profile_dirty, add="+")
        ent_delay.bind("<FocusOut>", self.mark_profile_dirty, add="+")

        btn_test = self.make_button(
            step_frame,
            "测试",
            lambda: self.test_single_step(step_idx),
            "neutral",
        )
        btn_test.grid(row=0, column=5, rowspan=2, padx=(8, 4), pady=12)

        btn_del = self.make_button(
            step_frame,
            "删除",
            lambda: self.delete_step(step_idx),
            "danger",
        )
        btn_del.grid(row=0, column=6, rowspan=2, padx=(4, 10), pady=12)
        
        # 动作字典缓存
        step_data = {
            "frame": step_frame,
            "lbl_num": lbl_num,
            "cb_key": cb_key,
            "lbl_coord": lbl_coord,
            "ent_delay": ent_delay,
            "btn_pick": btn_pick,
            "btn_test": btn_test,
            "btn_del": btn_del,
            "rx": rx,
            "ry": ry
        }
        self.steps.append(step_data)
        self.bind_steps_mousewheel(step_frame)
        self.update_step_numbers()
        if mark_dirty:
            self.mark_profile_dirty()
        
    def delete_step(self, index):
        step_data = self.steps[index]
        step_data["frame"].destroy()
        self.steps.pop(index)
        self.update_step_numbers()
        self.mark_profile_dirty()
        self.root.after_idle(self.refresh_steps_scrollregion)
        
    def update_step_numbers(self):
        for i, step_data in enumerate(self.steps):
            step_data["lbl_num"].config(text=f"{i + 1:02d}")
            step_data["btn_pick"].config(
                command=lambda idx=i, label=step_data["lbl_coord"]: self.pick_relative_coordinate(
                    idx,
                    label,
                )
            )
            step_data["btn_test"].config(command=lambda idx=i: self.test_single_step(idx))
            step_data["btn_del"].config(command=lambda idx=i: self.delete_step(idx))

    # --- 自定义坐标拾取机制 ---
    def pick_relative_coordinate(self, step_index, label_widget):
        if self.selection_in_progress:
            self.log("已有选点操作正在进行，已忽略重复请求。")
            return
        if not self.hwnd:
            messagebox.showwarning("警告", "请先在左侧选择目标 Roblox 游戏窗口！")
            return
            
        title = win32gui.GetWindowText(self.hwnd)
        if "Launcher" in title or "配置器" in title:
            messagebox.showwarning("警告", "您当前选中的窗口是配置器自己，请切换为实际的 'Roblox' 游戏客户端！")
            return
            
        self.selection_in_progress = True
        self.log("[选点] 控制台保持可见；仅在 Roblox 客户区显示置顶遮罩。")

        def cancel_selection(event=None):
            self.finish_selection_ui()
            self.log("已取消放置位置选取。")

        def on_click(event, overlay):
            px, py = win32gui.GetCursorPos()
            error_message = None
            try:
                cx, cy = win32gui.ScreenToClient(self.hwnd, (px, py))
                _, _, cw, ch = win32gui.GetClientRect(self.hwnd)

                if cw <= 0 or ch <= 0:
                    raise ValueError(f"Roblox 客户区尺寸无效: {cw}x{ch}")
                if not (0 <= cx < cw and 0 <= cy < ch):
                    self.log(
                        f"选点已拒绝：屏幕点 ({px},{py}) 换算为客户区 ({cx},{cy})，"
                        f"不在 0..{cw - 1}, 0..{ch - 1} 范围内。请确认目标窗口和显示器。"
                    )
                    error_message = (
                        "点击位置不在所选 Roblox 客户区内，坐标没有保存。\n"
                        "请检查目标窗口是否选择正确，以及 Roblox 是否位于可见屏幕上。"
                    )
                    return

                rx = cx / max(1, cw - 1)
                ry = cy / max(1, ch - 1)
                
                self.steps[step_index]["rx"] = rx
                self.steps[step_index]["ry"] = ry
                label_widget.config(text=f"X {rx:.3f}   Y {ry:.3f}")
                self.mark_profile_dirty()
                self.log(
                    f"步骤 {step_index + 1} 捕获坐标: screen=({px},{py}), "
                    f"client=({cx},{cy})/{cw}x{ch}, relative=({rx:.4f},{ry:.4f})"
                )
            except Exception as e:
                self.log(f"坐标换算失败 (请确认游戏窗口未最小化且选择句柄正确): {e}")
                error_message = str(e)
            finally:
                self.finish_selection_ui(overlay)
                if error_message:
                    messagebox.showerror("选点无效", error_message)

        def open_overlay():
            if not self.selection_in_progress:
                return
            try:
                overlay, canvas = self.create_target_overlay(
                    0.3,
                    "crosshair",
                    "black",
                )
                self.active_selection_overlay = overlay
                canvas.bind(
                    "<Button-1>",
                    lambda event: on_click(event, overlay),
                )
                overlay.bind("<Escape>", cancel_selection)
                overlay.bind("<Button-3>", cancel_selection)
            except Exception as e:
                self.finish_selection_ui()
                self.log(f"无法创建 Roblox 选点遮罩: {e}")
                messagebox.showerror("选点失败", str(e))

        # 让 Tk 完成主窗口隐藏后再创建遮罩，避免在 GUI 主线程 sleep 假死。
        self.root.after(300, open_overlay)
        
    # --- 按钮图像模板裁剪 ---
    def start_crop_template(self, filename):
        if not self.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 游戏窗口！")
            return
            
        title = win32gui.GetWindowText(self.hwnd)
        if "Launcher" in title or "配置器" in title:
            messagebox.showwarning("警告", "当前选中的窗口为控制软件本身，请切换为实际的游戏客户端！")
            return
            
        self.hide_main_window_for_selection()
        time.sleep(0.3)

        try:
            overlay, canvas = self.create_target_overlay(0.2, "cross", "gray")
        except Exception as e:
            self.restore_main_window()
            self.log(f"无法创建 Roblox 裁剪遮罩: {e}")
            messagebox.showerror("裁剪失败", str(e))
            return
        
        start_screen = [None]
        start_canvas = [None]
        rect_id = [None]
        pending_crop = [None]
        instruction_id = [None]

        def finish_crop(gx1, gy1, gx2, gy2, anchor_x, anchor_y):
            try:
                win_left, win_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
                _, _, cw, ch = win32gui.GetClientRect(self.hwnd)

                rx1 = gx1 - win_left
                ry1 = gy1 - win_top
                rx2 = gx2 - win_left
                ry2 = gy2 - win_top

                if rx1 < 0 or ry1 < 0 or rx2 > cw or ry2 > ch:
                    raise ValueError(
                        f"裁剪框不完全位于 Roblox 客户区："
                        f"crop=({rx1},{ry1})-({rx2},{ry2}), client={cw}x{ch}"
                    )
                if rx2 - rx1 < 20 or ry2 - ry1 < 10:
                    raise ValueError("裁剪区域过小；宽度至少 20 像素，高度至少 10 像素。")

                # 等待遮罩从桌面合成画面中完全消失，避免把半透明灰层保存进模板。
                time.sleep(0.15)
                full_img = engine.capture_window(self.hwnd)
                if full_img is None:
                    raise RuntimeError("无法抓取 Roblox 画面，请确认窗口未最小化或被遮挡。")

                cropped = full_img[ry1:ry2, rx1:rx2]
                if cropped.size == 0:
                    raise ValueError("截图区域宽度或高度过小。")

                save_path = os.path.join(TEMPLATES_DIR, filename)
                metadata_path = os.path.splitext(save_path)[0] + ".json"
                cv2.imwrite(save_path, cropped)

                click_offset_x = (anchor_x - gx1) / max(1, (gx2 - gx1) - 1)
                click_offset_y = (anchor_y - gy1) / max(1, (gy2 - gy1) - 1)
                metadata = {
                    "version": 1,
                    "click_offset_x": click_offset_x,
                    "click_offset_y": click_offset_y,
                    "template_width": int(cropped.shape[1]),
                    "template_height": int(cropped.shape[0]),
                }
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

                self.log(
                    f"模板已保存: {save_path}; 点击锚点偏移="
                    f"({click_offset_x:.4f},{click_offset_y:.4f})"
                )
                self.check_template_status()
            except Exception as e:
                self.log(f"模板截取失败: {e}")
                messagebox.showerror("模板截取失败", str(e))
            finally:
                self.restore_main_window()

        def cancel_crop(event=None):
            try:
                overlay.destroy()
            finally:
                self.restore_main_window()
                self.log("已取消模板截取。")
        
        def on_press(event):
            if pending_crop[0] is not None:
                return
            start_screen[0] = win32gui.GetCursorPos()
            start_canvas[0] = (event.x, event.y)
            rect_id[0] = canvas.create_rectangle(event.x, event.y, event.x+1, event.y+1, outline="red", width=2)
            
        def on_drag(event):
            if start_canvas[0] is not None and pending_crop[0] is None:
                sx, sy = start_canvas[0]
                canvas.coords(rect_id[0], sx, sy, event.x, event.y)
                
        def on_release(event):
            if start_screen[0] is None or pending_crop[0] is not None:
                return
            end_x, end_y = win32gui.GetCursorPos()
            x1, y1 = start_screen[0]
            x2, y2 = end_x, end_y

            gx1 = min(x1, x2)
            gy1 = min(y1, y2)
            gx2 = max(x1, x2)
            gy2 = max(y1, y2)

            if gx2 - gx1 < 20 or gy2 - gy1 < 10:
                messagebox.showerror(
                    "裁剪区域过小",
                    "请拖动框选一个至少 20×10 像素的识别区域。",
                )
                return

            pending_crop[0] = (gx1, gy1, gx2, gy2)
            canvas.unbind("<ButtonPress-1>")
            canvas.unbind("<B1-Motion>")
            canvas.unbind("<ButtonRelease-1>")
            canvas.config(cursor="hand2")

            _, _, overlay_width, _ = win32gui.GetClientRect(
                win32gui.GetAncestor(overlay.winfo_id(), win32con.GA_ROOT)
            )
            instruction_id[0] = canvas.create_text(
                overlay_width // 2,
                28,
                text="识别区域已框选；现在请单击绿色“开始游戏”按钮的中心",
                fill="#ffff00",
                font=("Microsoft YaHei", 14, "bold"),
            )

            def on_anchor_click(anchor_event):
                anchor_screen_x, anchor_screen_y = win32gui.GetCursorPos()
                crop_x1, crop_y1, crop_x2, crop_y2 = pending_crop[0]
                if not (
                    crop_x1 <= anchor_screen_x < crop_x2
                    and crop_y1 <= anchor_screen_y < crop_y2
                ):
                    self.log(
                        f"点击锚点不在识别区域内: ({anchor_screen_x},{anchor_screen_y})"
                    )
                    messagebox.showerror(
                        "点击点无效",
                        "实际点击点必须位于刚才框选的识别区域内部。",
                    )
                    return

                canvas.unbind("<Button-1>")
                overlay.destroy()
                finish_crop(
                    crop_x1,
                    crop_y1,
                    crop_x2,
                    crop_y2,
                    anchor_screen_x,
                    anchor_screen_y,
                )

            canvas.bind("<Button-1>", on_anchor_click)
                
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", cancel_crop)

    # --- 通用坐标拾取（复用选点遮罩，结果经回调返回） ---
    def pick_coordinate_generic(self, on_pick):
        if self.selection_in_progress:
            self.log("已有选点操作正在进行，已忽略重复请求。")
            return
        if not self.hwnd:
            messagebox.showwarning("警告", "请先在左侧选择目标 Roblox 游戏窗口！")
            return
        title = win32gui.GetWindowText(self.hwnd)
        if "Launcher" in title or "配置器" in title:
            messagebox.showwarning("警告", "当前窗口为控制软件本身，请切换为 Roblox 游戏客户端！")
            return

        self.selection_in_progress = True

        def cancel_selection(event=None):
            self.finish_selection_ui()
            self.log("已取消坐标选取。")

        def on_click(event, overlay):
            px, py = win32gui.GetCursorPos()
            error_message = None
            try:
                cx, cy = win32gui.ScreenToClient(self.hwnd, (px, py))
                _, _, cw, ch = win32gui.GetClientRect(self.hwnd)
                if cw <= 0 or ch <= 0:
                    raise ValueError(f"Roblox 客户区尺寸无效: {cw}x{ch}")
                if not (0 <= cx < cw and 0 <= cy < ch):
                    error_message = "点击位置不在所选 Roblox 客户区内，坐标没有保存。"
                    return
                rx = cx / max(1, cw - 1)
                ry = cy / max(1, ch - 1)
                on_pick(rx, ry)
                self.log(f"捕获坐标: client=({cx},{cy})/{cw}x{ch}, relative=({rx:.4f},{ry:.4f})")
            except Exception as e:
                error_message = str(e)
            finally:
                self.finish_selection_ui(overlay)
                if error_message:
                    messagebox.showerror("选点无效", error_message)

        def open_overlay():
            if not self.selection_in_progress:
                return
            try:
                overlay, canvas = self.create_target_overlay(0.3, "crosshair", "black")
                self.active_selection_overlay = overlay
                canvas.bind("<Button-1>", lambda event: on_click(event, overlay))
                overlay.bind("<Escape>", cancel_selection)
                overlay.bind("<Button-3>", cancel_selection)
            except Exception as e:
                self.finish_selection_ui()
                self.log(f"无法创建选点遮罩: {e}")
                messagebox.showerror("选点失败", str(e))

        self.root.after(300, open_overlay)

    # --- 通用模板截取（复用框选遮罩，结果经回调返回） ---
    def crop_template_generic(self, on_crop):
        if not self.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 游戏窗口！")
            return
        title = win32gui.GetWindowText(self.hwnd)
        if "Launcher" in title or "配置器" in title:
            messagebox.showwarning("警告", "当前窗口为控制软件本身，请切换为 Roblox 游戏客户端！")
            return

        self.hide_main_window_for_selection()
        time.sleep(0.3)
        try:
            overlay, canvas = self.create_target_overlay(0.15, "crosshair", "gray")
        except Exception as e:
            self.restore_main_window()
            self.log(f"无法创建裁剪遮罩: {e}")
            messagebox.showerror("裁剪失败", str(e))
            return

        # 顶部提示文字，明确告诉用户当前正在框选
        canvas.create_text(
            16, 24, anchor="w",
            text="✛ 按住鼠标左键拖动，框选要识别的区域（Esc 取消）",
            fill="#ffff00", font=("Microsoft YaHei", 14, "bold"),
        )

        start_screen = [None]
        start_canvas = [None]
        rect_id = [None]

        def finish_crop(gx1, gy1, gx2, gy2):
            try:
                win_left, win_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
                _, _, cw, ch = win32gui.GetClientRect(self.hwnd)
                rx1 = gx1 - win_left
                ry1 = gy1 - win_top
                rx2 = gx2 - win_left
                ry2 = gy2 - win_top
                if rx1 < 0 or ry1 < 0 or rx2 > cw or ry2 > ch:
                    raise ValueError("裁剪框不完全位于 Roblox 客户区")
                if rx2 - rx1 < 20 or ry2 - ry1 < 10:
                    raise ValueError("裁剪区域过小；宽度至少 20 像素，高度至少 10 像素。")

                time.sleep(0.15)
                full_img = engine.capture_window(self.hwnd)
                if full_img is None:
                    raise RuntimeError("无法抓取 Roblox 画面，请确认窗口未最小化或被遮挡。")

                cropped = full_img[ry1:ry2, rx1:rx2]
                if cropped.size == 0:
                    raise ValueError("截图区域宽度或高度过小。")

                # 默认以框选区域中心作为点击锚点（后续可再调整）
                on_crop(cropped, 0.5, 0.5)
            except Exception as e:
                self.log(f"模板截取失败: {e}")
                messagebox.showerror("模板截取失败", str(e))
            finally:
                self.restore_main_window()

        def cancel_crop(event=None):
            try:
                overlay.destroy()
            finally:
                self.restore_main_window()
                self.log("已取消模板截取。")

        def on_press(event):
            start_screen[0] = win32gui.GetCursorPos()
            start_canvas[0] = (event.x, event.y)
            # 明显的黄色高亮框
            rect_id[0] = canvas.create_rectangle(
                event.x, event.y, event.x + 1, event.y + 1,
                outline="#ffff00", width=3,
            )

        def on_drag(event):
            if start_canvas[0] is not None:
                sx, sy = start_canvas[0]
                canvas.coords(rect_id[0], sx, sy, event.x, event.y)

        def on_release(event):
            if start_screen[0] is None:
                return
            end_x, end_y = win32gui.GetCursorPos()
            x1, y1 = start_screen[0]
            gx1 = min(x1, end_x)
            gy1 = min(y1, end_y)
            gx2 = max(x1, end_x)
            gy2 = max(y1, end_y)
            if gx2 - gx1 < 20 or gy2 - gy1 < 10:
                messagebox.showerror("裁剪区域过小", "请拖动框选一个至少 20×10 像素的识别区域。")
                return
            overlay.destroy()
            finish_crop(gx1, gy1, gx2, gy2)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", cancel_crop)

    # --- 部署方案库 ---
    def mark_profile_dirty(self, event=None):
        if self.loading_profile or self.active_profile_id is None:
            return
        self.profile_dirty = True
        self.update_profile_status()

    def update_profile_status(self):
        if not hasattr(self, "lbl_profile_state"):
            return
        if self.profile_dirty:
            self.lbl_profile_state.config(text="● 未保存", fg="#e6b566")
            self.btn_profile_save.config(
                state="normal",
                bg=self.btn_primary,
                fg=self.fg_white,
                cursor="hand2",
            )
        else:
            self.lbl_profile_state.config(text="● 已保存", fg="#63cba5")
            self.btn_profile_save.config(
                state="disabled",
                bg=self.bg_input,
                disabledforeground=self.fg_dim,
                cursor="arrow",
            )

        preview_path = None
        profile = None
        if self.profile_store and self.active_profile_id:
            try:
                profile = self.profile_store.load_profile(self.active_profile_id)
                preview_path = self.profile_store.preview_path(self.active_profile_id)
            except ProfileStoreError:
                pass
        if preview_path and profile:
            preview = profile.get("preview") or {}
            width = preview.get("width", "?")
            height = preview.get("height", "?")
            self.lbl_preview_state.config(
                text=f"已有预览 · {width}×{height}",
                foreground=self.fg_gray,
            )
            self.btn_view_preview.config(state="normal")
        else:
            self.lbl_preview_state.config(
                text="尚无预览",
                foreground=self.fg_dim,
            )
            self.btn_view_preview.config(state="disabled")

    def refresh_profile_selector(self):
        profiles = self.profile_store.list_profiles()
        self.profile_lookup = {
            profile["name"]: profile["id"]
            for profile in profiles
        }
        names = list(self.profile_lookup)
        self.cb_profiles["values"] = names
        active_name = next(
            (
                profile["name"]
                for profile in profiles
                if profile["id"] == self.active_profile_id
            ),
            names[0] if names else "",
        )
        self.cb_profiles.set(active_name)
        self.update_profile_status()

    def clear_steps(self):
        for step in self.steps:
            step["frame"].destroy()
        self.steps.clear()
        self.root.after_idle(self.refresh_steps_scrollregion)

    def load_profile_into_ui(self, profile_id):
        profile = self.profile_store.load_profile(profile_id)
        self.loading_profile = True
        try:
            self.clear_steps()
            for item in profile["steps"]:
                self.add_step(
                    key=item["key"],
                    rx=item["rx"],
                    ry=item["ry"],
                    delay=item["delay"],
                    mark_dirty=False,
                )
            self.active_profile_id = profile_id
            self.profile_dirty = False
        finally:
            self.loading_profile = False
        self.refresh_profile_selector()
        self.log(
            f"已载入部署方案“{profile['name']}”，共 {len(profile['steps'])} 个步骤。"
        )

    def resolve_unsaved_profile(self, action_title, message):
        if not self.profile_dirty:
            return True
        decision = messagebox.askyesnocancel(
            action_title,
            f"{message}\n\n是否先保存当前方案？",
            parent=self.root,
        )
        if decision is None:
            return False
        if decision:
            return self.save_active_profile()
        return True

    def save_active_profile(self, log_success=True):
        if not self.profile_store or not self.active_profile_id:
            return False
        try:
            steps = self.build_current_steps_sequence()
            profile = self.profile_store.save_steps(self.active_profile_id, steps)
            self.profile_dirty = False
            self.refresh_profile_selector()
            if log_success:
                self.log(f"部署方案“{profile['name']}”已保存。")
            return True
        except (ValueError, ProfileStoreError) as error:
            self.log(f"保存部署方案失败: {error}")
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return False

    def save_config(self, log_success=True):
        """兼容旧调用入口；实际数据只写入当前部署方案。"""
        return self.save_active_profile(log_success=log_success)

    def load_config(self):
        try:
            self.profile_store = ProfileStore(PROFILES_DIR, CONFIG_FILE)
            self.active_profile_id = self.profile_store.active_profile_id()
            self.load_profile_into_ui(self.active_profile_id)
            if self.profile_store.migration_performed:
                self.log(
                    "旧 config.json 已导入为“默认方案”；原文件保持不变。"
                )
        except ProfileStoreError as error:
            self.log(f"部署方案库载入失败: {error}")
            messagebox.showerror(
                "方案库载入失败",
                f"{error}\n\n请检查目录：\n{PROFILES_DIR}",
                parent=self.root,
            )

    def on_profile_selected(self, event=None):
        selected_name = self.cb_profiles.get()
        target_id = self.profile_lookup.get(selected_name)
        if not target_id or target_id == self.active_profile_id:
            return
        if not self.resolve_unsaved_profile(
            "切换部署方案",
            "切换后，当前未保存的修改将被放弃。",
        ):
            self.refresh_profile_selector()
            return
        try:
            self.profile_store.set_active_profile(target_id)
            self.load_profile_into_ui(target_id)
        except ProfileStoreError as error:
            self.refresh_profile_selector()
            messagebox.showerror("切换失败", str(error), parent=self.root)

    def create_new_profile(self):
        if not self.resolve_unsaved_profile(
            "新建部署方案",
            "新建后，当前未保存的修改将被放弃。",
        ):
            return
        name = simpledialog.askstring(
            "新建部署方案",
            "输入方案名称：",
            parent=self.root,
        )
        if name is None:
            return
        try:
            profile = self.profile_store.create_profile(name)
            self.load_profile_into_ui(profile["id"])
            self.log(f"已新建部署方案“{profile['name']}”。")
        except ProfileStoreError as error:
            messagebox.showerror("新建失败", str(error), parent=self.root)

    def save_profile_as(self):
        name = simpledialog.askstring(
            "另存为新方案",
            "输入新方案名称：",
            parent=self.root,
        )
        if name is None:
            return
        try:
            steps = self.build_current_steps_sequence()
            profile = self.profile_store.duplicate_profile(
                self.active_profile_id,
                name,
                steps=steps,
            )
            self.load_profile_into_ui(profile["id"])
            self.log(f"当前部署内容已另存为“{profile['name']}”。")
        except (ValueError, ProfileStoreError) as error:
            messagebox.showerror("另存为失败", str(error), parent=self.root)

    def rename_active_profile(self):
        current = self.profile_store.load_profile(self.active_profile_id)
        name = simpledialog.askstring(
            "重命名部署方案",
            "输入新的方案名称：",
            initialvalue=current["name"],
            parent=self.root,
        )
        if name is None or name.strip() == current["name"]:
            return
        try:
            profile = self.profile_store.rename_profile(
                self.active_profile_id,
                name,
            )
            self.refresh_profile_selector()
            self.log(f"部署方案已重命名为“{profile['name']}”。")
        except ProfileStoreError as error:
            messagebox.showerror("重命名失败", str(error), parent=self.root)

    def delete_active_profile(self):
        profile = self.profile_store.load_profile(self.active_profile_id)
        if not messagebox.askyesno(
            "删除部署方案",
            f"确定删除“{profile['name']}”吗？\n\n"
            "方案会移入 .trash，可手动恢复。",
            parent=self.root,
        ):
            return
        try:
            next_id, trash_path = self.profile_store.delete_profile(
                self.active_profile_id
            )
            self.load_profile_into_ui(next_id)
            self.log(
                f"部署方案“{profile['name']}”已移入可恢复目录: {trash_path}"
            )
        except ProfileStoreError as error:
            messagebox.showerror("删除失败", str(error), parent=self.root)

    def capture_profile_preview(self):
        if self.preview_capture_in_progress:
            return
        if self.profile_dirty:
            if not messagebox.askyesno(
                "先保存部署方案",
                "预览图会归档到当前方案。为避免预览与部署动作不一致，"
                "需要先保存当前修改。\n\n现在保存并继续截取吗？",
                parent=self.root,
            ):
                return
            if not self.save_active_profile(log_success=False):
                return
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            messagebox.showwarning(
                "无法截取",
                "请先选择有效的 Roblox 游戏窗口。",
                parent=self.root,
            )
            return

        self.preview_capture_in_progress = True
        self.btn_capture_preview.config(state="disabled", text="截取中…")
        self.hide_main_window_for_selection()

        def capture():
            capture_error = None
            try:
                if not engine.force_foreground(
                    self.hwnd,
                    timeout=1.0,
                    log_callback=self.log,
                ):
                    raise RuntimeError(
                        "Roblox 未能置于前台，无法保证预览图未被其他窗口遮挡"
                    )
                time.sleep(0.15)
                frame = engine.capture_window(self.hwnd)
                if frame is None:
                    raise RuntimeError("无法抓取 Roblox 客户区画面")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=88, optimize=True)
                preview_path = self.profile_store.set_preview(
                    self.active_profile_id,
                    output.getvalue(),
                    image.width,
                    image.height,
                )
                self.update_profile_status()
                self.log(f"当前方案预览已保存: {preview_path}")
            except Exception as error:
                capture_error = error
                self.log(f"截取方案预览失败: {error}")
            finally:
                self.preview_capture_in_progress = False
                self.btn_capture_preview.config(state="normal", text="截取预览")
                self.restore_main_window()
            if capture_error is not None:
                messagebox.showerror(
                    "截取预览失败",
                    str(capture_error),
                    parent=self.root,
                )

        self.root.after(300, capture)

    def view_profile_preview(self):
        try:
            profile = self.profile_store.load_profile(self.active_profile_id)
            path = self.profile_store.preview_path(self.active_profile_id)
            if not path:
                messagebox.showinfo(
                    "尚无预览",
                    "请先在角色部署完成后点击“截取预览”。",
                    parent=self.root,
                )
                return
            PreviewDialog(
                self.root,
                path,
                profile["name"],
                {
                    "background": self.bg_dark,
                    "surface": self.bg_surface,
                    "foreground": self.fg_white,
                    "muted": self.fg_gray,
                },
            )
        except Exception as error:
            messagebox.showerror("查看预览失败", str(error), parent=self.root)
            
    def test_single_step(self, step_idx):
        if not self.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 游戏窗口！")
            return
        step = self.steps[step_idx]
        key = step["cb_key"].get()
        rx, ry = step["rx"], step["ry"]
        self.log(
            f"[测试] 执行步骤 {step_idx + 1}: 按数字键 '{key}'，"
            f"移动至 ({rx:.3f}, {ry:.3f})，再用鼠标左键放置"
        )
        
        single_seq = [{"key": key, "rx": rx, "ry": ry, "delay": 0.2}]
        
        def run_test():
            ok = engine.run_action_sequence(self.hwnd, single_seq, log_callback=self.log)
            if ok:
                self.log(f"[测试] 步骤 {step_idx + 1} 的 Windows 输入序列已完整发送。")
            else:
                self.log(f"[测试] 步骤 {step_idx + 1} 未完成，请查看上方首个错误。")
        threading.Thread(target=run_test, daemon=True).start()

    def build_current_steps_sequence(self):
        """在 Tk 主线程读取当前部署设置，生成后台线程可安全使用的不可变数据。"""
        sequence = []
        for index, step in enumerate(self.steps):
            key = step["cb_key"].get()
            try:
                delay = float(step["ent_delay"].get())
            except ValueError as error:
                raise ValueError(f"步骤 {index + 1} 的间隔不是有效数字") from error
            if delay < 0:
                raise ValueError(f"步骤 {index + 1} 的间隔不能小于 0")
            sequence.append(
                {
                    "key": key,
                    "rx": float(step["rx"]),
                    "ry": float(step["ry"]),
                    "delay": delay,
                }
            )
        return sequence

    def test_all_steps(self):
        if self.running:
            messagebox.showwarning("正在挂机", "请先停止挂机，再测试部署动作。")
            return
        if not self.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 游戏窗口！")
            return
        if not self.steps:
            messagebox.showwarning("没有步骤", "请先添加至少一个部署步骤。")
            return
        try:
            sequence = self.build_current_steps_sequence()
        except ValueError as error:
            messagebox.showerror("部署设置无效", str(error))
            return

        self.btn_test_all.config(state="disabled", text="测试中…")
        self.log(
            f"[测试全部] 开始执行 {len(sequence)} 个部署动作；"
            "不会点击“开始游戏”，也不会进入挂机循环。"
        )

        def finish_test(ok):
            if self.root.winfo_exists():
                self.btn_test_all.config(state="normal", text="测试全部")
            if ok:
                self.log("[测试全部] 所有部署动作已完整发送。")
            else:
                self.log("[测试全部] 动作未完成，请查看上方首个错误。")

        result_holder = {"ok": False}

        def run_test():
            result_holder["ok"] = engine.run_action_sequence(
                self.hwnd,
                sequence,
                log_callback=self.log,
            )

        test_thread = threading.Thread(target=run_test, daemon=True)
        test_thread.start()

        def poll_test_completion():
            if test_thread.is_alive():
                self.root.after(100, poll_test_completion)
            else:
                finish_test(result_holder["ok"])

        self.root.after(100, poll_test_completion)

    # --- 挂机后台执行线程 ---
    def start_farming(self):
        if not self.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 游戏窗口！")
            return
            
        start_path = os.path.join(TEMPLATES_DIR, "start_btn.png")
        metadata_path = os.path.splitext(start_path)[0] + ".json"
        start_img = cv2.imread(start_path) if os.path.exists(start_path) else None
        template_valid = (
            start_img is not None
            and start_img.shape[1] >= 20
            and start_img.shape[0] >= 10
            and os.path.exists(metadata_path)
        )
        if not template_valid:
            messagebox.showwarning(
                "警告",
                "当前开始按钮模板是旧版、尺寸过小或没有点击锚点。\n"
                "请重新执行“截取开始游戏按钮”。",
            )
            return
            
        # 在 Tk 主线程内制作不可变快照，后台线程不再直接读取 Tk 控件。
        try:
            self.steps_snapshot = self.build_current_steps_sequence()
        except ValueError as error:
            messagebox.showerror("部署设置无效", str(error))
            return
        
        self.running = True
        self.btn_start.config(state="disabled", text="运行中…")
        self.btn_stop.config(state="normal")
        self.lbl_status.config(text="●  状态：运行中", foreground="#63cba5")
        
        self.log("▶️ 后台挂机监测启动...")
        if self.night_auto_var.get():
            self.night_dimmed_by_farming = self.apply_night_screen(automatic=True)
        self.loop_thread = threading.Thread(target=self.farming_loop, daemon=True)
        self.loop_thread.start()
        
    def stop_farming(self):
        self.running = False
        self.btn_start.config(state="normal", text="开始挂机")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="●  状态：空闲", foreground="#e6b566")
        self.log("⏸️ 后台挂机监测停止。")
        if self.display_dimmer.active_level < 100:
            self.restore_night_screen()
        
    def load_template_click_anchor(self, filename):
        """读取模板内的实际点击锚点；旧模板没有元数据时兼容使用中心点。"""
        return vision.load_template_click_anchor(TEMPLATES_DIR, filename, self.log)

    def match_template_location(
        self,
        full_img,
        template_img,
        threshold=0.85,
        click_anchor=(0.5, 0.5),
    ):
        """
        在截图中寻找特征模板，并返回模板内部预先标定的实际点击点。
        """
        return vision.match_template_location(
            full_img,
            template_img,
            threshold=threshold,
            click_anchor=click_anchor,
        )

    def analyze_template_match(
        self,
        full_img,
        template_img,
        threshold=0.85,
        click_anchor=(0.5, 0.5),
    ):
        """无论是否超过阈值，都返回最高置信度及候选位置，供诊断日志使用。"""
        return vision.analyze_template_match(
            full_img,
            template_img,
            threshold=threshold,
            click_anchor=click_anchor,
        )

    def detect_start_button_by_color(self, full_img):
        """
        用绿色填充、长宽比、屏幕占比和中心位置联合识别开始按钮。

        这是模板匹配的备用路径，用于 Roblox 在鼠标位于窗口外时按钮外观发生变化的情况。
        条件刻意设置得较严格，避免把地图、血条或底部单位 UI 当成开始按钮。
        """
        if full_img is None:
            raise ValueError("识别图像为空")
        height, width = full_img.shape[:2]
        if width < 100 or height < 100:
            return None

        hsv = cv2.cvtColor(full_img, cv2.COLOR_BGR2HSV)
        green_mask = cv2.inRange(hsv, (35, 100, 100), (95, 255, 255))
        contours, _ = cv2.findContours(
            green_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        candidates = []
        for contour in contours:
            x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
            if candidate_height <= 0:
                continue
            aspect_ratio = candidate_width / candidate_height
            width_ratio = candidate_width / width
            height_ratio = candidate_height / height
            center_x = x + candidate_width / 2
            center_y = y + candidate_height / 2
            center_rx = center_x / width
            center_ry = center_y / height
            fill_ratio = cv2.contourArea(contour) / max(
                1,
                candidate_width * candidate_height,
            )

            if not (0.15 <= width_ratio <= 0.27):
                continue
            if not (0.02 <= height_ratio <= 0.055):
                continue
            if not (6.0 <= aspect_ratio <= 10.5):
                continue
            if not (0.38 <= center_rx <= 0.62):
                continue
            if not (0.16 <= center_ry <= 0.36):
                continue
            if fill_ratio < 0.72:
                continue

            # 越接近实测按钮的中心、长宽比和填充率，分数越高。
            center_score = max(
                0.0,
                1.0 - abs(center_rx - 0.5) / 0.12 - abs(center_ry - 0.26) / 0.10,
            )
            aspect_score = max(0.0, 1.0 - abs(aspect_ratio - 8.2) / 2.2)
            score = (
                min(1.0, fill_ratio) * 0.5
                + center_score * 0.3
                + aspect_score * 0.2
            )
            candidates.append(
                {
                    "relative_x": center_x / max(1, width - 1),
                    "relative_y": center_y / max(1, height - 1),
                    "score": float(score),
                    "bbox": (x, y, candidate_width, candidate_height),
                    "fill_ratio": float(fill_ratio),
                }
            )

        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate["score"])

    def get_recognition_context(self):
        """记录光标、目标客户区和前台窗口，定位鼠标进入是否触发识别。"""
        cursor_x, cursor_y = win32gui.GetCursorPos()
        client_left, client_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        _, _, client_width, client_height = win32gui.GetClientRect(self.hwnd)
        inside = (
            client_left <= cursor_x < client_left + client_width
            and client_top <= cursor_y < client_top + client_height
        )
        return {
            "cursor_x": cursor_x,
            "cursor_y": cursor_y,
            "cursor_inside": inside,
            "foreground_hwnd": win32gui.GetForegroundWindow(),
            "client_width": client_width,
            "client_height": client_height,
        }

    def save_recognition_diagnostic(self, frame, reason):
        """每次运行最多保存 30 张关键帧，避免长期挂机无限占用磁盘。"""
        if self.diagnostic_saved_count >= 30:
            return None
        os.makedirs(DIAGNOSTICS_DIR, exist_ok=True)
        self.diagnostic_saved_count += 1
        safe_reason = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in reason
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = (
            f"{timestamp}-{self.recognition_poll_number:05d}-"
            f"{safe_reason}-{self.diagnostic_saved_count:02d}.png"
        )
        path = os.path.join(DIAGNOSTICS_DIR, filename)
        if not cv2.imwrite(path, frame):
            raise OSError(f"无法写入诊断截图: {path}")
        return path

    def monitor_roblox_resources(self):
        """低频记录 Roblox 资源；超过长期挂机阈值时只提醒一次。"""
        now = time.monotonic()
        if now - self.last_resource_check_at < 60.0:
            return
        self.last_resource_check_at = now
        usage = engine.get_process_resource_usage(self.hwnd)
        working_set_gb = usage["working_set_bytes"] / (1024 ** 3)
        private_commit_gb = usage["private_commit_bytes"] / (1024 ** 3)
        runtime_hours = usage["runtime_seconds"] / 3600

        if now - self.last_resource_log_at >= 900.0:
            self.log(
                f"[资源监控] Roblox pid={usage['pid']}, "
                f"工作集={working_set_gb:.2f}GB, 私有提交={private_commit_gb:.2f}GB, "
                f"连续运行={runtime_hours:.1f}小时"
            )
            self.last_resource_log_at = now

        if (
            not self.resource_warning_emitted
            and (runtime_hours >= 8.0 or private_commit_gb >= 4.0)
        ):
            self.resource_warning_emitted = True
            self.log(
                "[资源提醒] Roblox 已连续运行较久或私有提交接近 4GB；"
                "建议在本局结束后重启 Roblox，以释放长期累积资源。"
            )
        
    def farming_loop(self):
        start_tpl = cv2.imread(os.path.join(TEMPLATES_DIR, "start_btn.png"))
        start_click_anchor = self.load_template_click_anchor("start_btn.png")
        if start_tpl is None:
            self.log("[异常] 无法读取开始按钮模板，监测已停止。")
            self.running = False
            return
        
        # 挂机状态机：
        # 0 - 等待“开始游戏”按钮出现
        # 1 - “开始游戏”按钮已消失（表明游戏处于战斗中，等待下一局出现）
        state = 0 
        self.log("进入循环监测状态，等待匹配 '开始游戏' 按钮...")
        
        while self.running:
            try:
                self.monitor_roblox_resources()
                poll_started_at = time.perf_counter()
                capture_started_at = time.perf_counter()
                frame = engine.capture_window(self.hwnd)
                capture_ms = (time.perf_counter() - capture_started_at) * 1000
                if frame is None:
                    self.log("[警告] 无法抓取 Roblox 窗口，2秒后重试...")
                    time.sleep(2)
                    continue
                
                # 侦测当前“开始游戏”按钮是否匹配到
                match_started_at = time.perf_counter()
                match_diagnostics = self.analyze_template_match(
                    frame,
                    start_tpl,
                    threshold=0.85,
                    click_anchor=start_click_anchor,
                )
                match_result = None
                detection_method = "none"
                fallback_diagnostics = None
                if match_diagnostics["matched"]:
                    match_result = (
                        match_diagnostics["relative_x"],
                        match_diagnostics["relative_y"],
                        match_diagnostics["confidence"],
                    )
                    detection_method = "template"
                else:
                    fallback_diagnostics = self.detect_start_button_by_color(frame)
                    if fallback_diagnostics is not None:
                        match_result = (
                            fallback_diagnostics["relative_x"],
                            fallback_diagnostics["relative_y"],
                            fallback_diagnostics["score"],
                        )
                        detection_method = "green_geometry"
                match_ms = (time.perf_counter() - match_started_at) * 1000

                self.recognition_poll_number += 1
                context = self.get_recognition_context()
                total_ms = (time.perf_counter() - poll_started_at) * 1000
                state_name = "等待按钮" if state == 0 else "战斗/等待下局"
                fallback_score_text = (
                    f"{fallback_diagnostics['score']:.4f}"
                    if fallback_diagnostics is not None
                    else "n/a"
                )
                self.log(
                    f"[识别轮询 #{self.recognition_poll_number}] state={state_name}, "
                    f"matched={'yes' if match_result else 'no'}, "
                    f"method={detection_method}, "
                    f"template_confidence={match_diagnostics['confidence']:.4f}/0.8500, "
                    f"fallback_score={fallback_score_text}, "
                    f"capture={capture_ms:.1f}ms, match={match_ms:.1f}ms, "
                    f"total={total_ms:.1f}ms, "
                    f"cursor=({context['cursor_x']},{context['cursor_y']}), "
                    f"inside={'yes' if context['cursor_inside'] else 'no'}, "
                    f"foreground={context['foreground_hwnd']}, target={self.hwnd}, "
                    f"client={context['client_width']}x{context['client_height']}"
                )

                now = time.monotonic()
                cursor_changed = (
                    self.last_cursor_inside is not None
                    and context["cursor_inside"] != self.last_cursor_inside
                )
                periodic_due = now - self.last_diagnostic_save_at >= 30.0
                transition_match = state == 1 and match_result is not None
                if (
                    self.diagnostic_saved_count == 0
                    or cursor_changed
                    or periodic_due
                    or transition_match
                ):
                    reason = (
                        "transition_match"
                        if transition_match
                        else "cursor_enter"
                        if cursor_changed and context["cursor_inside"]
                        else "cursor_leave"
                        if cursor_changed
                        else "periodic"
                    )
                    saved_path = self.save_recognition_diagnostic(frame, reason)
                    if saved_path:
                        self.log(f"[识别诊断] 已保存关键帧: {saved_path}")
                        self.last_diagnostic_save_at = now
                self.last_cursor_inside = context["cursor_inside"]
                
                if state == 0:
                    if match_result:
                        rx, ry, conf = match_result
                        self.log(
                            f"🎯 侦测到 '开始游戏' 按钮! "
                            f"(方式: {detection_method}, 分数: {conf:.2f})"
                            "并自动启动动作链..."
                        )
                        
                        # 整理动作序列数据
                        steps_seq = [dict(step) for step in self.steps_snapshot]
                            
                        # 一键置顶执行动作链序列，并自动点击“开始游戏”启动波次
                        ok = engine.run_action_sequence(
                            self.hwnd,
                            steps_seq,
                            start_click_rx=rx,
                            start_click_ry=ry,
                            log_callback=self.log,
                        )
                        if ok:
                            state = 1
                            self.log("⚔️ 部署与启动命令已完整发送，等待本局结束并重载下局...")
                            time.sleep(5)
                        else:
                            self.log("动作链未完成，保持等待状态，3 秒后重新检测。")
                            time.sleep(3)
                    else:
                        time.sleep(1)
                        
                elif state == 1:
                    # 如果匹配不到“开始游戏”按钮，表明游戏仍然在进行中
                    if not match_result:
                        # 游戏中，每隔 3 秒检测一次
                        time.sleep(3)
                    else:
                        # 如果重新检测到了“开始游戏”按钮，说明游戏重新加载了（进入了下一局的图一状态）
                        self.log("检测到新的一局游戏已加载！重新进入自动部署状态...")
                        state = 0
                        time.sleep(1)
                        
            except Exception as e:
                self.log(f"[异常] 循环执行发生错误: {e}")
                time.sleep(2)

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = AEAutomationApp(root)
        root.mainloop()
    except Exception as error:
        LOGGER.exception("程序启动或主循环发生致命异常")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{error}\n\n详细信息已写入:\n{LOG_FILE}",
                "Roblox AE Automation - Error",
                0x10,
            )
        except Exception:
            pass
