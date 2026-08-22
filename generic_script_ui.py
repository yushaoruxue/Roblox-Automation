"""Generic script editor UI (Slice 4R — product-layer redesign).

The GUI edits *user actions* (Layer 2), never the execution primitives (Layer 1).
``user_actions.compile_user_actions`` lowers them to ``script_runner`` actions at
run time. Layout is a three-pane editor:

    left   = action library (categorized by user goal)
    middle = flow tree (Treeview, nested if_image / repeat / group)
    right  = dynamic properties + template preview + last recognition result

Persistence reuses ``script_store.ScriptStore``; dirty tracking, the
run/stop worker bridge, and the legacy coordinate picker / template crop are
kept from Slice 4. Recognition test always captures the Roblox frame *now*.
"""

from __future__ import annotations

import copy
import json
import os
import time

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import cv2
from PIL import Image, ImageTk

import engine
import user_actions
from script_store import ScriptStore, ScriptStoreError
from generic_script_model import GenericScriptModel, GenericRunnerController


class GenericScriptUI(tk.Frame):
    def __init__(self, parent, app, scripts_dir):
        super().__init__(parent)
        self.app = app
        self.store = ScriptStore(scripts_dir)
        self.model = GenericScriptModel(self.store)
        self.controller = GenericRunnerController()

        self._form_entries = {}   # field key -> (entry_widget, parse_kind, var)
        self._form_path = None    # path of the action being edited
        self._tree_paths = {}     # tree iid -> action/list path (tuple)
        self._iid_counter = 0
        self._log_visible = True

        c = {
            "bg_dark": getattr(app, "bg_dark", "#1e1e2e"),
            "bg_surface": getattr(app, "bg_surface", "#27273a"),
            "bg_input": getattr(app, "bg_input", "#1c1c2a"),
            "fg_white": getattr(app, "fg_white", "#e6e6f0"),
            "fg_gray": getattr(app, "fg_gray", "#9a9ab0"),
            "fg_dim": getattr(app, "fg_dim", "#6a6a80"),
            "accent": getattr(app, "btn_primary", "#43a982"),
            "danger": getattr(app, "btn_danger", "#d06464"),
        }
        self._c = c

        # ttk.Treeview 暗色主题（与 AE 风格一致，去掉默认白底）
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background=c["bg_input"],
            foreground=c["fg_white"],
            fieldbackground=c["bg_input"],
            rowheight=26,
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", c["accent"])],
            foreground=[("selected", c["fg_white"])],
        )
        style.configure(
            "Treeview.Heading",
            background=c["bg_surface"],
            foreground=c["fg_white"],
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        self._build_layout()
        self._refresh_script_list()
        self._rebuild_tree()
        self.after(100, self._drain_logs)

    # ------------------------------------------------------------- helpers
    def _log(self, msg):
        self.controller.log_queue.put(msg)

    def _btn(self, parent, text, cmd, variant=None, small=False):
        c = self._c
        bg = {"accent": c["accent"], "danger": c["danger"]}.get(variant, c["bg_input"])
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=c["fg_white"],
                         activebackground=bg, activeforeground=c["fg_white"],
                         relief="flat", bd=0, cursor="hand2",
                         padx=(6 if small else 10), pady=(2 if small else 4),
                         font=("Microsoft YaHei UI", 8 if small else 9))

    def _drain_logs(self):
        while not self.controller.log_queue.empty():
            msg = self.controller.log_queue.get()
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self.after(100, self._drain_logs)

    # ------------------------------------------------------------- layout
    def _build_layout(self):
        c = self._c
        # toolbar
        bar = tk.Frame(self, bg=c["bg_surface"])
        bar.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(bar, text="Roblox:", bg=c["bg_surface"], fg=c["fg_gray"]).pack(side="left")
        self.lbl_roblox = tk.Label(bar, text="（未选择）", bg=c["bg_surface"],
                                   fg=c["fg_white"])
        self.lbl_roblox.pack(side="left", padx=(2, 10))
        self._btn(bar, "刷新", self._refresh_roblox, small=True).pack(side="left")

        tk.Label(bar, text="脚本:", bg=c["bg_surface"], fg=c["fg_gray"]).pack(side="left", padx=(8, 0))
        self.cb_scripts = ttk.Combobox(bar, state="readonly", width=22)
        self.cb_scripts.pack(side="left", padx=(4, 8))
        self.cb_scripts.bind("<<ComboboxSelected>>", self._on_script_select)

        for text, cmd in [("新建", self._new_script), ("保存", self._save_script),
                          ("重命名", self._rename_script), ("删除", self._delete_script)]:
            self._btn(bar, text, cmd, small=True).pack(side="left", padx=2)

        self.btn_stop = self._btn(bar, "■ 停止", self._stop_script, "danger", small=True)
        self.btn_stop.pack(side="right", padx=2)
        self.btn_run = self._btn(bar, "▶ 运行", self._run_script, "accent", small=True)
        self.btn_run.pack(side="right", padx=2)
        self.lbl_state = tk.Label(bar, text="", bg=c["bg_surface"], fg=c["fg_gray"])
        self.lbl_state.pack(side="right", padx=8)

        # three panes
        panes = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=c["bg_dark"], bd=0, sashwidth=6)
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        left = tk.Frame(panes, bg=c["bg_dark"])
        middle = tk.Frame(panes, bg=c["bg_dark"])
        right = tk.Frame(panes, bg=c["bg_dark"])
        panes.add(left, minsize=150, width=180, stretch="never")
        panes.add(middle, minsize=320, width=480, stretch="always")
        panes.add(right, minsize=260, width=330, stretch="always")

        self._build_library(left)
        self._build_flow(middle)
        self._build_properties(right)

        # collapsible log
        log_head = tk.Frame(self, bg=c["bg_dark"])
        log_head.pack(fill="x", padx=10)
        self.btn_log_toggle = tk.Button(log_head, text="▾ 运行日志", command=self._toggle_log,
                                        bg=c["bg_dark"], fg=c["fg_white"], relief="flat",
                                        bd=0, cursor="hand2", font=("Microsoft YaHei UI", 9, "bold"))
        self.btn_log_toggle.pack(side="left")
        self.log_frame = tk.Frame(self, bg=c["bg_dark"])
        self.log_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.txt_log = tk.Text(self.log_frame, height=6, bg=c["bg_input"], fg=c["fg_white"],
                               relief="flat", highlightthickness=0, wrap="none",
                               state="disabled", font=("Cascadia Mono", 9))
        self.txt_log.pack(fill="both", expand=True)

    def _build_library(self, parent):
        c = self._c
        tk.Label(parent, text="动作库", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.lib_tree = ttk.Treeview(parent, show="tree")
        self.lib_tree.pack(fill="both", expand=True)
        for category, items in user_actions.ACTION_LIBRARY.items():
            cat_iid = self.lib_tree.insert("", "end", text=category, open=True)
            for label, atype in items:
                self.lib_tree.insert(cat_iid, "end", text=label, values=(atype,))
        self.lib_tree.bind("<Double-1>", self._on_library_double)

    def _build_flow(self, parent):
        c = self._c
        tk.Label(parent, text="脚本流程", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.tree = ttk.Treeview(parent, selectmode="browse")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double)

        btns = tk.Frame(parent, bg=c["bg_dark"])
        btns.pack(fill="x", pady=(6, 0))
        self._btn(btns, "删除", self._delete_action, small=True).pack(side="left", padx=2)
        self._btn(btns, "复制", self._duplicate_action, small=True).pack(side="left", padx=2)
        self._btn(btns, "↑", lambda: self._move_action(-1), small=True).pack(side="left", padx=2)
        self._btn(btns, "↓", lambda: self._move_action(1), small=True).pack(side="left", padx=2)

    def _build_properties(self, parent):
        c = self._c
        tk.Label(parent, text="属性 / 视觉", bg=c["bg_dark"], fg=c["fg_white"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.form_frame = tk.Frame(parent, bg=c["bg_surface"])
        self.form_frame.pack(fill="both", expand=True)

    def _toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_frame.pack(fill="x", padx=10, pady=(0, 10))
            self.btn_log_toggle.config(text="▾ 运行日志")
        else:
            self.log_frame.pack_forget()
            self.btn_log_toggle.config(text="▸ 运行日志")

    # ------------------------------------------------------------- status
    def _refresh_status(self):
        if self.controller.is_running():
            self.lbl_state.config(text="● 运行中", fg="#e6b566")
        elif self.model.dirty:
            self.lbl_state.config(text="● 未保存", fg="#e6b566")
        else:
            self.lbl_state.config(text="● 已保存", fg="#63cba5")

    def _refresh_roblox(self):
        h = engine.find_roblox_hwnd()
        if h:
            self.app.hwnd = h[0][0]
            self.lbl_roblox.config(text=h[0][1])
        else:
            self.app.hwnd = None
            self.lbl_roblox.config(text="（未检测到）")
        self.lbl_roblox.config(fg=self._c["fg_white"] if self.app.hwnd else self._c["danger"])

    # ------------------------------------------------------------- scripts
    def _refresh_script_list(self):
        names = [s["name"] for s in self.store.list_scripts()]
        self.cb_scripts["values"] = names
        if self.model.script_id:
            try:
                self.cb_scripts.set(self.store.load_script(self.model.script_id)["name"])
                return
            except ScriptStoreError:
                pass
        self.cb_scripts.set(names[0] if names else "")

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
        self._refresh_script_list()
        self._rebuild_tree()
        self._clear_form()
        self._refresh_status()
        self._log(f"已载入脚本“{self.model.name}”。")

    def _new_script(self):
        if not self._resolve_unsaved("新建脚本", "新建后当前未保存的修改将被放弃。"):
            return
        name = simpledialog.askstring("新建脚本", "输入脚本名称：", parent=self)
        if not name:
            return
        self.model.new(name)
        self._refresh_script_list()
        self._rebuild_tree()
        self._clear_form()
        self._refresh_status()
        self._log(f"已新建脚本“{name}”（尚未保存）。")

    def _save_script(self):
        try:
            if self.model.script_id is None and not self.model.name:
                # 未命名新脚本自动生成名字，不再要求用户手动输入
                existing = {s["name"] for s in self.store.list_scripts()}
                base = "脚本"
                n = 1
                while f"{base}_{n}" in existing:
                    n += 1
                self.model.name = f"{base}_{n}"
            self.model.validate()
            script = self.model.save()
            self._refresh_script_list()
            self._refresh_status()
            self._log(f"脚本“{script['name']}”已保存。")
            return True
        except (ValueError, ScriptStoreError) as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return False

    def _rename_script(self):
        if self.model.script_id is None:
            messagebox.showinfo("提示", "请先保存脚本后再重命名。", parent=self)
            return
        name = simpledialog.askstring("重命名脚本", "输入新名称：",
                                      initialvalue=self.model.name, parent=self)
        if not name or name == self.model.name:
            return
        try:
            self.model.name = self.store.rename_script(self.model.script_id, name)["name"]
            self._refresh_script_list()
            self._refresh_status()
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
            self._refresh_script_list()
            self._rebuild_tree()
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

    # ------------------------------------------------------------- flow tree
    def _make_iid(self):
        self._iid_counter += 1
        return f"n{self._iid_counter}"

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_paths = {}
        self._path_iids = {}
        self._iid_counter = 0
        for i, act in enumerate(self.model.actions):
            self._insert_node("", [i], act)

    def _insert_node(self, parent_iid, path, act):
        iid = self._make_iid()
        self.tree.insert(parent_iid, "end", iid=iid, text=user_actions.action_summary(act))
        tpath = tuple(path)
        self._tree_paths[iid] = tpath
        self._path_iids[tpath] = iid
        children = user_actions.child_container(act)
        if act.get("type") == "if_image":
            for key in ("then", "else"):
                branch_iid = self._make_iid()
                self.tree.insert(iid, "end", iid=branch_iid, text=key.upper())
                bpath = tuple(path + [key])
                self._tree_paths[branch_iid] = bpath
                self._path_iids[bpath] = branch_iid
                for j, child in enumerate(act.get(key, [])):
                    self._insert_node(branch_iid, path + [key, j], child)
        elif "actions" in children:
            for j, child in enumerate(act.get("actions", [])):
                self._insert_node(iid, path + ["actions", j], child)

    def _reselect_path(self, path):
        """重新选中路径对应的节点（重建 tree 后恢复表单），无则忽略。"""
        if not path:
            return
        iid = self._path_iids.get(tuple(path))
        if iid:
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._tree_paths.get(sel[0])

    def _insert_target(self):
        """Return the path of the action LIST new actions should go into."""
        path = self._selected_path()
        if path is None:
            return []
        path = list(path)
        if isinstance(path[-1], str):
            return path  # a branch list (then/else/actions)
        act = self.model.get_action(path)
        children = user_actions.child_container(act)
        if children:
            return path + [children[0]]  # insert into first child list
        return path[:-1]  # sibling of a leaf

    def _add_user_action(self, atype):
        self._apply_form()
        action = user_actions.new_action(atype)
        target = self._insert_target()
        sel = self._selected_path()
        if sel and isinstance(sel[-1], int) and self.model.get_action(sel).get("type") not in ("if_image", "repeat", "group"):
            index = sel[-1] + 1
        else:
            index = None
        self.model.insert_action(target, action, index)
        self._rebuild_tree()
        new_index = index if index is not None else len(self.model.get_list(target)) - 1
        self._reselect_path(list(target) + [new_index])
        self._refresh_status()

    def _on_library_double(self, event):
        item = self.lib_tree.focus()
        if not item:
            return
        values = self.lib_tree.item(item, "values")
        if values and values[0] in user_actions.ACTION_TEMPLATES:
            self._add_user_action(values[0])

    def _on_tree_select(self, event=None):
        self._apply_form()
        path = self._selected_path()
        if path is None or isinstance(path[-1], str):
            self._clear_form()
            return
        self._build_form(path)

    def _on_tree_double(self, event):
        # double-click a leaf to edit; nothing extra needed (select already builds form)
        pass

    def _delete_action(self):
        path = self._selected_path()
        if path is None or isinstance(path[-1], str):
            return
        self.model.remove_action(list(path))
        self._rebuild_tree()
        self._clear_form()
        self._refresh_status()

    def _duplicate_action(self):
        path = self._selected_path()
        if path is None or isinstance(path[-1], str):
            return
        self.model.duplicate_action(list(path))
        self._rebuild_tree()
        self._refresh_status()

    def _move_action(self, delta):
        path = self._selected_path()
        if path is None or isinstance(path[-1], str):
            return
        self.model.move_action(list(path), delta)
        self._rebuild_tree()
        self._refresh_status()

    # ------------------------------------------------------------- form
    def _clear_form(self):
        for child in self.form_frame.winfo_children():
            child.destroy()
        self._form_entries = {}
        self._form_path = None

    def _form_row(self, label, key, value, parse="str"):
        c = self._c
        fr = tk.Frame(self.form_frame, bg=c["bg_surface"])
        fr.pack(fill="x", padx=14, pady=3)
        tk.Label(fr, text=label, bg=c["bg_surface"], fg=c["fg_gray"], width=14,
                 anchor="w").pack(side="left")
        var = tk.StringVar(value=str(value))
        entry = tk.Entry(fr, textvariable=var, bg=c["bg_input"], fg=c["fg_white"],
                         insertbackground=c["fg_white"], relief="flat", width=20)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<FocusOut>", self._apply_form)
        entry.bind("<Return>", self._apply_form)
        self._form_entries[key] = (entry, parse, var)

    def _form_btn(self, text, command):
        return self._btn(self.form_frame, text, command, small=True)

    def _build_form(self, path):
        self._clear_form()
        action = self.model.get_action(path)
        self._form_path = path
        c = self._c
        t = action.get("type")

        tk.Label(self.form_frame, text=user_actions.action_summary(action), bg=c["bg_surface"],
                 fg=c["fg_white"], font=("Microsoft YaHei UI", 10, "bold"),
                 wraplength=280, justify="left").pack(anchor="w", padx=14, pady=(12, 8))

        if t == "key":
            self._form_row("按键 key", "key", action.get("key", ""))
            self._form_row("按住(秒)", "hold_seconds", action.get("hold_seconds", 0.06), "float")
            self._form_row("执行后等待(秒)", "after_wait", action.get("after_wait", 0), "float")
            self._test_btn(action)
        elif t == "click":
            self._form_row("X", "x", action.get("x", 0.5), "float")
            self._form_row("Y", "y", action.get("y", 0.5), "float")
            self._form_row("执行后等待(秒)", "after_wait", action.get("after_wait", 0), "float")
            self._form_btn("从 Roblox 选择坐标", lambda: self._pick_coordinate(action)).pack(anchor="w", padx=14, pady=3)
            self._test_btn(action)
        elif t == "key_click":
            self._form_row("按键 key", "key", action.get("key", "1"))
            self._form_row("按住(秒)", "hold_seconds", action.get("hold_seconds", 0.06), "float")
            self._form_row("点击 X", "x", action.get("x", 0.5), "float")
            self._form_row("点击 Y", "y", action.get("y", 0.5), "float")
            self._form_btn("从 Roblox 选择坐标", lambda: self._pick_coordinate(action)).pack(anchor="w", padx=14, pady=3)
            self._form_row("执行后等待(秒)", "after_wait", action.get("after_wait", 0.5), "float")
            self._test_btn(action)
        elif t == "wait":
            self._form_row("等待(秒)", "seconds", action.get("seconds", 1.0), "float")
        elif t in ("find_image", "click_image", "if_image"):
            self._image_form(action, t)
        elif t == "repeat":
            self._repeat_form(action)
        elif t == "group":
            self._form_row("名称", "name", action.get("name", "动作组"))

    def _image_form(self, action, t):
        c = self._c
        # template (read-only path, set via crop)
        tk.Label(self.form_frame, text="模板", bg=c["bg_surface"], fg=c["fg_gray"],
                 width=14, anchor="w").pack(side="left", padx=(14, 0), pady=3)
        self.lbl_template = tk.Label(self.form_frame, text=action.get("template", "(未选择)"),
                                     bg=c["bg_surface"], fg=c["fg_white"], anchor="w")
        self.lbl_template.pack(side="left", fill="x", expand=True, pady=3)

        self._form_row("相似度", "threshold", action.get("threshold", 0.85), "float")
        if t in ("click_image",):
            self._form_row("执行后等待(秒)", "after_wait", action.get("after_wait", 0.3), "float")

        row_btns = tk.Frame(self.form_frame, bg=c["bg_surface"])
        row_btns.pack(fill="x", padx=14, pady=6)
        self._btn(row_btns, "截取模板", lambda: self._crop_template(action), "accent", small=True).pack(side="left", padx=2)
        self._btn(row_btns, "测试识别", lambda: self._test_recognition(action), small=True).pack(side="left", padx=2)

        # template preview + last test result
        self.preview_area = tk.Frame(self.form_frame, bg=c["bg_surface"])
        self.preview_area.pack(fill="both", expand=True, padx=14, pady=8)
        self._show_template_preview(action.get("template", ""))

    def _repeat_form(self, action):
        c = self._c
        self._forever_var = tk.BooleanVar(value=bool(action.get("forever", False)))

        def _toggle():
            action["forever"] = self._forever_var.get()
            if action.get("forever"):
                action.pop("count", None)
            else:
                action.setdefault("count", 1)
            self.model.mark_dirty()
            self._refresh_status()
            self._rebuild_tree()

        cb = tk.Checkbutton(self.form_frame, text="一直重复 (直到停止)", variable=self._forever_var,
                            command=_toggle, bg=c["bg_surface"], fg=c["fg_white"],
                            selectcolor=c["bg_input"], activebackground=c["bg_surface"])
        cb.pack(anchor="w", padx=14, pady=6)
        if not action.get("forever"):
            self._form_row("重复次数", "count", action.get("count", 1), "int")
        self._form_btn("向内部添加动作 →", self._add_into_container).pack(anchor="w", padx=14, pady=4)

    def _test_btn(self, action):
        self._form_btn("测试动作", lambda: self._test_action(action)).pack(anchor="w", padx=14, pady=6)

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
            self._rebuild_tree()
            self._refresh_status()

    def _add_into_container(self):
        # insert a new action into the currently-edited container (repeat/group/if_image)
        if self._form_path is None:
            return
        act = self.model.get_action(self._form_path)
        children = user_actions.child_container(act)
        if not children:
            return
        key = children[0]
        self.model.insert_action(self._form_path + [key], user_actions.new_action("wait"))
        self._rebuild_tree()
        self._refresh_status()

    # ------------------------------------------------------------- preview
    def _template_abs(self, template_rel):
        return self.model.resolve_template_abs(template_rel)

    def _show_template_preview(self, template_rel):
        for child in self.preview_area.winfo_children():
            child.destroy()
        c = self._c
        if not template_rel:
            tk.Label(self.preview_area, text="（尚无模板预览）", bg=c["bg_surface"],
                     fg=c["fg_dim"]).pack(pady=20)
            return
        try:
            img = cv2.imread(self._template_abs(template_rel))
            if img is None:
                raise ValueError("读取失败")
            h, w = img.shape[:2]
            tk.Label(self.preview_area, text=f"{template_rel}  ·  {w}×{h}", bg=c["bg_surface"],
                     fg=c["fg_gray"]).pack(anchor="w", pady=(0, 4))
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(img_rgb)
            pil.thumbnail((220, 140))
            self._tpl_photo = ImageTk.PhotoImage(pil)
            tk.Label(self.preview_area, image=self._tpl_photo, bg=c["bg_surface"]).pack()
        except Exception as e:
            tk.Label(self.preview_area, text=f"模板预览失败: {e}", bg=c["bg_surface"],
                     fg=c["fg_dim"]).pack(pady=20)

    def _preview_current_frame(self):
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 窗口！", parent=self)
            return
        try:
            frame = engine.capture_window(hwnd)
            if frame is None:
                raise RuntimeError("截图失败")
            for child in self.preview_area.winfo_children():
                child.destroy()
            c = self._c
            ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
            tk.Label(self.preview_area, text=f"编辑器画面快照 · 采集时间 {ts}（仅调试，不用于识别）",
                     bg=c["bg_surface"], fg=c["fg_gray"], wraplength=280,
                     justify="left").pack(anchor="w", pady=(0, 4))
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(img_rgb)
            pil.thumbnail((220, 140))
            self._frame_photo = ImageTk.PhotoImage(pil)
            tk.Label(self.preview_area, image=self._frame_photo, bg=c["bg_surface"]).pack()
        except Exception as e:
            self._log(f"获取当前画面失败: {e}")

    # ------------------------------------------------------------- live test
    def _test_action(self, action):
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 窗口！", parent=self)
            return
        try:
            ok = self.model.test_input(hwnd, action)
            self._log(f"动作测试结果: {'成功' if ok else '失败'}（{action.get('type')}）")
        except Exception as e:
            self._log(f"动作测试异常: {e}")

    def _test_recognition(self, action):
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 窗口！", parent=self)
            return
        template = action.get("template", "")
        if not template:
            messagebox.showinfo("提示", "请先截取模板。", parent=self)
            return
        try:
            diag = self.model.test_find_image(hwnd, template, float(action.get("threshold", 0.85)))
            status = "FOUND" if diag["matched"] else "NOT FOUND"
            ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
            self._log(f"测试识别 [{status}] {template} conf={diag['confidence']:.4f} "
                      f"pos=({diag['relative_x']:.4f},{diag['relative_y']:.4f}) @{ts}")
            # show in the properties area
            for child in self.preview_area.winfo_children():
                child.destroy()
            c = self._c
            color = "#63cba5" if diag["matched"] else "#e6b566"
            tk.Label(self.preview_area, text=f"最近测试: {status}  @ {ts}", bg=c["bg_surface"],
                     fg=color, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            tk.Label(self.preview_area, text=f"置信度 {diag['confidence']:.4f}\n"
                     f"位置 ({diag['relative_x']:.4f}, {diag['relative_y']:.4f})",
                     bg=c["bg_surface"], fg=c["fg_white"], justify="left").pack(anchor="w", pady=(2, 0))
        except Exception as e:
            self._log(f"测试识别异常: {e}")
            messagebox.showerror("测试识别失败", str(e), parent=self)

    # ------------------------------------------------------------- pick / crop
    def _pick_coordinate(self, action):
        if not self.app.hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 窗口！", parent=self)
            return

        def on_pick(rx, ry):
            action["x"] = rx
            action["y"] = ry
            self.model.mark_dirty()
            self._refresh_status()
            self._rebuild_tree()
            self._build_form(self._form_path)

        self.app.pick_coordinate_generic(on_pick)

    def _crop_template(self, action):
        if self.model.script_id is None:
            messagebox.showinfo("提示", "请先保存脚本，再截取模板。", parent=self)
            self._save_script()
            if self.model.script_id is None:
                return

        def on_crop(cropped, offset_x, offset_y):
            try:
                assets = self.model.ensure_assets_dir()
                # 自动生成模板文件名，不再要求用户手动输入
                n = 1
                while os.path.exists(os.path.join(assets, f"tpl_{n}.png")):
                    n += 1
                rel = self.model.template_rel_path(f"tpl_{n}.png")
                abs_path = os.path.join(self.model.store.script_dir(self.model.script_id), rel)
                cv2.imwrite(abs_path, cropped)
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
                self._refresh_status()
                self._rebuild_tree()
                self._reselect_path(self._form_path)  # 重建后恢复选择与表单
                self._log(f"模板已保存: {rel}（{cropped.shape[1]}×{cropped.shape[0]}）")
            except Exception as e:
                self._log(f"模板保存失败: {e}")
                messagebox.showerror("模板保存失败", str(e), parent=self)

        self.app.crop_template_generic(on_crop)

    # ------------------------------------------------------------- run / stop
    def _run_script(self):
        if self.controller.is_running():
            return
        hwnd = self.app.hwnd
        if not hwnd:
            messagebox.showwarning("警告", "请先选择目标 Roblox 窗口！", parent=self)
            return
        self._apply_form()
        if self.model.script_id is None or self.model.dirty:
            if not self._save_script():
                return
        base_dir = self.model.store.script_dir(self.model.script_id)
        try:
            prims = self.model.compiled_actions()
        except (ValueError, KeyError) as e:
            messagebox.showerror("编译失败", str(e), parent=self)
            return
        self.controller.start(hwnd, prims, base_dir)
        self._log(f"开始运行脚本“{self.model.name}”（{len(self.model.actions)} 个用户动作 → "
                  f"{len(prims)} 个原语）。")
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
