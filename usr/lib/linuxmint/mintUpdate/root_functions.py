#!/usr/bin/python3

import os
import subprocess
import sys

from mintcommon import synaptic

from common.constants import PRIORITY_UPDATES, REBOOT_REQUIRED_FILE
from common.functions import get_max_snapshots, read_file

if not os.getuid() == 0 or len(sys.argv) < 2:
    sys.exit(1)

### UTILITY FUNCTIONS ###

def set_reboot_required():
    try:
        if not os.path.exists(REBOOT_REQUIRED_FILE):
            open(REBOOT_REQUIRED_FILE, "w").close()
    except:
        pass

def package_needed_by_another_kernel(apt_cache, version, current_kernel_type):
    from common.constants import KERNEL_PKG_NAMES, SUPPORTED_KERNEL_TYPES

    for kernel_type in SUPPORTED_KERNEL_TYPES:
        if kernel_type == current_kernel_type:
            continue
        for name in KERNEL_PKG_NAMES:
            if "-KERNELTYPE" in name:
                name = name.replace("VERSION", version).replace(
                    "-KERNELTYPE", kernel_type)
                if name in apt_cache:
                    pkg = apt_cache[name]
                    if pkg.is_installed and not pkg.is_auto_installed:
                        return True
    return False

def mark_kernel_packages(state, kernel_version, kernel_type):
    import apt

    from common.constants import KERNEL_PKG_NAMES

    try:
        apt_cache = apt.Cache()
    except:
        sys.exit(1)

    _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
    _KERNEL_PKG_NAMES.append("linux-image-unsigned-VERSION-KERNELTYPE")
    packages = []
    for name in _KERNEL_PKG_NAMES:
        name = name.replace("VERSION", kernel_version).replace(
            "-KERNELTYPE", kernel_type)
        if name in apt_cache:
            pkg = apt_cache[name]
            if pkg.is_installed:
                # skip kernel_type independent packages (headers) if another
                # kernel of the same version but different type is manually
                # installed
                if not kernel_type in name and \
                   package_needed_by_another_kernel(apt_cache, kernel_version, kernel_type):
                    continue
                packages.append(name)
    # set auto-install state
    if packages:
        try:
            subprocess.run(["apt-mark", state] + packages,
                           stdout=subprocess.DEVNULL, check=True)
        except subprocess.CalledProcessError:
            sys.exit(1)
    else:
        sys.exit(1)

### TIMESHIFT ###

def create_snapshot(comment):
    # Get list of existing snapshots
    old_snapshots = get_mintupdate_snapshots()
    # Create new snapshot
    cmd = ["pkexec", "timeshift", "--scripted", "--create", "--comments", comment]
    p = subprocess.run(cmd)
    # Get new list of snapshots
    snapshots = get_mintupdate_snapshots()
    # Check if the snapshot was successfully created by comparing the old and
    # new lists of snapshots
    if len(snapshots) != len(old_snapshots) + 1:
        print("#mintupdate-snapshot-failed", flush=True)
    # Prune snapshots
    print("#mintupdate-pruning-snapshots", flush=True)
    prune_snapshots()
    sys.exit(p.returncode)

def get_mintupdate_snapshots():
    # Get list of all snapshots from timeshift
    try:
        data = subprocess.run(["timeshift", "--list"],
            stdout=subprocess.PIPE, encoding="utf-8").stdout
    except Exception as e:
        print(f"Exception trying to get list of snapshots:\n{e}")
        data = ""

    # Get list of #mintupdate tagged snapshots
    snapshots = []
    for line in data.split("\n"):
        line = line.split()
        if len(line) > 5 and line[-1] == "#mintupdate":
            snapshots.append(line[2])
    snapshots.sort(reverse=True)
    return snapshots

def prune_snapshots(snapshots=None):
    if snapshots == None:
        snapshots = get_mintupdate_snapshots()
    # Delete all tagged snapshots in excess of the maximum configured number of
    # snapshots
    for snapshot in snapshots[get_max_snapshots():]:
        try:
            subprocess.run(["timeshift", "--delete", "--snapshot", snapshot])
        except Exception as e:
            print(f"Exception trying to delete snapshot {snapshot}:\n{e}")

### SELF-UPDATE ###

def self_update(xid, selections_file):
    # Verify that `selections_file` only contains allowed content to ensure
    # this can only be used for the intended purpose, for we do not ask for
    # authorization for self-updates.
    for line in read_file(selections_file):
        line = line.split()
        if len(line) != 2 or \
            line[1] != "install" or \
            line[0] not in PRIORITY_UPDATES:
            sys.exit(1)
    run_synaptic(xid, selections_file)

### SYNAPTIC ###

def run_synaptic(xid, selections_file, closeZvt=True):
    returncode = synaptic.install(xid, selections_file, closeZvt)
    sys.exit(returncode)

### MAINLINE KERNELS ###

