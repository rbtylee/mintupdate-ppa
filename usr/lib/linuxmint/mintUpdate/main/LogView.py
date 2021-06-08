import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


class LogView(Gtk.Window):

    def __init__(self, application):
        super().__init__()
        self.application = application
        self.application.information_window_showing = self
        self.set_default_size(640, 480)
        self.set_title(_("Log View"))
        self.set_icon_name("mintupdate")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_border_width(12)
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_right_margin(scrolled_window.get_vscrollbar().get_preferred_width().natural_width)
        scrolled_window.add(self.textview)
        box.pack_start(scrolled_window, True, True, 0)
        self.add(box)

        self.textview.get_buffer().set_text(self.application.logger.read())
        self.application.logger.set_hook(self.update_log)

        self.connect("destroy", self.destroy_window)
        self.show_all()

    def destroy_window(self, _widget):
        self.application.logger.remove_hook()
        self.application.information_window_showing = False
        self.destroy()

    def update_log(self, line):
        try:
            buffer = self.textview.get_buffer()
            buffer.insert(buffer.get_end_iter(), line)
        except:
            pass
