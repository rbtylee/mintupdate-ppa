import gettext

from gi.repository import Gio

gettext.install("mintupdate", "/usr/share/locale", names="ngettext")
settings = Gio.Settings.new("com.linuxmint.updates")