def install_mainline_kernel(mode, debfiles):
    import tempfile

    tmpfolder = os.path.join(tempfile.gettempdir(), "mintUpdate/")

    # Make sure we've got valid packages in the argument
    for debfile in debfiles:
        if not debfile.startswith(tmpfolder) or \
           not debfile.endswith(".deb") or \
           not os.path.isfile(debfile):
            sys.exit(2)

    # Install debfiles with dpkg
    p = subprocess.run(["dpkg", "-i"] + debfiles)
    if mode == "upgrade":
        # Mark packages from kernel upgrades as automatically installed
        for debfile in debfiles:
            # Get package name from file
            try:
                package = subprocess.run(["dpkg-deb", "-f", debfile, "Package"],
                    stdout=subprocess.PIPE, encoding="utf-8").stdout.strip()
                if package:
                    subprocess.run(["apt-mark", "auto", package])
                    set_reboot_required()
            except:
                import traceback
                traceback.print_exc()

    # Exit with dpkg's returncode
    sys.exit(p.returncode)

### GRUB FUNCTIONS ###

def grub_set_default(grub_index):
    try:
        subprocess.run(["grub-set-default", grub_index], check=True)
    except:
        sys.exit(1)

def grub_reboot(grub_index):
    try:
        subprocess.run(["grub-reboot", grub_index], check=True)
        subprocess.Popen(["systemctl", "reboot"])
    except:
        sys.exit(1)

### APT FUNCTIONS ###

def apt_unhold(package):
    try:
        subprocess.run(["apt-mark", "unhold", package], check=True)
    except:
        sys.exit(1)

### AUTOMATION ###

def automation(automation_id, action):
    import json

    # import AUTOMATIONS dict
    with open("/usr/share/linuxmint/mintupdate/automation/index.json") as _f:
        AUTOMATIONS = json.load(_f)

    def do_enable(_automation_id):
        filename, name = AUTOMATIONS[_automation_id]
        use_user_file = automation_id == "blacklist" or "-options" in automation_id
        if use_user_file or not os.path.isfile(filename):
            if name == "systemd":
                basename = os.path.basename(filename)
                subprocess.run(["/bin/systemctl", "enable", basename])
                subprocess.run(["/bin/systemctl", "start", basename])
            else:
                default = f"/usr/share/linuxmint/mintupdate/automation/{_automation_id}.default"
                subprocess.run(["cp", default, filename])
                print(f"{name} {filename} created.")
                if use_user_file:
                    copy_user_file(filename)

    def do_disable(_automation_id):
        filename, name = AUTOMATIONS[_automation_id]
        if os.path.isfile(filename):
            if name == "systemd":
                basename = os.path.basename(filename)
                subprocess.run(["/bin/systemctl", "stop", basename])
                subprocess.run(["/bin/systemctl", "disable", basename])
            else:
                subprocess.run(["rm", "-f", filename])
                print(f"{name} {filename} removed.")

    def copy_user_file(outfile):
        import tempfile
        try:
            infile = os.path.join(tempfile.gettempdir(), f"mintUpdate/{automation_id}")
            with open(infile, "r") as export:
                with open(outfile, "a") as f:
                    for line in export:
                        f.write(line)
            os.remove(infile)
            print("User settings exported.")
        except:
            pass

    if action == "enable":
        # Enable the cleanup service in case it was disabled manually
        subprocess.run(["/bin/systemctl", "enable", "mintupdate-automation-cleanup.service"])
        do_enable(automation_id)
        if automation_id == "upgrade":
            do_enable("blacklist")
    else:
        do_disable(automation_id)
    if automation_id in ("upgrade", "autoremove"):
        subprocess.run(["systemctl", "daemon-reload"])

### OPEN APPLICATIONS ###

def set_environment(args):
    for arg in args:
        arg = arg.split("=", 1)
        if arg[0] in ("HOME", "DISPLAY", "XAUTHORITY"):
            os.environ[arg[0]] = arg[1]

def open_software_sources(args):
    try:
        set_environment(args)
        if os.path.exists("/usr/bin/software-sources"):
            subprocess.run(["/usr/bin/software-sources"])
        elif os.path.exists("/usr/bin/software-properties-gtk"):
            subprocess.run(["/usr/bin/software-properties-gtk"])
    except:
        sys.exit(1)

def open_timeshift_gtk(args):
    try:
        set_environment(args)
        subprocess.run(["timeshift-gtk"])
    except:
        sys.exit(1)

### ENTRY POINT ###
if __name__ == "__main__":
    if sys.argv[1] == "timeshift":
        create_snapshot(sys.argv[2])
    elif sys.argv[1] == "prune-snapshots":
        prune_snapshots()
    elif sys.argv[1] == "self-update":
        self_update(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "synaptic":
        if "reboot-required" in sys.argv[3:]:
            set_reboot_required()
        run_synaptic(sys.argv[2], sys.argv[3], "closeZvt" in sys.argv[3:])
    elif sys.argv[1] == "mainline":
        install_mainline_kernel(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "automation":
        automation(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "grub-reboot":
        grub_reboot(sys.argv[2])
    elif sys.argv[1] == "grub-set-default":
        grub_set_default(sys.argv[2])
    elif sys.argv[1] == "open-software-sources":
        open_software_sources(sys.argv[2:])
    elif sys.argv[1] == "open-timeshift-gtk":
        open_timeshift_gtk(sys.argv[2:])
    elif sys.argv[1] == "mark-kernel":
        mark_kernel_packages(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "apt-unhold":
        apt_unhold(sys.argv[2])
    else:
        sys.exit(1)
else:
    sys.exit(1)
