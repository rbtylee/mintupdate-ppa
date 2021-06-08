import json
import os
import subprocess
import time
import traceback
from datetime import datetime

from common import settings
from common.constants import SUPPORTED_KERNEL_TYPES


def read_file(path):
    """ Return list of lines in given file path, empty if the path does not exist """
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.readlines()
        except:
            pass
    return [""]

def get_release_dates():
    """ Get distro release dates for support duration calculation """
    release_dates = {}
    distro_info = []
    data_files = ("/usr/share/distro-info/ubuntu.csv", "/usr/share/distro-info/debian.csv")
    for data_file in data_files:
        distro_info += read_file(data_file)
    if distro_info:
        for distro in distro_info[1:]:
            try:
                distro = distro.split(",")
                release_date = time.mktime(time.strptime(distro[4], '%Y-%m-%d'))
                release_date = datetime.fromtimestamp(release_date)
                support_end = time.mktime(time.strptime(distro[5].rstrip(), '%Y-%m-%d'))
                support_end = datetime.fromtimestamp(support_end)
                release_dates[distro[2]] = [release_date, support_end]
            except:
                pass
    return release_dates

def configured_kernel_type():
    """ Return the kernel flavour configured in settings, if supported, else "-generic" """
    kernel_type = settings.get_string("selected-kernel-type")
    if kernel_type not in SUPPORTED_KERNEL_TYPES:
        kernel_type = "-generic"
    return kernel_type

def check_timeshift():
    """ Returns True if timeshift exists and is set up """
    if not os.path.exists("/usr/bin/timeshift"):
        return False
    if os.path.isfile("/etc/timeshift.json"):
        try:
            with open("/etc/timeshift.json", encoding="utf-8") as f:
                data = json.load(f)
                if "backup_device_uuid" in data and data["backup_device_uuid"]:
                    return True
        except:
            print(f"Exception while checking Timeshift configuration:\n{traceback.format_exc()}")
    return False

def get_max_snapshots():
    """ Parse /etc/mintupdate-system-snapshots.conf and return the MAX_SNAPSHOTS value """
    max_snapshots = 5
    for line in read_file("/etc/mintupdate-system-snapshots.conf"):
        if line.startswith("MAX_SNAPSHOTS="):
            value = line.strip().split("MAX_SNAPSHOTS=")[1]
            if value.isnumeric():
                max_snapshots = int(value)
    return max_snapshots

def dpkg_locked():
    """ Returns `True` if a process has a handle on /var/lib/dpkg/lock (no check for write lock) """
    try:
        subprocess.run(["sudo", "/bin/fuser", "-s", "/var/lib/dpkg/lock"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except subprocess.CalledProcessError:
        return False
