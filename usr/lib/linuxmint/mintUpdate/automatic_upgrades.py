#!/usr/bin/python3

import os
import subprocess
import time

from common.constants import (AUTOMATIC_UPGRADES_CONFFILE,
                              AUTOMATIC_UPGRADES_LOGFILE, UPDATE_FAILED_FILE)
from common.functions import dpkg_locked, read_file

# Sleep while the package management system is locked
while dpkg_locked():
    time.sleep(60)

log = open(AUTOMATIC_UPGRADES_LOGFILE, "a")
log.write(f"\n-- Automatic Upgrade starting {time.strftime('%a %d %b %Y %H:%M:%S %Z')}:\n")
log.flush()

pkla_source = "/usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla"
pkla_target = "/etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla"
try:
    # Put shutdown and reboot blocker into place
    os.symlink(pkla_source, pkla_target)
except:
    pass

try:
    # Parse options file
    arguments = []
    if os.path.isfile(AUTOMATIC_UPGRADES_CONFFILE):
        for line in read_file(AUTOMATIC_UPGRADES_CONFFILE):
            line = line.strip()
            if line and not line.startswith("#"):
                arguments.append(line)

    # Run mintupdate-cli through systemd-inhibit
    cmd = ["/bin/systemd-inhibit", '--why="Performing automatic updates"',
           '--who="Update Manager"',  "--what=shutdown", "--mode=block",
           "/usr/bin/mintupdate-cli", "upgrade", "--refresh-cache", "--quiet"]
    cmd.extend(arguments)
    try:
        subprocess.run(cmd, stdout=log, stderr=log, check=True)
    except subprocess.CalledProcessError:
        os.makedirs(os.path.dirname(UPDATE_FAILED_FILE), exist_ok=True)
        open(UPDATE_FAILED_FILE, "w").close()
except:
    import traceback
    log.write("Exception occurred:\n")
    log.write(traceback.format_exc())

try:
    # Remove shutdown and reboot blocker
    os.unlink(pkla_target)
except:
    pass

log.write("-- Automatic Upgrade finished\n")
log.close()
