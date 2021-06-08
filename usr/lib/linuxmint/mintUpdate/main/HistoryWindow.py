import dateutil.parser
import os
import subprocess
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from mintcommon.localization import localized_ui

from common.constants import KERNEL_PKG_NAMES
from common.ChangelogWindow import ChangelogWindow

COL_DATE_SORT, COL_DATE_LOCALIZED, COL_PACKAGE, COL_OLD_VERSION, COL_NEW_VERSION = range(5)

class HistoryWindow:

    def __init__(self, application):
        self.application = application
        self.application.history_window_showing = self
        builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/history.ui", _), -1)
        self.window = builder.get_object("main_window")
        self.window.set_transient_for(self.application.window)
        self.window.connect("destroy", self.destroy_window)
        self.window.connect("key-press-event", self.on_key_press_event)
        builder.get_object("button_close").connect("clicked", self.destroy_window)

        self.treeview = builder.get_object("treeview_history")
        column_date_sort = self.application.add_treeview_column(_("Date"),
            COL_DATE_SORT, Gtk.CellRendererText(), text=COL_DATE_SORT)
        column_date_sort.set_visible(False)
        column_date_localized = self.application.add_treeview_column(_("Date"),
            COL_DATE_SORT, Gtk.CellRendererText(), text=COL_DATE_LOCALIZED)
        column_package = self.application.add_treeview_column(_("Package"),
            COL_PACKAGE, Gtk.CellRendererText(), text=COL_PACKAGE)
        column_old_version = self.application.add_treeview_column(_("Old Version"),
            COL_OLD_VERSION, Gtk.CellRendererText(), text=COL_OLD_VERSION)
        column_old_version.set_max_width(300)
        column_new_version = self.application.add_treeview_column(_("New Version"),
            COL_NEW_VERSION, Gtk.CellRendererText(), text=COL_NEW_VERSION)
        column_new_version.set_max_width(300)
        self.treeview.append_column(column_date_sort)
        self.treeview.append_column(column_date_localized)
        self.treeview.append_column(column_package)
        self.treeview.append_column(column_old_version)
        self.treeview.append_column(column_new_version)
        self.treeview.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.treeview.set_search_column(COL_PACKAGE)
        self.window.show_all()

        # Build model
        thread = threading.Thread(target=self.build_model)
        thread.start()
        while thread.is_alive():
            Gtk.main_iteration()

        # Add model to window (if window was not destroyed in the meantime)
        if self.application.history_window_showing == self:
            self.model.set_sort_column_id(COL_DATE_SORT, Gtk.SortType.DESCENDING)
            self.treeview.set_model(self.model)
            self.treeview.connect("button-release-event" , self.on_click, self.model)
            builder.get_object("stack").set_visible_child_name("treeview_container")
            builder.get_object("spinner").stop()

    def destroy_window(self, _widget):
        self.window.disconnect_by_func(self.destroy_window)
        self.application.history_window_showing = False
        self.window.destroy()

    def build_model(self):
        self.model = Gtk.TreeStore(str, str, str, str, str)
        if os.path.isfile("/var/log/dpkg.log"):
            updates = subprocess.run('zgrep -e " upgrade " -e " install linux-" -sh /var/log/dpkg.log*',
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True, encoding="utf-8").stdout.split("\n")
            updates.sort(reverse=True)
            kernel_packages = []
            for pkg_name in KERNEL_PKG_NAMES:
                kernel_packages.append(pkg_name.replace("-VERSION", "").replace("-KERNELTYPE", ""))
            try:
                default_arch = subprocess.run(["dpkg", "--print-architecture"],
                                            stdout=subprocess.PIPE, encoding="utf-8").stdout.strip()
            except:
                default_arch = ""
            for pkg in updates:
                values = pkg.split()
                if len(values) == 6:
                    upd_date, upd_time, action, package, oldVersion, newVersion = values
                    if oldVersion == newVersion:
                        continue
                    if action == "install":
                        is_kernel = False
                        for pkg_name in kernel_packages:
                            if package.startswith(pkg_name):
                                is_kernel = True
                        if not is_kernel:
                            continue
                    if ":" in package:
                        # Only show foreign architectures
                        package = package.replace(":all", "").replace(f":{default_arch}", "")

                    tree_iter = self.model.insert_before(None, None)
                    upd_date_time = f"{upd_date} {upd_time}"
                    self.model.set_value(tree_iter, COL_DATE_SORT, upd_date_time)
                    dt = dateutil.parser.parse(upd_date_time)
                    self.model.set_value(tree_iter, COL_DATE_LOCALIZED, dt.strftime("%x %X").strip())
                    self.model.set_value(tree_iter, COL_PACKAGE, package)
                    self.model.set_value(tree_iter, COL_OLD_VERSION, oldVersion)
                    self.model.set_value(tree_iter, COL_NEW_VERSION, newVersion)

    @staticmethod
    def on_click(widget, event, model):
        """ Right-click handler """
        if event.button == 3: # right click
            path = widget.get_path_at_pos(int(event.x), int(event.y))[0]
            package_name = model[path][COL_PACKAGE]
            ChangelogWindow(source=package_name)

    def on_key_press_event(self, _widget, event):
        """ Ctrl+c handler """
        modifiers = (Gdk.ModifierType.SHIFT_MASK |
             Gdk.ModifierType.CONTROL_MASK |
             Gdk.ModifierType.MOD1_MASK |
             Gdk.ModifierType.SUPER_MASK |
             Gdk.ModifierType.HYPER_MASK |
             Gdk.ModifierType.META_MASK)
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and \
           not (event.state & (modifiers ^ Gdk.ModifierType.CONTROL_MASK)) and \
           event.keyval == Gdk.KEY_c:
            output = []
            model, path = self.treeview.get_selection().get_selected_rows()
            for row in path:
                output.append(f"{model[row][COL_DATE_LOCALIZED]} {model[row][COL_PACKAGE]} {model[row][COL_OLD_VERSION]} {model[row][COL_NEW_VERSION]}")
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text("\n".join(output), -1)
