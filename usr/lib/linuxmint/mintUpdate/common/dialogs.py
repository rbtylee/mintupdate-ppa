import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


def show_dpkg_lock_msg(parent):
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        destroy_with_parent=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        title=_("Cannot Proceed"),
        text=_("Another process is currently using the package management system. "
                "Please wait for it to finish and then try again."))
    dialog.run()
    dialog.destroy()

def show_confirmation_dialog(transient_for, text, title=None):
    """ Show a modal confirmation dialog, returns a bool (not thread-safe) """
    dialog = Gtk.MessageDialog(transient_for=transient_for,
                            modal=True,
                            destroy_with_parent=True,
                            message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.YES_NO,
                            text=text,
                            use_markup=True)
    if title:
        dialog.set_title(title)
    dialog.set_default_response(Gtk.ResponseType.NO)
    retval = dialog.run() == Gtk.ResponseType.YES
    dialog.destroy()
    return retval
