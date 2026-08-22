"""Generic script editor UI (Slice 4).

A Tk editor for the Slice-3 generic action model. The model is a plain
``dict``/``list`` tree owned by :class:`GenericScriptModel` — Tk widgets are
never the source of truth, only a view/editor over it.

Layout: script list (left) | action list + breadcrumb (middle) | action params
(right), with a shared log at the bottom. Editing is backed by:

- ``script_store.ScriptStore`` for persistence (``scripts/``, separate from the
  legacy ``profiles/``).
- ``generic_script_model.GenericScriptModel`` for the in-memory tree + dirty.
- ``generic_script_model.GenericRunnerController`` for Run/Stop on a worker.

Coordinate picking and template capture reuse the app's mature overlay
primitives via ``app.pick_coordinate_generic`` / ``app.crop_template_generic``.
Recognition test always captures the Roblox frame *now* (never a cached shot).
"""

from __future__ import annotations

import copy
import os
import queue
import time

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import cv2

from script_store import ScriptStore, ScriptStoreError
from generic_script_model import GenericScriptModel, GenericRunnerController


_ACTION_TEMPLATES = {
    "key": {"type": "key", "key": "1", "hold_seconds": 0.06},
    "click": {"type": "click", "x": 0.5, "y": 0.5},
    "wait": {"type": "wait", "seconds": 0.5},
    "find_image": {"type": "find_image", "template": "", "threshold": 0.85},
    "click_image": {"type": "click_image", "template": "", "threshold": 0.85},
    "if_image": {"type": "if_image", "template": "", "threshold": 0.85, "then": [], "else": []},
    "repeat": {"type": "repeat", "count": 1, "actions": []},
}


