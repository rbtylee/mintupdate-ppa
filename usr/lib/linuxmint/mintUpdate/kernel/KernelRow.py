import subprocess
import threading

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk, GLib

from common.ChangelogWindow import ChangelogWindow
from common.constants import ROOT_FUNCTIONS, Origin
from common.dialogs import show_confirmation_dialog, show_dpkg_lock_msg
from common.functions import dpkg_locked
from common.MainlineKernels import MAINLINE_KERNEL_DATA
from kernel.MarkKernelRow import MarkKernelRow

CHANGELOG = _("Changelog")
BUG_REPORTS = _("Bug Tracker")
CVE_TRACKER = _("Known Security Issues (CVE)")
WARNING = _("<b>Warning:</b> This is an unsupported kernel meant for testing purposes.")

class KernelRow(Gtk.ListBoxRow):

    def __init__(self, kernel, application, kernel_window):
        Gtk.ListBoxRow.__init__(self)

        self.application = application
        self.kernel_window = kernel_window

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)
        hbox = Gtk.Box()
        hbox.set_margin_top(8)
        hbox.set_margin_bottom(8)
        hbox.set_margin_start(20)
        hbox.set_margin_end(20)
        vbox.pack_start(hbox, True, True, 0)
        version_box = Gtk.Box()
        hbox.pack_start(version_box, False, False, 0)
        version_label = Gtk.Label()
        version_label.set_markup(kernel.name)
        version_box.pack_start(version_label, False, False, 0)
        info_box = Gtk.Box()
        info_box.set_spacing(6)
        hbox.pack_end(info_box, False, False, 0)

        if kernel.name != "":
            label = Gtk.Label()
            label.set_margin_end(6)
            label.set_margin_start(6)
            label.props.xalign = 0.5
            label.set_markup(f"<i>{kernel.suffix}</i>")
            Gtk.StyleContext.add_class(Gtk.Widget.get_style_context(label), "dim-label")
            hbox.set_center_widget(label)

        if kernel.support_status:
            status_label = Gtk.Label()
            status_label.set_margin_end(0)
            status_label.set_markup(kernel.support_status)
            status_label.set_halign(Gtk.Align.END)
            hbox.pack_end(status_label, True, True, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(150)
        vbox.pack_start(self.revealer, True, True, 0)
        hidden_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hidden_box.set_margin_end(20)
        hidden_box.set_margin_start(20)
        hidden_box.set_margin_bottom(6)
        self.revealer.add(hidden_box)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.set_margin_bottom(6)
        hidden_box.pack_start(self.box, True, True, 0)
        button_box_container = Gtk.Box()
        self.button_box_left = Gtk.Box()
        button_box_right = Gtk.Box()
        button_box_container.pack_start(self.button_box_left, False, False, 0)
        button_box_container.pack_end(button_box_right, False, False, 0)
        hidden_box.pack_start(button_box_container, False, False, 0)

        is_mainline = False
        # Try to identify installed mainline kernels via the version suffix,
        # e.g. 5.1.2-050102.201905141830
        if kernel.origin == Origin.LOCAL and "-" in kernel.pkg_version:
            version_suffix = kernel.pkg_version.split("-", 1)[1]
            if len(version_suffix) >= 6 and "." in version_suffix and \
               len(version_suffix.split(".", 1)[1]) == 12:
                is_mainline = True
        elif kernel.origin == Origin.MAINLINE_PPA:
            # Mainline kernel needs pkg_version for installation because it's
            # part of the filename
            is_mainline = True
            kernel.version = kernel.pkg_version

        # Ubuntu kernels
        if kernel.origin == Origin.UBUNTU:
            self.add_button(CHANGELOG,
                            source=f"linux-image-{kernel.version}{kernel.type}",
                            name=f"linux {kernel.version} (Ubuntu)")
            self.add_label(f"<a href='https://launchpad.net/ubuntu/+source/linux/+bugs?field.searchtext={kernel.version}'>{BUG_REPORTS}</a>")
            self.add_label(f"<a href='https://people.canonical.com/~ubuntu-security/cve/pkg/linux.html'>{CVE_TRACKER}</a>")
        # Mainline kernels (Ubuntu's builds)
        elif is_mainline:
            self.add_label(WARNING)
            if kernel.version_id[3].isnumeric():
                mainline_branch_id = int(kernel.version_id[3])
            else:
                mainline_branch_id = 0
            mainline_kernel_data = MAINLINE_KERNEL_DATA[mainline_branch_id]
            name = mainline_kernel_data.format_version(kernel.version_id[3])
            changelog_url = mainline_kernel_data.changelog_url(kernel.version_id[3])
            self.add_button(CHANGELOG,
                            source=changelog_url,
                            name=f"linux {name}")
            self.add_label(f"<a href='https://bugzilla.kernel.org/'>{BUG_REPORTS}</a>")
            self.add_label(f"<a href='https://nvd.nist.gov/vuln/search/results?query=linux+kernel'>{CVE_TRACKER}</a>")
        # Liquorix kernels
        elif kernel.type == "-liquorix":
            self.add_button(CHANGELOG,
                            source=f"linux-image-{kernel.version}-liquorix-amd64",
                            name=f"linux {kernel.version} (Liquorix)")
            self.add_label(f"<a href='https://github.com/damentz/liquorix-package/issues'>{BUG_REPORTS}</a>")
            self.add_label(f"<a href='https://nvd.nist.gov/vuln/search/results?query=linux+kernel'>{CVE_TRACKER}</a>")
        # Valve's experimental mftutex kernels
        elif kernel.type == "-mfutex":
            self.add_label(WARNING)
            self.add_button(CHANGELOG,
                            source=f"linux-image-{kernel.version}{kernel.type}",
                            name=f"linux {kernel.version} (Ubuntu)")
            self.add_label(f"<a href='https://launchpad.net/ubuntu/+source/linux/+bugs?field.searchtext={kernel.version}'>{BUG_REPORTS}</a>")
            self.add_label(f"<a href='https://people.canonical.com/~ubuntu-security/cve/pkg/linux.html'>{CVE_TRACKER}</a>")

        button = Gtk.Button.new()
        button.connect("clicked", self.install_kernel, kernel)
        button_box_right.pack_end(button, False, False, 0)
        queuebutton = Gtk.Button.new()
        queuebutton.connect("clicked", self.queue_kernel, kernel)
        button_box_right.pack_end(queuebutton, False, False, 5)
        if kernel.installed:
            button.set_label(_("Remove"))
            queuebutton.set_label(_("Queue Removal"))
            if kernel.used:
                button.set_tooltip_text(_("This kernel cannot be removed because it is currently in use."))
                button.set_sensitive(False)
        else:
            button.set_label(_("Install"))
            queuebutton.set_label(_("Queue Installation"))
        queuebutton.set_tooltip_text(button.get_tooltip_text())
        queuebutton.set_sensitive(button.get_sensitive())
        if kernel.installed:
            kernel_state = Gtk.Button.new()
            kernel_state.connect("clicked", self.on_kernel_state_clicked, kernel)
            self.kernel_state_setup(kernel_state, kernel.is_auto_installed)
            button_box_right.pack_end(kernel_state, False, False, 0)

    def add_label(self, markup):
        label = Gtk.Label()
        label.set_line_wrap(True)
        label.set_markup(markup)
        self.box.pack_start(label, False, False, 2)

    def add_button(self, label, source, name="", callback=None):
        """ Adds a button to self.button_box_left """
        if not callback:
            callback = self.show_changelog
        button = Gtk.Button.new()
        button.set_label(label)
        button.connect("clicked", callback, source, name)
        self.button_box_left.add(button)

    def show_changelog(self, widget, source, name):
        ChangelogWindow(source=source,
                        name=name,
                        transient_for=self.kernel_window.window,
                        widget=widget,
                        modal=True)

    def show_hide_children(self, _widget):
        if self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(False)
        else:
            self.revealer.set_reveal_child(True)

    def install_kernel(self, _widget, kernel):
        if kernel.installed:
            message = _("Are you sure you want to remove the %s kernel?") % f"{kernel.version}{kernel.type}"
        else:
            message = _("Are you sure you want to install the %s kernel?") % f"{kernel.version}{kernel.type}"
        if show_confirmation_dialog(self.kernel_window.window, message):
            if dpkg_locked():
                show_dpkg_lock_msg(self.kernel_window.window)
            else:
                self.kernel_window.install([kernel])

    def queue_kernel(self, widget, kernel):
        widget.set_sensitive(False)
        if kernel not in self.kernel_window.queued_kernels:
            self.kernel_window.button_do_queue.set_sensitive(True)
            self.kernel_window.queued_kernels_listbox.append(
                MarkKernelRow(kernel, self.kernel_window.queued_kernels))

    @staticmethod
    def kernel_state_setup(widget, is_auto_installed):
        if is_auto_installed:
            tooltip = _("Kernel can be removed automatically.\nClick to add automatic removal protection.")
            icon = "mintupdate-unlocked-symbolic"
        else:
            tooltip = _("Kernel is protected from automatic removal.\nClick to remove automatic removal protection.")
            icon = "mintupdate-locked-symbolic"
        widget.set_tooltip_text(tooltip)
        widget.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))

    def on_kernel_state_clicked(self, widget, kernel):
        widget.set_sensitive(False)
        thread = threading.Thread(target=self.do_mark_kernel, args=(widget, kernel))
        thread.start()
        while thread.is_alive():
            Gtk.main_iteration()
        widget.set_sensitive(True)

    def do_mark_kernel(self, widget, kernel):
        try:
            subprocess.run(["pkexec", ROOT_FUNCTIONS, "mark-kernel",
                            "manual" if kernel.is_auto_installed else "auto",
                            kernel.version, kernel.type], check=True)
            kernel.is_auto_installed = not kernel.is_auto_installed
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.kernel_state_setup, widget, kernel.is_auto_installed)
        except subprocess.CalledProcessError:
            print("Error setting state for kernel:", kernel.version)
