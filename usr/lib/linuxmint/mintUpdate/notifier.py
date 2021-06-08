#!/usr/bin/python3

import os

from common import settings
from common.constants import (AUTOMATIC_UPGRADES_LOGFILE, REBOOT_REQUIRED_FILE,
                              UPDATE_FAILED_FILE)


def notify(path, setting, summary, body):
    mtime = int(os.path.getmtime(path))
    if mtime != settings.get_int64(setting):
        Notify.Notification.new(summary, body, "mintupdate").show()
        settings.set_int64(setting, mtime)

reboot_required = os.path.exists(REBOOT_REQUIRED_FILE)
update_failed = os.path.exists(UPDATE_FAILED_FILE)
if reboot_required or update_failed:
    import gi
    gi.require_version('Notify', '0.7')
    from gi.repository import Notify

    if Notify.init("mintUpdate_notifier"):
        if reboot_required:
            notify(REBOOT_REQUIRED_FILE,
                   "notifier-reboot-required-notified",
                   _("Reboot required"),
                   _("You have installed updates that require a reboot to take effect, "
                     "please reboot your system as soon as possible."))
        if update_failed:
            notify(UPDATE_FAILED_FILE,
                   "notifier-autoupdate-failure-notified",
                   _("Automatic updates error"),
                   _("The automatic update service returned an error, for details "
                     "please check the log file at %s") % AUTOMATIC_UPGRADES_LOGFILE)
        Notify.uninit()
