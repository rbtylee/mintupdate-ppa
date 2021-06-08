import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class MarkKernelRow(Gtk.ListBoxRow):

    def __init__(self, kernel, kernel_list, is_latest_in_series=False, current_kernel=None):
        Gtk.ListBoxRow.__init__(self)
        self.kernel_list = kernel_list
        self.kernel = kernel
        if kernel.installed:
            action = _("Remove")
        else:
            action = _("Install")
        button = Gtk.CheckButton.new_with_label(f"{action} {kernel.version}{kernel.type}")
        button.connect("toggled", self.on_checked)
        Gtk.ToggleButton.set_active(button, not current_kernel or
            (not is_latest_in_series and current_kernel > kernel.version_id))
        self.add(button)

    def on_checked(self, widget):
        if widget.get_active():
            self.kernel_list.append(self.kernel)
        else:
            self.kernel_list.remove(self.kernel)
