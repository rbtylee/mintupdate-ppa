import json
import os
import subprocess
import tempfile
import threading
import traceback

from common import settings
from common.constants import ROOT_FUNCTIONS
from common.dialogs import show_confirmation_dialog


def size_to_string(size):
    GIGABYTE = 1000 ** 3
    MEGABYTE = 1000 ** 2
    KILOBYTE = 1000

    if size >= GIGABYTE:
        # TRANSLATORS: the abbreviation of gigabyte
        return "%d %s" % (size // GIGABYTE,  _("GB"))
    if size >= MEGABYTE:
        # TRANSLATORS: the abbreviation of megabyte
        return "%d %s" % (size // MEGABYTE,  _("MB"))
    if size >= KILOBYTE:
        # TRANSLATORS: the abbreviation of kilobyte
        return "%d %s" % (size // KILOBYTE,  _("KB"))
    # TRANSLATORS: the abbreviation of byte
    return "%d %s" % (size,  _("B"))

def auto_upgrades_enabled():
    with open("/usr/share/linuxmint/mintupdate/automation/index.json") as f:
        AUTOMATIONS = json.load(f)
    return os.path.isfile(AUTOMATIONS["upgrade"][0])

def export_automation_user_data(automation_id, data):
    """
    Writes `data` to a tempory file and then calls ROOT_FUNCTIONS to copy it to
    its destination.

    Returns the result as a bool, and if run as a threading.Thread, also as the
    `result` property of the Thread object.
    """
    filename = os.path.join(tempfile.gettempdir(), f"mintUpdate/{automation_id}")
    with open(filename, "w") as f:
        f.write(f"{os.linesep.join(data)}{os.linesep}")
    try:
        subprocess.run(["pkexec", ROOT_FUNCTIONS, "automation", automation_id, "enable"], check=True)
        try:
            threading.current_thread().result = True
        except:
            pass
        return True
    except subprocess.CalledProcessError:
        pass
    except:
        print(f"Exception exporting automation user data for `{automation_id}`:\n{traceback.format_exc()}")
    try:
        threading.current_thread().result = False
    except:
        pass
    return False

def check_export_blacklist(transient_for, blacklist=None):
    """ Checks if automatic upgrades are enabled and if yes, prompts the user to export the blacklist """
    if auto_upgrades_enabled():
        message = _("The automatic update service is enabled on this system. "
                    "Do you want it to use your modified ignore list?")
        if show_confirmation_dialog(transient_for, message):
            if not blacklist:
                blacklist = settings.get_strv("blacklisted-packages")
            threading.Thread(target=export_automation_user_data,
                args=("blacklist", blacklist), daemon=False).start()
