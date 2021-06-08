class KernelData:
    """ Data class holding information about a kernel """

    def __init__(self):
        self.version_id = None
        self.version = None
        self.pkg_version = None
        self.series = None
        self.type = None
        self.name = ""
        self.suffix = ""
        self.installed = False
        self.is_auto_installed = False
        self.used = False
        self.origin = 0
        self.release = ""
        self.support_duration = 0
        self.support_status = ""
