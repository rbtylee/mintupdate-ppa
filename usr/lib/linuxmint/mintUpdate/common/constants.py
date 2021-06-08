import os

from common import settings

# These updates take priority over other updates and can be installed automatically
# WARNING: Currently it is assumed that source name and package name are identical
PRIORITY_UPDATES = ["mintupdate", "mintupdate-common", "kernel-manager", "mint-release-upgrade", "mint-upgrade-info"]

### KERNELS ###
SUPPORTED_KERNEL_TYPES = ["-generic", "-lowlatency", "-aws", "-azure", "-gcp", "-kvm", "-oem", "-oracle"]
if os.uname().machine == "x86_64":
    SUPPORTED_KERNEL_TYPES.append('-liquorix')
SUPPORTED_KERNEL_TYPES.append('-mfutex')
KERNEL_PKG_NAMES = ['linux-headers-VERSION',
                    'linux-headers-VERSION-KERNELTYPE',
                    'linux-image-VERSION-KERNELTYPE',
                    'linux-modules-VERSION-KERNELTYPE',
                    'linux-modules-extra-VERSION-KERNELTYPE',
                    'linux-image-extra-VERSION-KERNELTYPE', # Naming convention in 16.04, until 4.15 series
                    'linux-headers-VERSION-KERNELTYPE-amd64', # Liquorix
                    'linux-image-VERSION-KERNELTYPE-amd64', # Liquorix
                    ]
USE_MAINLINE_KERNELS = settings.get_boolean("use-mainline-kernels")

# Package origin enum
class Origin:
    # We could have used enum.IntEnum for this but that's needless complexity
    LOCAL = 0
    UBUNTU = 1
    DEBIAN = 2
    MAINLINE_PPA = 3
    OTHER = 4


### FILES ###
ROOT_FUNCTIONS = "/usr/lib/linuxmint/mintUpdate/root_functions.py"
NAMED_PIPE = os.path.join("/run/user/", str(os.getuid()), "mintupdate.fifo")
AUTOMATIC_UPGRADES_CONFFILE = "/etc/mintupdate-automatic-upgrades.conf"
AUTOMATIC_UPGRADES_LOGFILE = "/var/log/mintupdate.log"
REBOOT_REQUIRED_FILE = "/run/reboot-required"
UPDATE_FAILED_FILE = "/var/cache/mintupdate/automatic-upgrades-failed"

# List of variables to pass through pkexec
PKEXEC_ENV = [f"HOME={os.environ.get('HOME')}",
              f"DISPLAY={os.environ.get('DISPLAY')}",
              f"XAUTHORITY={os.environ.get('XAUTHORITY')}"]
