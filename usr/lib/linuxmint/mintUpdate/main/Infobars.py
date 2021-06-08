import os
import traceback
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import pycurl

from common import settings
from common.functions import get_release_dates, read_file
from main.constants import DISTRO_INFO


class Infobars:

    def __init__(self, application):
        self.application = application
        self.infobar = self.application.builder.get_object("hbox_infobar")
        self.base_release = self.get_base_release_codename()

    def show_infobar(self, infobar_id, title, msg, msg_type=Gtk.MessageType.WARNING,
                     icon=None, callback=None):
        if self.infobar_is_shown(infobar_id):
            return
        infobar = Gtk.InfoBar()
        infobar.id = infobar_id
        infobar.set_margin_bottom(2)
        infobar.set_message_type(msg_type)
        if not icon:
            if msg_type == Gtk.MessageType.WARNING:
                icon = "dialog-warning-symbolic"
            elif msg_type == Gtk.MessageType.ERROR:
                icon = "dialog-error-symbolic"
            elif msg_type == Gtk.MessageType.QUESTION:
                icon = "dialog-question-symbolic"
            elif msg_type == Gtk.MessageType.INFO:
                icon = "dialog-information-symbolic"
            else:
                icon = "dialog-warning-symbolic"
        img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        infobar.get_content_area().pack_start(img, False, False, 0)

        info_label = Gtk.Label()
        info_label.set_line_wrap(True)
        info_label.set_markup(f"<b>{title}</b>\n{msg}")
        infobar.get_content_area().pack_start(info_label, False, False, 0)
        if callback:
            if msg_type == Gtk.MessageType.QUESTION:
                infobar.add_button(_("Yes"), Gtk.ResponseType.YES)
                infobar.add_button(_("No"), Gtk.ResponseType.NO)
            else:
                infobar.add_button(_("OK"), Gtk.ResponseType.OK)
            infobar.connect("response", callback)
        infobar.show_all()
        self.infobar.pack_start(infobar, True, True, 0)

    def remove_infobar(self, infobar_id):
        for child in self.infobar.get_children():
            if child.id == infobar_id:
                child.destroy()

    def infobar_is_shown(self, infobar_id):
        for child in self.infobar.get_children():
            if child.id == infobar_id:
                return True
        return False

    def run_status_checks(self, root_mode, thread=None):
        """ Runs various status checks and shows infobars where appropriate """
        self.eol_check()
        if DISTRO_INFO["ID"] == "LinuxMint":
            self.mint_release_upgrade_notification()
            self.mint_mirror_check(root_mode)
        self.reboot_required_check()
        if thread:
            thread.set()

    def reboot_required_check(self):
        """ Check reboot required flag and display infobar where applicable """
        infobar_id = "reboot_required"
        if (not self.application.reboot_required and not os.path.exists("/var/run/reboot-required")) or \
           self.infobar_is_shown(infobar_id):
            return
        title = _("Reboot required")
        msg = _("You have installed updates that require a reboot to take effect, "
                "please reboot your system as soon as possible.")
        self.application.infobars.show_infobar(infobar_id, title, msg)

        # Desktop notification
        self.application.show_notification(title, msg)

    def eol_check(self):
        """ Check distro eol status and display infobar where applicable """
        self.application.is_end_of_life, show_eol_warning, eol_date = self.get_eol_status()

        infobar_id = "eol"
        if show_eol_warning and settings.get_boolean("warn-about-distribution-eol"):
            if self.infobar_is_shown(infobar_id):
                return
            release_name = DISTRO_INFO["DESCRIPTION"]
            if not release_name:
                release_name = _("distribution")
            eol_date = eol_date.strftime('%x')
            infobar_message = "%s\n\n%s %s" % (
                _(f"Your {release_name} is only supported until {eol_date}."),
                _("Your system will remain functional after that date, but the official software repositories will "
                  "become unavailable along with any updates including security updates."),
                _("You should perform an upgrade to or a clean install of a newer version of your distribution "
                  "before that happens."))
            if DISTRO_INFO["ID"] == "LinuxMint":
                infobar_message += "\n\n%s" % \
                    (_("For more information visit %s.") % "<a href='https://www.linuxmint.com'>https://www.linuxmint.com</a>")
            self.show_infobar(infobar_id,
                              _("DISTRIBUTION END OF LIFE WARNING"),
                              infobar_message,
                              Gtk.MessageType.WARNING,
                              callback=self.on_infobar_eol_response)
        else:
            self.remove_infobar(infobar_id)

    @staticmethod
    def get_eol_status():
        """ Checks if distribution has reached end of life (EOL)

        Returns:
        * is_eol: True if EOL
        * show_eol_warning: True if early_warning_days > EOL - now
        * eol_date: datetime object of EOL date
        """
        early_warning_days = 90
        is_eol = False
        eol_date = None
        show_eol_warning = False
        try:
            release_dates = get_release_dates()
            if release_dates:
                if self.base_release and self.base_release in release_dates.keys():
                    now = datetime.now()
                    eol_date = release_dates[self.base_release][1]
                    is_eol =  now > eol_date
                    show_eol_warning =  (eol_date - now).days <= early_warning_days
        except:
            pass
        return (is_eol, show_eol_warning, eol_date)

    @staticmethod
    def get_base_release_codename():
        """ Parses /etc/os-release for UBUNTU_CODENAME or DEBIAN_CODENAME, returns `None` on failure """
        release_data = read_file("/etc/os-release")
        return next((x.split("=", 1)[1].strip() for x in release_data
                     if x.startswith("UBUNTU_CODENAME=") or x.startswith("DEBIAN_CODENAME=")), None)

    @staticmethod
    def get_url_last_modified(url):
        """
        Returns

        a `datetime` object with the resource time as retrieved by cURL,

        `True` when no time was received or the protocol was not supported,

        `False` in all other cases
        """
        try:
            c = pycurl.Curl()
            c.setopt(pycurl.URL, url)
            c.setopt(pycurl.CONNECTTIMEOUT, 5)
            c.setopt(pycurl.TIMEOUT, 5)
            c.setopt(pycurl.FOLLOWLOCATION, 1)
            c.setopt(pycurl.NOBODY, 1)
            c.setopt(pycurl.OPT_FILETIME, 1)
            c.perform()
            filetime = c.getinfo(pycurl.INFO_FILETIME)
            if filetime > 0:
                return datetime.fromtimestamp(filetime)
            else:
                # no valid time received
                return True
        except pycurl.error as e:
            if e.args[0] == pycurl.E_UNSUPPORTED_PROTOCOL:
                # unsupported protocol
                return True
        except:
            pass
        return False

    def get_test_url(self, mirror_config, mirror_type, url_type):
        """ Returns a URL to be used for Last-Modified tests on Mint or Ubuntu (base) repository servers """
        if mirror_type == "main":
            return f"{mirror_config[mirror_type][url_type]}/dists/{DISTRO_INFO['CODENAME']}/Release"
        else:
            return f"{mirror_config[mirror_type][url_type]}/dists/{self.base_release}-updates/InRelease"

    def mint_mirror_check(self, root_mode=False):
        """ Mirror-related notifications (Mint only) """
        infobar_id = "software-sources"
        self.remove_infobar(infobar_id)

        # Maximum acceptable mirror desync in days
        max_mirror_age = 2
        distro_conf_path = f"/usr/share/software-sources/{DISTRO_INFO['CODENAME']}/distro.conf"
        sources_list_path = "/etc/apt/sources.list.d/official-package-repositories.list"
        if not os.path.isfile("/usr/bin/software-sources") or not \
           os.path.isfile(distro_conf_path) or not \
           os.path.isfile(sources_list_path):
            return

        try:
            mirror_config = {
                "main": {"default": "", "identifier": "", "url": ""},
                "base": {"default": "", "identifier": "", "url": ""}
                }
            # get default mirror URLs and identifiers:
            for line in read_file(distro_conf_path):
                if line.startswith("default="):
                    mirror_config["main"]["default"] = line.split("=", 1)[1].strip()
                elif line.startswith("base_default="):
                    mirror_config["base"]["default"] = line.split("=", 1)[1].strip()
                elif line.startswith("main_identifier="):
                    mirror_config["main"]["identifier"] = line.split("=", 1)[1].strip()
                elif line.startswith("base_identifier="):
                    mirror_config["base"]["identifier"] = line.split("=", 1)[1].strip()
            # get actual mirror URLs
            for line in read_file(sources_list_path):
                if line.startswith("deb "):
                    if f'{DISTRO_INFO["CODENAME"]} {mirror_config["main"]["identifier"]}' in line:
                        mirror_config["main"]["url"] = line.split()[1].rstrip("/")
                    elif f'{self.base_release}{mirror_config["base"]["identifier"]}' in line:
                        mirror_config["base"]["url"] = line.split()[1].rstrip("/")
            if not mirror_config["main"]["url"] or not mirror_config["base"]["url"]:
                self.application.logger.write_error("Error retrieving mirror URLs, skipping mirror check")
                return

            infobar_message = None

            # Using at least one default repo, suggest to switch
            if mirror_config["main"]["url"] == mirror_config["main"]["default"] or \
               mirror_config["base"]["url"] == mirror_config["base"]["default"]:
                if not settings.get_boolean("default-repo-is-ok"):
                    infobar_title = _("Do you want to switch to a local mirror?")
                    infobar_message = _("Local mirrors are usually faster.")
                    infobar_message_type = Gtk.MessageType.QUESTION
            # Check staleness of local mirrors
            elif root_mode:
                # Only perform up-to-date checks when remote refreshing
                infobar_title = _(f"Please switch to another mirror")
                infobar_message = _infobar_message = ""
                infobar_message_type = Gtk.MessageType.WARNING
                for mirror_type in ("main", "base"):
                    # skip default mirrors
                    if mirror_config[mirror_type]["url"] == mirror_config[mirror_type]["default"]:
                        continue
                    # get default repo date
                    base_date = self.get_url_last_modified(
                        self.get_test_url(mirror_config, mirror_type, "default"))
                    if base_date == True:
                        # unsupported protocol or server does not transmit the resource time
                        continue
                    elif not base_date:
                        # default repo is unreachable, assume no Internet connection and skip the check
                        continue
                    now = datetime.now(tz=base_date.tzinfo)
                    default_mirror_age = (now - base_date).days
                    if not default_mirror_age > max_mirror_age:
                        # default repo was updated within max_mirror_age, no point comparing
                        continue
                    # get mirror date
                    mirror_date = self.get_url_last_modified(
                        self.get_test_url(mirror_config, mirror_type, "url"))
                    if mirror_date == True:
                        # unsupported protocol or server does not transmit the resource time
                        continue
                    elif not mirror_date:
                        _infobar_message = _("%s is unreachable.") % mirror_config[mirror_type]["url"]
                        self.application.logger.write_error(f'{mirror_config[mirror_type]["url"]} is unreachable')
                    else:
                        mirror_age = (base_date - mirror_date).days
                        if mirror_age > max_mirror_age:
                            if mirror_type == "main":
                                # TRANSLATORS: this refers to the "Main" mirror type as seen on the software-sources
                                # tool's Official Repositories tab, please use the same translation
                                _infobar_message = _("The configured main mirror is out of date.")
                            else:
                                # TRANSLATORS: this refers to the "Base" mirror type as seen on the software-sources
                                # tool's Official Repositories tab, please use the same translation
                                _infobar_message = _("The configured base mirror is out of date.")
                            self.application.logger.write_error(
                                f'{mirror_type.capitalize()} mirror {mirror_config[mirror_type]["url"]} '
                                f'is out of date by {mirror_age} days')
                    if _infobar_message:
                        if infobar_message:
                            infobar_message = f"{infobar_message}\n{_infobar_message}"
                        else:
                            infobar_message = _infobar_message
            if infobar_message:
                self.show_infobar(infobar_id,
                                  infobar_title,
                                  infobar_message,
                                  infobar_message_type,
                                  callback=self.on_infobar_softwaresources_response)
                if infobar_message_type == Gtk.MessageType.WARNING:
                    self.application.show_notification(infobar_title, infobar_message)
        except:
            self.application.logger.write_error(
                f"An exception occurred while checking mirror age:\n{traceback.format_exc()}")

    def mint_release_upgrade_notification(self):
        """ Release upgrade notification shown once (Mint only) """
        infobar_id = "rel_upgrade"
        if self.application.notify_release_upgrade:
            infobar_title = _("New Linux Mint point release available!")
            infobar_message = "%s\n%s" % \
                (_("You can upgrade to %s from the Edit menu.") % self.application.notify_release_upgrade[1],
                 _("For more information visit %s.") % "<a href='https://www.linuxmint.com'>https://www.linuxmint.com</a>")

            self.show_infobar(infobar_id, infobar_title,
                infobar_message,
                Gtk.MessageType.INFO, callback=self.on_release_upgrade_response)

            # Desktop notification
            infobar_message = _("You can now upgrade to %s") % self.application.notify_release_upgrade[1]
            self.application.show_notification(infobar_title, infobar_message, show_button=True)

    def on_release_upgrade_response(self, infobar, _response_id):
        infobar.destroy()
        settings.set_string("release-upgrade-notified", self.application.notify_release_upgrade[0])
        self.application.notify_release_upgrade = None

    def on_infobar_softwaresources_response(self, infobar, response_id):
        infobar.destroy()
        if response_id == Gtk.ResponseType.NO:
            settings.set_boolean("default-repo-is-ok", True)
        else:
            self.application.open_repositories()

    @staticmethod
    def on_infobar_eol_response(infobar, _response_id):
        infobar.destroy()
        settings.set_boolean("warn-about-distribution-eol", False)