class GenericScriptUI(tk.Frame):
    def __init__(self, parent, app, scripts_dir):
        super().__init__(parent)
        self.app = app
        self.store = ScriptStore(scripts_dir)
        self.model = GenericScriptModel(self.store)
        self.controller = GenericRunnerController()

        self._form_entries = {}   # field key -> (widget, parse_kind)
        self._form_path = None    # path of the action currently being edited
        self._current_list_path = []   # path to the action list being shown

        self._colors = {
            "bg_dark": getattr(app, "bg_dark", "#1e1e2e"),
            "bg_surface": getattr(app, "bg_surface", "#27273a"),
            "bg_input": getattr(app, "bg_input", "#1c1c2a"),
            "fg_white": getattr(app, "fg_white", "#e6e6f0"),
            "fg_gray": getattr(app, "fg_gray", "#9a9ab0"),
            "fg_dim": getattr(app, "fg_dim", "#6a6a80"),
            "accent": getattr(app, "btn_primary", "#43a982"),
            "danger": getattr(app, "btn_danger", "#d06464"),
        }

        self._build_layout()
        self._refresh_script_list()
        self._refresh_action_list()
        self.after(100, self._drain_logs)

    # ---------------------------------------------------------------- logging
    def _log(self, msg):
        self.controller.log_queue.put(msg)

    def _drain_logs(self):
        while not self.controller.log_queue.empty():
            msg = self.controller.log_queue.get()
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self.after(100, self._drain_logs)

    # ---------------------------------------------------------------- layout
    def _build_layout(self):
        c = self._colors
        # toolbar
        toolbar = tk.Frame(self, bg=c["bg_surface"])
        toolbar.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(toolbar, text="脚本:", bg=c["bg_surface"], fg=c["fg_gray"]).pack(side="left")
        self.cb_scripts = ttk.Combobox(toolbar, state="readonly", width=24)
        self.cb_scripts.pack(side="left", padx=(4, 10))
        self.cb_scripts.bind("<<ComboboxSelected>>", self._on_script_select)

        for text, cmd in [
            ("新建", self._new_script), ("保存", self._save_script),
            ("另存", self._save_script_as), ("重命名", self._rename_script),
            ("删除", self._delete_script),
        ]:
            self._tool_btn(toolbar, text, cmd).pack(side="left", padx=2)

        self.btn_run = self._tool_btn(toolbar, "▶ 运行", self._run_script, "accent")
        self.btn_run.pack(side="right", padx=2)
        self.btn_stop = self._tool_btn(toolbar, "■ 停止", self._stop_script, "danger")
        self.btn_stop.pack(side="right", padx=2)

        self.lbl_state = tk.Label(toolbar, text="", bg=c["bg_surface"], fg=c["fg_gray"])
        self.lbl_state.pack(side="right", padx=10)

        # three-column body
        body = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=c["bg_dark"], bd=0, sashwidth=6)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = tk.Frame(body, bg=c["bg_dark"])
        middle = tk.Frame(body, bg=c["bg_dark"])
        right = tk.Frame(body, bg=c["bg_dark"])
        body.add(left, minsize=180, width=200, stretch="never")
        body.add(middle, minsize=260, width=320, stretch="always")
        body.add(right, minsize=260, width=320, stretch="always")

        # left: script list
        tk.Label(left, text="脚本列表", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.lb_scripts = tk.Listbox(left, bg=c["bg_input"], fg=c["fg_white"],
                                     selectbackground=c["accent"], relief="flat",
                                     highlightthickness=0, exportselection=False)
        self.lb_scripts.pack(fill="both", expand=True)
        self.lb_scripts.bind("<<ListboxSelect>>", self._on_script_list_select)

        # middle: breadcrumb + action list + buttons
        self.lbl_breadcrumb = tk.Label(middle, text="动作（根）", bg=c["bg_dark"],
                                       fg=c["fg_gray"], anchor="w")
        self.lbl_breadcrumb.pack(fill="x", pady=(0, 4))

        self.lb_actions = tk.Listbox(middle, bg=c["bg_input"], fg=c["fg_white"],
                                     selectbackground=c["accent"], relief="flat",
                                     highlightthickness=0, exportselection=False)
        self.lb_actions.pack(fill="both", expand=True)
        self.lb_actions.bind("<<ListboxSelect>>", self._on_action_select)
        self.lb_actions.bind("<Double-1>", self._on_action_double)

        act_btns = tk.Frame(middle, bg=c["bg_dark"])
        act_btns.pack(fill="x", pady=(6, 0))
        self._tool_btn(act_btns, "＋添加", self._add_action_menu).pack(side="left", padx=2)
        self._tool_btn(act_btns, "删除", self._delete_action).pack(side="left", padx=2)
        self._tool_btn(act_btns, "↑", lambda: self._move_action(-1)).pack(side="left", padx=2)
        self._tool_btn(act_btns, "↓", lambda: self._move_action(1)).pack(side="left", padx=2)
        self._tool_btn(act_btns, "复制", self._duplicate_action).pack(side="left", padx=2)
        self._tool_btn(act_btns, "编辑内部", self._edit_nested).pack(side="left", padx=2)
        self._tool_btn(act_btns, "◀ 上一级", self._go_up).pack(side="right", padx=2)

        # right: params
        tk.Label(right, text="动作参数", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.form_frame = tk.Frame(right, bg=c["bg_surface"])
        self.form_frame.pack(fill="both", expand=True)

        # bottom: log
        log_frame = tk.Frame(self, bg=c["bg_dark"])
        log_frame.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(log_frame, text="运行日志", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.txt_log = tk.Text(log_frame, height=6, bg=c["bg_input"], fg=c["fg_white"],
                               relief="flat", highlightthickness=0, wrap="none",
                               state="disabled", font=("Cascadia Mono", 9))
        self.txt_log.pack(fill="both", expand=True)

    def _tool_btn(self, parent, text, command, variant=None):
        c = self._colors
        bg = c["accent"] if variant == "accent" else (c["danger"] if variant == "danger" else c["bg_input"])
        fg = c["fg_white"]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg, relief="flat",
                         bd=0, padx=10, pady=4, cursor="hand2",
                         font=("Microsoft YaHei UI", 9))

    # ---------------------------------------------------------------- status
    def _refresh_status(self):
        if self.controller.is_running():
            self.lbl_state.config(text="● 运行中", fg="#e6b566")
        elif self.model.dirty:
            self.lbl_state.config(text="● 未保存", fg="#e6b566")
        else:
            self.lbl_state.config(text="● 已保存", fg="#63cba5")

    # ---------------------------------------------------------------- scripts
    def _refresh_script_list(self):
        scripts = self.store.list_scripts()
        names = [s["name"] for s in scripts]
        self.cb_scripts["values"] = names
        self.lb_scripts.delete(0, "end")
        for name in names:
            self.lb_scripts.insert("end", name)
        if self.model.script_id:
            try:
                cur = self.store.load_script(self.model.script_id)
                self.cb_scripts.set(cur["name"])
                return
            except ScriptStoreError:
                pass
        if names:
            self.cb_scripts.set(names[0])
        else:
            self.cb_scripts.set("")

    def _on_script_list_select(self, event=None):
        idx = self.lb_scripts.curselection()
        if not idx:
            return
        name = self.lb_scripts.get(idx[0])
        for s in self.store.list_scripts():
            if s["name"] == name:
                self._switch_to(s["id"])
                return

    def _on_script_select(self, event=None):
        name = self.cb_scripts.get()
        for s in self.store.list_scripts():
            if s["name"] == name:
                self._switch_to(s["id"])
                return

    def _switch_to(self, script_id):
        if not self._resolve_unsaved("切换脚本", "切换后当前未保存的修改将被放弃。"):
            self._refresh_script_list()
            return
        self.model.load(script_id)
        self._current_list_path = []
        self._refresh_script_list()
        self._refresh_action_list()
        self._clear_form()
        self._refresh_status()
        self._log(f"已载入脚本“{self.model.name}”。")

    def _new_script(self):
        if not self._resolve_unsaved("新建脚本", "新建后当前未保存的修改将被放弃。"):
            return
        name = simpledialog.askstring("新建脚本", "输入脚本名称：", parent=self)
        if name is None:
            return
        try:
            self.model.new(name)
            self._current_list_path = []
            self._refresh_script_list()
            self._refresh_action_list()
            self._clear_form()
            self._refresh_status()
            self._log(f"已新建脚本“{name}”（尚未保存）。")
        except ScriptStoreError as e:
            messagebox.showerror("新建失败", str(e), parent=self)

    def _save_script(self):
        try:
            if self.model.script_id is None:
                if not self.model.name:
                    name = simpledialog.askstring("保存脚本", "输入脚本名称：", parent=self)
                    if not name:
                        return
                    self.model.name = name
            script = self.model.save()
            self._refresh_script_list()
            self._refresh_status()
            self._log(f"脚本“{script['name']}”已保存。")
            return True
        except ScriptStoreError as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return False

    def _save_script_as(self):
        name = simpledialog.askstring("另存为", "输入新脚本名称：", parent=self)
        if not name:
            return
        try:
            script = self.store.create_script(name, self.model.actions)
            self.model.script_id = script["id"]
            self.model.name = script["name"]
            self.model.dirty = False
            self._refresh_script_list()
            self._refresh_status()
            self._log(f"已另存为“{script['name']}”。")
        except ScriptStoreError as e:
            messagebox.showerror("另存为失败", str(e), parent=self)

    def _rename_script(self):
        if self.model.script_id is None:
            messagebox.showinfo("提示", "请先保存脚本后再重命名。", parent=self)
            return
        name = simpledialog.askstring("重命名脚本", "输入新名称：",
                                      initialvalue=self.model.name, parent=self)
        if not name or name == self.model.name:
            return
        try:
            script = self.store.rename_script(self.model.script_id, name)
            self.model.name = script["name"]
            self._refresh_script_list()
            self._refresh_status()
            self._log(f"脚本已重命名为“{script['name']}”。")
        except ScriptStoreError as e:
            messagebox.showerror("重命名失败", str(e), parent=self)

    def _delete_script(self):
        if self.model.script_id is None:
            messagebox.showinfo("提示", "当前脚本尚未保存，无需删除。", parent=self)
            return
        if not messagebox.askyesno("删除脚本", f"确定删除脚本“{self.model.name}”？", parent=self):
            return
        try:
            self.model.delete()
            self._current_list_path = []
            self._refresh_script_list()
            self._refresh_action_list()
            self._clear_form()
            self._refresh_status()
            self._log("脚本已删除。")
        except ScriptStoreError as e:
            messagebox.showerror("删除失败", str(e), parent=self)

    def _resolve_unsaved(self, title, message):
        if not self.model.dirty:
            return True
        decision = messagebox.askyesnocancel(title, f"{message}\n\n是否先保存当前脚本？", parent=self)
        if decision is None:
            return False
        if decision:
            return self._save_script()
        return True

    # ---------------------------------------------------------------- actions
    def _current_list(self):
        return self.model.get_list(self._current_list_path)

    def _selected_action_path(self):
        sel = self.lb_actions.curselection()
        if not sel:
            return None
        return self._current_list_path + [sel[0]]

    def _refresh_action_list(self):
        self.lb_actions.delete(0, "end")
        for act in self._current_list():
            self.lb_actions.insert("end", self._action_summary(act))
        # breadcrumb
        parts = ["根"]
        for i, step in enumerate(self._current_list_path):
            if isinstance(step, int):
                continue
            parts.append(str(step))
        self.lbl_breadcrumb.config(text="动作  " + " > ".join(parts))

    def _action_summary(self, act):
        t = act.get("type", "?")
        if t == "key":
            return f"按键  {act.get('key','')}  按住{act.get('hold_seconds',0.06)}s"
        if t == "click":
            return f"点击  ({act.get('x',0):.3f}, {act.get('y',0):.3f})"
        if t == "wait":
            return f"等待  {act.get('seconds',0)}s"
        if t == "find_image":
            return f"找图  {act.get('template','(未选)')}"
        if t == "click_image":
            return f"点击图  {act.get('template','(未选)')}"
        if t == "if_image":
            return f"如果图  {act.get('template','(未选)')}  → then {len(act.get('then',[]))} / else {len(act.get('else',[]))}"
        if t == "repeat":
            if act.get("forever"):
                return f"重复  一直 (内部 {len(act.get('actions',[]))} 个动作)"
            return f"重复  {act.get('count',1)} 次 (内部 {len(act.get('actions',[]))} 个动作)"
        return f"未知  {t}"

    def _add_action_menu(self):
        menu = tk.Menu(self, tearoff=0)
        sub_keyboard = tk.Menu(menu, tearoff=0)
        sub_keyboard.add_command(label="按键", command=lambda: self._add_action("key"))
        sub_mouse = tk.Menu(menu, tearoff=0)
        sub_mouse.add_command(label="点击坐标", command=lambda: self._add_action("click"))
        sub_flow = tk.Menu(menu, tearoff=0)
        sub_flow.add_command(label="等待", command=lambda: self._add_action("wait"))
        sub_flow.add_command(label="如果图片", command=lambda: self._add_action("if_image"))
        sub_flow.add_command(label="重复", command=lambda: self._add_action("repeat"))
        sub_vision = tk.Menu(menu, tearoff=0)
        sub_vision.add_command(label="找图片", command=lambda: self._add_action("find_image"))
        sub_vision.add_command(label="点击图片", command=lambda: self._add_action("click_image"))
        menu.add_cascade(label="键盘", menu=sub_keyboard)
        menu.add_cascade(label="鼠标", menu=sub_mouse)
        menu.add_cascade(label="流程", menu=sub_flow)
        menu.add_cascade(label="视觉", menu=sub_vision)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _add_action(self, atype):
        action = copy.deepcopy(_ACTION_TEMPLATES[atype])
        sel = self.lb_actions.curselection()
        index = sel[0] + 1 if sel else None
        self.model.insert_action(self._current_list_path, action, index)
        self._refresh_action_list()
        if index is None:
            index = len(self._current_list()) - 1
        self.lb_actions.selection_clear(0, "end")
        self.lb_actions.selection_set(index)
        self._on_action_select()
        self._refresh_status()

    def _delete_action(self):
        path = self._selected_action_path()
        if path is None:
            return
        self.model.remove_action(path)
        self._refresh_action_list()
        self._clear_form()
        self._refresh_status()

    def _move_action(self, delta):
        path = self._selected_action_path()
        if path is None:
            return
        idx = path[-1]
        self.model.move_action(path, delta)
        self._refresh_action_list()
        new_idx = min(max(0, idx + delta), len(self._current_list()) - 1)
        self.lb_actions.selection_clear(0, "end")
        self.lb_actions.selection_set(new_idx)
        self._refresh_status()

    def _duplicate_action(self):
        path = self._selected_action_path()
        if path is None:
            return
        self.model.duplicate_action(path)
        self._refresh_action_list()
        self._refresh_status()

    def _go_up(self):
        if not self._current_list_path:
            return
        self._current_list_path = self._current_list_path[:-1]
        if self._current_list_path and isinstance(self._current_list_path[-1], str):
            self._current_list_path = self._current_list_path[:-1]
        self._refresh_action_list()
        self._clear_form()

    def _edit_nested(self):
        path = self._selected_action_path()
        if path is None:
            return
        action = self.model.get_action(path)
        children = GenericScriptModel.child_lists(action)
        if not children:
            messagebox.showinfo("提示", "该动作没有可编辑的内部动作。", parent=self)
            return
        keys = list(children.keys())
        if len(keys) == 1:
            self._current_list_path = path + [keys[0]]
            self._refresh_action_list()
            self._clear_form()
            return
        # if_image: choose then/else
        choice = simpledialog.askstring(
            "编辑内部", "输入要编辑的分支 (then / else)：",
            initialvalue="then", parent=self,
        )
        if choice in ("then", "else"):
            self._current_list_path = path + [choice]
            self._refresh_action_list()
            self._clear_form()

    def _on_action_double(self, event):
        path = self._selected_action_path()
        if path is None:
            return
        action = self.model.get_action(path)
        if GenericScriptModel.child_lists(action):
            self._edit_nested()

    # ---------------------------------------------------------------- form
    def _clear_form(self):
        for child in self.form_frame.winfo_children():
            child.destroy()
        self._form_entries = {}
        self._form_path = None

    def _on_action_select(self, event=None):
        path = self._selected_action_path()
        if path is None:
            self._clear_form()
            return
        self._build_form(path)

    def _build_form(self, path):
        self._clear_form()
        action = self.model.get_action(path)
        self._form_path = path
        c = self._colors

        t = action.get("type", "?")
        tk.Label(self.form_frame, text=f"动作类型：{t}", bg=c["bg_surface"],
                 fg=c["fg_white"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        def row(label, key, value, parse="str"):
            fr = tk.Frame(self.form_frame, bg=c["bg_surface"])
            fr.pack(fill="x", padx=14, pady=4)
            tk.Label(fr, text=label, bg=c["bg_surface"], fg=c["fg_gray"], width=12,
                     anchor="w").pack(side="left")
            var = tk.StringVar(value=str(value))
            entry = tk.Entry(fr, textvariable=var, bg=c["bg_input"], fg=c["fg_white"],
                             insertbackground=c["fg_white"], relief="flat", width=22)
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<FocusOut>", self._apply_form)
            entry.bind("<Return>", self._apply_form)
            self._form_entries[key] = (entry, parse, var)

        if t == "key":
            row("按键 key", "key", action.get("key", ""))
            row("按住(秒)", "hold_seconds", action.get("hold_seconds", 0.06), "float")
            self._form_btn("测试此按键", lambda: self._test_action(action))
        elif t == "click":
            row("X", "x", action.get("x", 0.5), "float")
            row("Y", "y", action.get("y", 0.5), "float")
            self._form_btn("从 Roblox 选择坐标", lambda: self._pick_coordinate(action))
            self._form_btn("测试此点击", lambda: self._test_action(action))
        elif t == "wait":
            row("等待(秒)", "seconds", action.get("seconds", 0.5), "float")
        elif t in ("find_image", "click_image", "if_image"):
            row("模板 template", "template", action.get("template", ""))
            row("阈值 threshold", "threshold", action.get("threshold", 0.85), "float")
            self._form_btn("截取模板", lambda: self._crop_template(action))
            self._form_btn("测试识别", lambda: self._test_recognition(action))
            if t == "if_image":
                self._form_btn("编辑 Then / Else", self._edit_nested)
        elif t == "repeat":
            forever = bool(action.get("forever", False))
            self._forever_var = tk.BooleanVar(value=forever)
            def _toggle():
                action["forever"] = self._forever_var.get()
                if action.get("forever"):
                    action.pop("count", None)
                else:
                    action.setdefault("count", 1)
                self.model.mark_dirty()
                self._refresh_status()
                self._refresh_action_list()
            cb = tk.Checkbutton(self.form_frame, text="一直重复 (直到停止)",
                                variable=self._forever_var, command=_toggle,
                                bg=c["bg_surface"], fg=c["fg_white"],
                                selectcolor=c["bg_input"], activebackground=c["bg_surface"])
            cb.pack(anchor="w", padx=14, pady=6)
            if not forever:
                row("重复次数", "count", action.get("count", 1), "int")
            self._form_btn("编辑内部动作", self._edit_nested)

    def _form_btn(self, text, command):
        c = self._colors
        btn = tk.Button(self.form_frame, text=text, command=command, bg=c["bg_input"],
                        fg=c["fg_white"], activebackground=c["bg_input"],
                        activeforeground=c["fg_white"], relief="flat", bd=0,
                        padx=10, pady=5, cursor="hand2")
        btn.pack(anchor="w", padx=14, pady=4)
        return btn

    def _apply_form(self, event=None):
        if self._form_path is None:
            return
        action = self.model.get_action(self._form_path)
        changed = False
        for key, (entry, parse, var) in self._form_entries.items():
            raw = var.get()
            try:
                if parse == "float":
                    val = float(raw)
                elif parse == "int":
                    val = int(float(raw))
                else:
                    val = raw.strip()
                if action.get(key) != val:
                    action[key] = val
                    changed = True
            except ValueError:
                self._log(f"字段 {key} 的值无效，已忽略: {raw!r}")
        if changed:
            self.model.mark_dirty()
            self._refresh_action_list()
            self._refresh_status()

    # ---------------------------------------------------------------- live test
    def _test_action(self, action):
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先在 AE 部署页选择目标 Roblox 窗口！", parent=self)
            return
        try:
            ok = self.model.test_input(hwnd, action)
            self._log(f"动作测试结果: {'成功' if ok else '失败'}（{action.get('type')}）")
        except Exception as e:
            self._log(f"动作测试异常: {e}")

    def _pick_coordinate(self, action):
        if not self.app.hwnd:
            messagebox.showwarning("警告", "请先在 AE 部署页选择目标 Roblox 窗口！", parent=self)
            return

        def on_pick(rx, ry):
            action["x"] = rx
            action["y"] = ry
            self.model.mark_dirty()
            self._refresh_action_list()
            self._refresh_status()
            self._build_form(self._form_path)

        self.app.pick_coordinate_generic(on_pick)

    def _crop_template(self, action):
        if self.model.script_id is None:
            messagebox.showinfo("提示", "请先保存脚本，再截取模板（模板保存到脚本的 assets 目录）。", parent=self)
            self._save_script()
            if self.model.script_id is None:
                return

        def on_crop(cropped, offset_x, offset_y):
            name = simpledialog.askstring("保存模板", "输入模板文件名（例如 start.png）：", parent=self)
            if not name:
                return
            if not name.lower().endswith(".png"):
                name += ".png"
            rel = self.model.template_rel_path(name)
            abs_path = os.path.join(self.model.store.script_dir(self.model.script_id), rel)
            try:
                cv2.imwrite(abs_path, cropped)
                # sidecar click-anchor metadata (same schema as Slice-1 vision)
                import json
                meta = {
                    "version": 1,
                    "click_offset_x": round(offset_x, 4),
                    "click_offset_y": round(offset_y, 4),
                    "template_width": int(cropped.shape[1]),
                    "template_height": int(cropped.shape[0]),
                }
                with open(os.path.splitext(abs_path)[0] + ".json", "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                action["template"] = rel
                self.model.mark_dirty()
                self._refresh_action_list()
                self._refresh_status()
                self._build_form(self._form_path)
                self._log(f"模板已保存: {rel}，点击锚点=({offset_x:.4f},{offset_y:.4f})")
            except Exception as e:
                self._log(f"模板保存失败: {e}")
                messagebox.showerror("模板保存失败", str(e), parent=self)

        self.app.crop_template_generic(on_crop)

    def _test_recognition(self, action):
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先在 AE 部署页选择目标 Roblox 窗口！", parent=self)
            return
        template = action.get("template", "")
        if not template:
            messagebox.showinfo("提示", "请先截取或选择模板。", parent=self)
            return
        try:
            diag = self.model.test_find_image(hwnd, template, float(action.get("threshold", 0.85)))
            status = "FOUND" if diag["matched"] else "NOT FOUND"
            self._log(f"测试识别 [{status}] 模板={template} "
                      f"confidence={diag['confidence']:.4f} "
                      f"position=({diag['relative_x']:.4f},{diag['relative_y']:.4f})")
            messagebox.showinfo(
                "测试识别",
                f"模板: {template}\n结果: {status}\n置信度: {diag['confidence']:.4f}\n"
                f"位置: ({diag['relative_x']:.4f}, {diag['relative_y']:.4f})",
                parent=self,
            )
        except Exception as e:
            self._log(f"测试识别异常: {e}")
            messagebox.showerror("测试识别失败", str(e), parent=self)

    # ---------------------------------------------------------------- run / stop
    def _run_script(self):
        if self.controller.is_running():
            return
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先在 AE 部署页选择目标 Roblox 窗口！", parent=self)
            return
        # flush any in-progress form edit
        self._apply_form()
        if self.model.script_id is None or self.model.dirty:
            if not self._save_script():
                return
        base_dir = self.model.store.script_dir(self.model.script_id)
        actions = copy.deepcopy(self.model.actions)
        self.controller.start(hwnd, actions, base_dir)
        self._log(f"开始运行脚本“{self.model.name}”（{len(actions)} 个动作）。")
        self._refresh_status()

    def _stop_script(self):
        if not self.controller.is_running():
            return
        self.controller.stop()
        self._log("已请求停止脚本（等待当前短输入会话收尾）。")
        self._refresh_status()

    def on_close(self):
        """Return True if close may proceed; False cancels it."""
        if self.controller.is_running():
            self.controller.stop()
        if not self._resolve_unsaved("关闭程序", "当前通用脚本有未保存的修改。"):
            return False
        return True
