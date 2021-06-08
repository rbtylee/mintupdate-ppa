import locale
import os
import subprocess
import threading
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from apt.utils import get_maintenance_end_date
from mintcommon.localization import localized_ui

from common import settings
from common.constants import (ROOT_FUNCTIONS, SUPPORTED_KERNEL_TYPES,
                              USE_MAINLINE_KERNELS, Origin)
from common.dialogs import show_confirmation_dialog, show_dpkg_lock_msg
from common.functions import (configured_kernel_type, dpkg_locked,
                              get_release_dates, read_file)
from common.MainlineKernels import MAINLINE_KERNEL_DATA, MainlineKernels
from kernel.InstallKernelThread import InstallKernelThread
from kernel.KernelData import KernelData
from kernel.KernelRow import KernelRow
from kernel.MarkKernelRow import MarkKernelRow


class KernelWindow:

    def __init__(self, application, is_standalone=False):
        self.application = application
        self.application.set_sensitive(False)
        self.application.refresh_inhibited = True
        self.is_standalone = is_standalone
        self.builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/kernels.ui", _), -1)
        self.window = self.builder.get_object("main_window")
        self.window.set_transient_for(self.application.window)
        self.minimize_handler = self.window.connect("window-state-event", self.on_minimize)
        self.destroy_handler = self.window.connect("destroy", self.destroy_window)

        self.main_stack = self.builder.get_object("main_stack")
        self.status_refreshing_spinner = self.builder.get_object("status_refreshing_spinner")
        self.status_installing_spinner = self.builder.get_object("status_installing_spinner")

        # Set up the kernel warning page
        self.builder.get_object("button_continue").connect("clicked", self.on_continue_clicked)
        self.builder.get_object("button_help").connect("clicked", self.application.open_help)
        self.builder.get_object("checkbutton1").connect("toggled", self.on_info_checkbox_toggled)
        self.main_stack.set_visible_child_name("status_refreshing")

        # Set up the main kernel page
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        stack_switcher = Gtk.StackSidebar()
        stack_switcher.set_stack(self.stack)
        self.builder.get_object("scrolled_series").pack_start(stack_switcher, True, True, 0)
        self.builder.get_object("kernel_stack_box").pack_start(self.stack, True, True, 0)

        # Set up the kernel mass operation confirmation window and associated buttons
        self.action_confirmation_dialog = self.builder.get_object("confirmation_window")
        self.action_confirmation_dialog.connect("destroy", self.on_action_cancel_clicked)
        self.action_confirmation_dialog.connect("delete-event", self.on_action_cancel_clicked)
        self.builder.get_object("b_confirmation_confirm").connect("clicked", self.on_action_confirm_clicked)
        self.builder.get_object("b_confirmation_cancel").connect("clicked", self.on_action_cancel_clicked)
        self.action_listbox = self.builder.get_object("confirmation_listbox")
        self.action_listbox.set_sort_func(self.action_listbox_sort)
        self.remove_kernels_listbox = []
        self.queued_kernels_listbox = []
        self.queued_kernels = []
        self.marked_kernels = []
        self.installed_kernels = []
        self.button_massremove = self.builder.get_object("button_massremove")
        self.button_massremove.connect("clicked", self.show_action_confirmation_dialog, self.remove_kernels_listbox)
        self.button_do_queue = self.builder.get_object("button_do_queue")
        self.button_do_queue.connect("clicked", self.show_action_confirmation_dialog, self.queued_kernels_listbox)

        # Set up the reboot to kernel dialog
        self.grub_config_path = "/boot/grub/grub.cfg"
        self.grub_confirmation_dialog = self.builder.get_object("grub_confirmation_window")
        self.grub_confirmation_dialog.set_title("")
        self.grub_confirmation_dialog.connect("destroy", self.grub_confirmation_dialog_hide)
        self.grub_confirmation_dialog.connect("delete-event", self.grub_confirmation_dialog_hide)
        self.builder.get_object("b_grub_cancel").connect("clicked", self.grub_confirmation_dialog_hide)
        self.grub_confirm_button = self.builder.get_object("b_grub_confirm")
        self.grub_confirm_label = self.builder.get_object("grub_label")

        # Get distro release dates for support duration calculation
        self.release_dates = get_release_dates()

        # Get mainline kernel support status, if enabled
        if USE_MAINLINE_KERNELS:
            mainline = MainlineKernels()
            try:
                self.mainline_kernel_support_status = mainline.get_support_status()
            except mainline.MainlineKernelsException as e:
                self.application.logger.write_error(e)
                self.application.logger.write_error("Mainline kernels might not be listed.")
                self.mainline_kernel_support_status = {}
        else:
            self.mainline_kernel_support_status = {}

        # Set up the kernel type selection dropdown
        self.kernel_type_selector_box = self.builder.get_object("kernel-type-selector-box")
        self.initially_configured_kernel_type = self.current_kernel_type = configured_kernel_type()
        self.kernel_type_selector = self.builder.get_object("cb_kernel_type")
        for index, kernel_type in enumerate(SUPPORTED_KERNEL_TYPES):
            self.kernel_type_selector.append_text(kernel_type[1:])
            if kernel_type[1:] == self.current_kernel_type[1:]:
                self.kernel_type_selector.set_active(index)
        self.kernel_type_selector.connect("changed", self.on_kernel_type_selector_changed)

        # Show or hide the reboot_menu_button
        if os.path.exists(self.grub_config_path):
            self.reboot_menu_button = self.builder.get_object("reboot-menu-button")
        else:
            self.reboot_menu_button = False

        # Show the window
        if self.application.app_hidden:
            self.window.set_position(Gtk.WindowPosition.CENTER)
        else:
            self.window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
            self.window.set_skip_taskbar_hint(True)
        self.window.show_all()
        self.window.present_with_time(Gtk.get_current_event_time())

        # Create reboot menu:
        if self.reboot_menu_button:
            menu = Gtk.Menu()
            def add_menu_item(label, callback, *user_data):
                menu_item = Gtk.MenuItem.new_with_label(label)
                menu_item.connect("activate", callback, *user_data)
                menu.append(menu_item)
                return menu_item
            add_menu_item(_("Set Default Kernel…"), self.show_grub_confirmation_dialog, "set-default")
            add_menu_item(_("Reboot to Default Kernel…"), self.reboot_to_kernel, self.window, "default")
            add_menu_item(_("Reboot to Current Kernel…"), self.reboot_to_kernel, self.window, "current")
            add_menu_item(_("Reboot to Another Kernel…"), self.show_grub_confirmation_dialog, "reboot")
            menu.show_all()
            self.reboot_menu_button.set_popup(menu)
            del add_menu_item
            del menu

        if settings.get_boolean("hide-kernel-update-warning"):
            # Build kernels list
            self.refresh_kernels_list()
        else:
            # Show info box
            self.main_stack.set_visible_child_name("info_box")

    def toggle_kernel_type_selector(self):
        """
        Called by refresh_kernels_list, toggles visibility of kernel_type_selector_box if
        one of allow_kernel_type_selection and the allow-kernel-type-selection setting are True
        """
        self.kernel_type_selector_box.set_visible(self.allow_kernel_type_selection | \
            settings.get_boolean("allow-kernel-type-selection"))

    def on_kernel_type_selector_changed(self, widget):
        """ Store current selection and refresh kernel list on kernel type selection change """
        self.current_kernel_type = f"-{widget.get_active_text()}"
        settings.set_string("selected-kernel-type", self.current_kernel_type)
        self.refresh_kernels_list()

    def on_minimize(self, _widget, event):
        """
        Minimizes the main window when the kernel window gets minimized.
        This is necessary for UX because we do not show a taskbar hint anymore.
        """
        if not self.application.app_hidden and event.new_window_state & Gdk.WindowState.ICONIFIED:
            self.application.window.iconify()

    def refresh_kernels_list(self):
        self.status_refreshing_spinner.start()
        self.main_stack.set_visible_child_name("status_refreshing")
        self.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.remove_kernels_listbox.clear()
        for child in self.stack.get_children():
            child.destroy()
        self.kernel_list = ""
        thread = threading.Thread(target=self.do_refresh_kernels_list)
        thread.start()
        while thread.is_alive():
            Gtk.main_iteration()
        try:
            self.build_kernels_list(self.kernel_list)
            del self.kernel_list
            self.stack.show_all()
            self.window.get_window().set_cursor(None)
            self.toggle_kernel_type_selector()
            self.main_stack.set_visible_child_name("main_box")
            self.status_refreshing_spinner.stop()
        except:
            # Usually when kernel window was closed in the meantime
            pass

    def do_refresh_kernels_list(self):
        self.kernel_list = subprocess.run(["/usr/lib/linuxmint/mintUpdate/checkKernels.py",
            self.current_kernel_type], stdout=subprocess.PIPE, encoding="utf-8").stdout

    def build_kernels_list(self, lines):
        now = datetime.now()
        hwe_support_duration = {}
        lines = lines.split("\n")
        lines.sort()
        kernel_list = []
        pages_needed = []
        pages_needed_sort = []
        self.marked_kernels.clear()
        self.button_massremove.set_sensitive(False)
        if self.reboot_menu_button:
            self.reboot_menu_button.set_visible(False)
        current_kernel = None
        self.allow_kernel_type_selection = False
        for line in lines:
            kernel_data = KernelData()
            values = line.split('###')
            if len(values) == 10:
                (version_id, kernel_data.version, kernel_data.pkg_version,
                 installed, used, origin, archive, support_duration,
                 kernel_data.type) = values[1:]
                kernel_data.version_id = version_id.split(".")
                installed = int(installed)
                # installed is:
                # 0 if not installed
                # 1 if manually installed
                # 2 if automatically installed
                kernel_data.installed = installed > 0
                kernel_data.is_auto_installed = installed == 2
                kernel_data.used = (used == "1")
                kernel_data.origin = int(origin)
                if kernel_data.used:
                    kernel_data.suffix = _("Active")
                    # ACTIVE_KERNEL_VERSION is used by the MarkKernelRow class
                    current_kernel = kernel_data.version_id
                elif kernel_data.installed:
                    kernel_data.suffix = _("Installed")
                elif kernel_data.origin == Origin.UBUNTU and "-proposed" in archive:
                    kernel_data.suffix = _("(Pre-release)")
                elif kernel_data.origin == Origin.MAINLINE_PPA:
                    kernel_data.suffix = _("(Mainline)")
                if kernel_data.type == self.current_kernel_type:
                    kernel_data.name = kernel_data.version
                else:
                    kernel_data.name = kernel_data.version + kernel_data.type
                    self.allow_kernel_type_selection = True
                kernel_data.series = ".".join(kernel_data.name.replace("-",".").split(".")[:2])
                kernel_data.release = archive.split("-", 1)[0]
                kernel_data.support_duration = int(support_duration)
                if kernel_data.support_duration and kernel_data.origin == Origin.UBUNTU:
                    if not kernel_data.release in hwe_support_duration:
                        hwe_support_duration[kernel_data.release] = []
                    if not [x for x in hwe_support_duration[kernel_data.release] if x[0] == kernel_data.series]:
                        hwe_support_duration[kernel_data.release].append(
                            [kernel_data.series, kernel_data.support_duration])

                kernel_list.append(kernel_data)
                if kernel_data.series not in pages_needed:
                    pages_needed.append(kernel_data.series)
                    pages_needed_sort.append([kernel_data.version_id, kernel_data.series])

        # get kernel support duration
        kernel_support_info = {}
        for release in hwe_support_duration:
            if release not in self.release_dates.keys():
                continue
            kernel_support_info[release] = []
            kernel_count = len(hwe_support_duration[release])
            time_since_release = (now.year - self.release_dates[release][0].year) * 12 + (now.month - self.release_dates[release][0].month)
            for point_release, kernel in enumerate(hwe_support_duration[release]):
                page_label, support_duration = kernel
                if support_duration == -1:
                    # here's some magic to determine hwe support duration based on the release cycle
                    # described here: https://wiki.ubuntu.com/Kernel/Support#A18.04.x_Ubuntu_Kernel_Support
                    if point_release >= 4:
                        # Regularly the 4th point release is the next LTS kernel. However, this sequence breaks when
                        # out-of-turn HWE kernels like 4.11 are introduced, so we have to work around that:
                        if kernel_count > 5 and point_release < kernel_count - 1:
                            support_duration = kernel_support_info[release][3][1]
                        # the 4th point release is LTS and scheduled 28 months after original release:
                        elif time_since_release >= 28:
                            support_duration = (self.release_dates[release][1].year - self.release_dates[release][0].year) * 12 + \
                                (self.release_dates[release][1].month - self.release_dates[release][0].month)
                    if point_release >= 1 and support_duration == -1:
                        # out of turn HWE kernels can be detected quite well at the time of release,
                        # but later on there's no way to know which one was the one that was out of turn
                        max_expected_point_release = (time_since_release - 3) // 6 + 1
                        if point_release > max_expected_point_release:
                            # out of turn HWE kernel
                            support_duration = 10 + max_expected_point_release * 6
                        else:
                            # treat as regular HWE kernel
                            support_duration = 10 + point_release * 6

                support_end_str = ""
                is_end_of_life = False
                support_end_year, support_end_month = get_maintenance_end_date(
                    self.release_dates[release][0], support_duration)
                is_end_of_life = (now.year > support_end_year or
                                  (now.year == support_end_year and now.month > support_end_month))
                if not is_end_of_life:
                    support_end_str = f'{locale.nl_langinfo(getattr(locale, f"MON_{support_end_month}"))} {support_end_year}'

                kernel_support_info[release].append([page_label, support_duration, support_end_str, is_end_of_life])

        kernel_list.sort(key=lambda x: x.version_id, reverse=True)
        supported_kernels = {}
        supported_mainline_kernels = []

        self.installed_kernels.clear()
        # Dictionary of i18n strings for use as kernel support status strings in the following loop
        KERNEL_SUPPORT_STRINGS = {
            "eol": _("End of Life"),
            "superseded": _("Superseded"),
            "unsupported": _("Unsupported"),
            "unknown": _("Status Unknown"),
            "supported_until": _("Supported until"),
            # TRANSLATORS: a mainline kernel version that gets built every day
            "daily": _("Daily build"),
            # TRANSLATORS: kernel.org kernel status tag, may be better left untranslated but I leave that to your judgement
            "mainline": _("Mainline"),
            # TRANSLATORS: kernel.org kernel status tag, may be better left untranslated but I leave that to your judgement
            "stable": _("Stable"),
            # TRANSLATORS: kernel.org kernel status tag, may be better left untranslated but I leave that to your judgement
            "longterm": _("Longterm")
        }
        for kernel in kernel_list:
            is_latest_in_series = False
            if kernel.support_duration and kernel.origin == Origin.UBUNTU:
                # Ubuntu kernels
                if kernel.release in kernel_support_info.keys():
                    support_info = [x for x in kernel_support_info[kernel.release] if x[0] == kernel.series]
                else:
                    support_info = None
                if support_info:
                    support_duration, support_end_str, is_end_of_life = support_info[0][1:]
                    if support_end_str:
                        if not kernel.type in supported_kernels.keys():
                            supported_kernels[kernel.type] = []
                        if not kernel.series in supported_kernels[kernel.type]:
                            supported_kernels[kernel.type].append(kernel.series)
                            kernel.support_status = "%s %s" % (KERNEL_SUPPORT_STRINGS["supported_until"], support_end_str)
                            is_latest_in_series = True
                        else:
                            kernel.support_status = KERNEL_SUPPORT_STRINGS["superseded"]
                    elif is_end_of_life:
                        kernel.support_status = KERNEL_SUPPORT_STRINGS["eol"]
            elif kernel.origin == Origin.MAINLINE_PPA or \
                 (kernel.origin == Origin.LOCAL and kernel.installed and
                  len(kernel.version_id) == 5 and len(kernel.version_id[4]) == 12):
                # Mainline kernels or what we hope is installed
                # (try to identify by the 12 character version block at the end)
                if not kernel.series in supported_mainline_kernels:
                    supported_mainline_kernels.append(kernel.series)
                    if not self.mainline_kernel_support_status:
                        kernel.support_status = KERNEL_SUPPORT_STRINGS["unknown"]
                    elif kernel.series in self.mainline_kernel_support_status:
                        # For some reason a .get() on the dict goes into an endless loop
                        # so doing the check manually
                        if self.mainline_kernel_support_status[kernel.series] in KERNEL_SUPPORT_STRINGS:
                            kernel.support_status = KERNEL_SUPPORT_STRINGS[self.mainline_kernel_support_status[kernel.series]]
                        else:
                            kernel.support_status = self.mainline_kernel_support_status[kernel.series].title()
                    else:
                        kernel.support_status = KERNEL_SUPPORT_STRINGS["eol"]
                elif kernel.version_id[3] == "999":
                    kernel.support_status = KERNEL_SUPPORT_STRINGS["daily"]
                elif kernel.version_id[3] in MAINLINE_KERNEL_DATA:
                    kernel.support_status = MAINLINE_KERNEL_DATA[kernel.version_id[3]]
                else:
                    kernel.support_status = KERNEL_SUPPORT_STRINGS["superseded"]
            else:
                kernel.support_status = KERNEL_SUPPORT_STRINGS["unsupported"]
            if kernel.installed:
                self.installed_kernels.append((kernel.type, kernel.version))
                if not kernel.used:
                    if self.reboot_menu_button:
                        self.reboot_menu_button.set_visible(True)
                    self.button_massremove.set_sensitive(True)
                    self.remove_kernels_listbox.append(MarkKernelRow(
                        kernel, self.marked_kernels, is_latest_in_series, current_kernel))

        # add kernels to UI
        pages_needed_sort.sort(reverse=True)
        active_kernel = None
        for page in pages_needed_sort:
            page = page[1]
            scw = Gtk.ScrolledWindow()
            scw.set_shadow_type(Gtk.ShadowType.IN)
            list_box = Gtk.ListBox()
            list_box.set_header_func(self.list_header_func, None)
            list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            list_box.set_activate_on_single_click(True)
            scw.add(list_box)
            self.stack.add_titled(scw, page, page)

            for kernel in kernel_list:
                if kernel.series == page:
                    row = KernelRow(kernel, self.application, self)
                    list_box.add(row)

            list_box.connect("row-activated", self.on_row_activated)

        # Create the active kernel label
        active_kernel = next((kernel for kernel in kernel_list if kernel.used), False)
        label = _("You are currently using the following kernel:")
        if active_kernel:
            if active_kernel.support_status:
                active_kernel.support_status = f" ({active_kernel.support_status})"
            self.builder.get_object("current_label").set_markup(
                f"<b>{label} {active_kernel.version}{active_kernel.type}{active_kernel.support_status}</b>")
        else:
            unsupported = _("Unknown")
            self.builder.get_object("current_label").set_markup(
                f"<b>{label} {os.uname().release} ({unsupported})</b>")

    def grub_confirmation_dialog_hide(self, *_args):
        self.grub_confirmation_dialog.hide()
        self.window.set_sensitive(True)
        return True

    def show_grub_confirmation_dialog(self, _widget, mode):
        self.window.set_sensitive(False)
        if hasattr(self.grub_confirm_button, "handler"):
            self.grub_confirm_button.disconnect(self.grub_confirm_button.handler)
        handler = None
        if mode == "reboot":
            self.grub_confirm_label.set_label(_("Select the kernel to reboot to:"))
            self.grub_confirm_button.set_label(_("Reboot to Selected Kernel Now"))
            handler = self.grub_confirm_button.connect("clicked", self.reboot_to_kernel,
                self.grub_confirmation_dialog, "another")
        elif mode == "set-default":
            # TRANSLATORS: GRUB_DEFAULT=saved and the /etc/default/grub path must not be translated
            self.grub_confirm_label.set_label(_("Select the kernel the system with with by default.\n"
                "GRUB_DEFAULT=saved must be set in /etc/default/grub."))
            self.grub_confirm_button.set_label(_("Set Selected Kernel as Default Kernel"))
            handler = self.grub_confirm_button.connect("clicked", self.grub_set_default)
        self.grub_confirm_button.handler = handler

        self.grub_confirmation_dialog.show_all()
        self.grub_confirmation_dialog.set_titlebar(Gtk.Box())
        cb_kernels = self.builder.get_object("cb_kernels")
        cb_kernels.remove_all()
        if mode == "reboot":
            current_kernel = os.uname().release
        else:
            current_kernel = False
        for kernel in self.installed_kernels:
            kernel_name = f"{kernel[1]}{kernel[0]}"
            if current_kernel and kernel_name == current_kernel:
                continue
            cb_kernels.append_text(kernel_name)
        cb_kernels.set_active(0)

    def reboot_to_kernel(self, _widget, parent, mode):
        """ `mode` must be one of `default`, `current`, or `another` """
        if mode == "default":
            if show_confirmation_dialog(parent, _(f"Reboot to default kernel now?")):
                subprocess.run(["systemctl", "reboot"])
            else:
                return
        if mode == "current":
            selected_kernel = os.uname().release
            if not show_confirmation_dialog(parent, _(f"Reboot to kernel {selected_kernel} now?")):
                return
        elif mode == "another":
            selected_kernel = self.builder.get_object("cb_kernels").get_active_text()
        self.configure_grub( selected_kernel, "grub-reboot")
        self.window.set_sensitive(True)

    def configure_grub(self, selected_kernel, command="grub-reboot"):
        """
        params:

        `parent` - window to be transient for

        `selected_kernel` - name of the kernel to reboot to

        `command` - one of `grub-reboot` or `grub-set-default`
        """
        self.grub_confirmation_dialog.hide()
        Gdk.flush()
        grub_index = self.find_kernel_in_grub(selected_kernel)
        if grub_index == False or subprocess.run(
                ["pkexec", ROOT_FUNCTIONS, command, grub_index]).returncode:
            dialog = Gtk.MessageDialog(transient_for=self.window,
                                       modal=True,
                                       destroy_with_parent=True,
                                       message_type=Gtk.MessageType.ERROR,
                                       buttons=Gtk.ButtonsType.OK,
                                       title=_("Cannot Proceed"),
                                       text=_("Failed to configure selected kernel with GRUB"))
            dialog.run()
            dialog.destroy()

    def grub_set_default(self, _widget):
        self.configure_grub(self.builder.get_object("cb_kernels").get_active_text(), "grub-set-default")
        self.window.set_sensitive(True)

    @staticmethod
    def get_menuentry_id_option(line):
        """ Return the menuentry_id_option from a grub.cfg line """
        try:
            line = line.split("{", 1)[0].split()
            return next((line[i + 1] for i, s in enumerate(line) if s == "$menuentry_id_option"), "").strip("\"'")
        except:
            return ""

    def find_kernel_in_grub(self, kernel_version):
        """
        Parse grub.cfg and return the menuentry_id_option of a non-recovery
        entry pointing to kernel_version and the current root device, if any.

        Instead of picking the first applicable entry, the entire grub.cfg is
        parsed and the most deeply nested matching entry is picked based on the
        assumption that it will be the least likely to change (in a default
        environment the entries in the Advanced submenu always keep their ID
        whereas automatically generated entries outside of the Advanced menu
        change as new kernels get installed).
        """
        major, minor = divmod(os.stat('/').st_dev, 256)
        cwd = "/dev/block/"
        device = os.path.realpath(os.path.join(cwd, os.readlink(f"{cwd}{major}:{minor}")))
        uuid = None
        cwd = "/dev/disk/by-uuid/"
        for item in os.listdir(cwd):
            item_path = os.path.realpath(os.path.join(cwd, os.readlink(f"{cwd}{item}")))
            if item_path == device:
                uuid = item
                break
        if not os.path.exists(self.grub_config_path):
            return False
        new_entry = False
        level = 0
        index = {}
        index[level] = -1
        menuentry_id_option = {}
        is_menu_entry = False
        found_entry = None
        for line in read_file(self.grub_config_path):
            line = line.strip()
            if line.startswith("submenu "):
                menuentry_id_option[level] = self.get_menuentry_id_option(line)
                index[level] += 1
                level += 1
                index[level] = -1
            elif line.startswith("menuentry "):
                menuentry_id_option[level] = self.get_menuentry_id_option(line)
                index[level] += 1
                is_menu_entry = True
                if not "recovery" in line:
                    new_entry = True
            elif line.startswith("linux"):
                if not new_entry:
                    continue
                try:
                    vmlinuz = line.split()[1].split("vmlinuz-")[1]
                    try:
                        root = line.split("root=")[1].split()[0]
                    except:
                        root = None
                    for i in range(len(index)):
                        if menuentry_id_option[i]:
                            value = menuentry_id_option[i]
                        else:
                            value = index[i]
                        if i == 0:
                            _index = value
                        else:
                            _index = f"{_index}>{value}"
                    if vmlinuz.startswith(kernel_version) and \
                       not " recovery" in line and \
                       (not root or root in (device, f"UUID={uuid}")):
                        _found = str(_index)
                        if not found_entry or _found.count(">") > found_entry.count(">"):
                            found_entry = _found
                    new_entry = False
                except IndexError:
                    continue
            if "}" in line:
                if is_menu_entry:
                    is_menu_entry = False
                elif level > 0:
                    del index[level]
                    del menuentry_id_option[level]
                    level -= 1
        return found_entry

    def destroy_window(self, _widget=None, refresh=False):
        self.window.disconnect(self.destroy_handler)
        self.window.disconnect(self.minimize_handler)
        self.window.destroy()
        if not self.is_standalone:
            self.application.kernel_window_showing = False
            self.application.set_sensitive(True)
            self.application.refresh_inhibited = False
            if refresh or self.initially_configured_kernel_type != self.current_kernel_type:
                self.application.refresh()
            self.application.kernel_window = None
        else:
            self.window.hide()
            while Gtk.events_pending():
                Gtk.main_iteration()
            raise(SystemExit)

    def install(self, kernel_list):
        if not self.is_standalone:
            self.window.hide()
            self.application.kernel_window_showing = False
            self.application.set_sensitive(False, allow_quit=False)
            self.application.set_status("mintupdate-installing", _("Kernel installation/removal in progress"))
            if settings.get_boolean("hide-window-after-update"):
                self.application.hide_main_window()
        else:
            self.window.set_sensitive(True)
            self.status_installing_spinner.start()
            self.main_stack.set_visible_child_name("status_installing")
            self.window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))

        InstallKernelThread(kernel_list, self.application, self).start()

    def on_continue_clicked(self, _widget):
        self.refresh_kernels_list()

    @staticmethod
    def on_info_checkbox_toggled(widget):
        settings.set_boolean("hide-kernel-update-warning", widget.get_active())

    @staticmethod
    def on_row_activated(_list_box, row):
        row.show_hide_children(row)

    def show_action_confirmation_dialog(self, _widget, kernel_list):
        self.window.set_sensitive(False)
        for child in self.action_listbox.get_children():
            self.action_listbox.remove(child)
        for item in kernel_list:
            self.action_listbox.add(item)
        self.action_confirmation_dialog.show_all()
        self.action_confirmation_dialog.set_titlebar(Gtk.Box())

    def on_action_cancel_clicked(self, *_args):
        self.action_confirmation_dialog.hide()
        self.window.set_sensitive(True)
        return True

    def on_action_confirm_clicked(self, _widget):
        self.action_confirmation_dialog.hide()
        if self.action_listbox.get_children():
            kernel_list = self.action_listbox.get_children()[0].kernel_list
        else:
            kernel_list = None
        if kernel_list:
            if dpkg_locked():
                show_dpkg_lock_msg(self.window)
                self.window.set_sensitive(True)
            else:
                self.install(kernel_list)
        else:
            self.window.set_sensitive(True)

    @staticmethod
    def action_listbox_sort(row_1, row_2):
        return row_1.kernel.version < row_2.kernel.version

    @staticmethod
    def list_header_func(row, before, _user_data):
        if before and not row.get_header():
            row.set_header(Gtk.Separator())
