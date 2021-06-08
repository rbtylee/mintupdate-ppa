import html


class Update:

    def __init__(self, package=None, input_string=None, source_name=None):
        self.changelog = None
        self.package_names = []
        self.source_packages = set()
        self.size = 0
        self.installed_size = 0
        self.installed_size_change = 0
        self.main_package_name = ""
        self.description = ""
        self.short_description = ""
        if package:
            self.add_package(package)
            self.package_name = package.name
            self.new_version = package.candidate.version
            if not package.is_installed:
                self.old_version = ""
            else:
                self.old_version = package.installed.version
            self.real_source_name = package.candidate.source_name
            if source_name:
                self.source_name = source_name
            else:
                self.source_name = self.real_source_name
            self.display_name = self.source_name
            self.archive = ""
            if self.new_version != self.old_version:
                self.type = "package"
                self.origin = ""
                for origin in package.candidate.origins:
                    self.origin = origin.origin
                    self.site = origin.site
                    self.archive = origin.archive
                    if origin.origin == "Ubuntu":
                        self.origin = "ubuntu"
                    elif origin.origin == "Debian":
                        self.origin = "debian"
                    elif origin.origin.startswith("LP-PPA"):
                        self.origin = origin.origin
                    if origin.origin == "Ubuntu" and '-security' in origin.archive:
                        self.type = "security"
                        break
                    if origin.origin == "Debian" and '-Security' in origin.label:
                        self.type = "security"
                        break
                    if source_name in ["firefox", "thunderbird"]:
                        self.type = "security"
                        break
                    if origin.origin == "linuxmint":
                        if origin.component == "romeo":
                            self.type = "unstable"
                            break
                if package.candidate.section == "kernel" or \
                   self.package_name.startswith("linux-headers") or \
                   self.real_source_name in ["linux", "linux-kernel", "linux-signed", "linux-meta"]:
                    self.type = "kernel"
        else:
            # Build the class from the input_string
            self.parse(input_string)

    def add_package(self, pkg):
        self.package_names.append(pkg.name)
        self.source_packages.add(f"{pkg.candidate.source_name}={pkg.candidate.source_version}")
        self.size += pkg.candidate.size
        self.installed_size += pkg.candidate.installed_size
        if not pkg.is_installed:
            self.installed_size_change = self.installed_size
        else:
            self.installed_size_change += pkg.candidate.installed_size - pkg.installed.installed_size
        if not self.main_package_name or pkg.name == self.source_name or \
           len(pkg.name) < len(self.main_package_name):
            self.overwrite_main_package(pkg)

    def overwrite_main_package(self, pkg):
        self.description = pkg.candidate.description
        self.short_description = pkg.candidate.summary
        self.main_package_name = pkg.name

    def serialize(self):
        output_string = u"###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s---EOL---" % \
        (self.display_name, self.source_name, self.real_source_name, ", ".join(self.source_packages),
         self.main_package_name, ", ".join(self.package_names), self.new_version,
         self.old_version, self.size, self.installed_size, self.installed_size_change,
         self.type, self.origin, self.short_description, self.description, self.site, self.archive)
        print(output_string.encode("ascii", "xmlcharrefreplace"))

    def parse(self, input_string):
        try:
            parser = html.parser.HTMLParser()
            input_string = html.unescape(input_string)
            del parser
        except:
            pass
        values = input_string.split("###")[1:]
        (self.display_name, self.source_name, self.real_source_name, source_packages,
         self.main_package_name, package_names, self.new_version,
         self.old_version, self.size, self.installed_size, self.installed_size_change,
         self.type, self.origin, self.short_description,
         self.description, self.site, self.archive) = values
        self.size = int(self.size)
        self.installed_size = int(self.installed_size)
        self.installed_size_change = int(self.installed_size_change)
        self.package_names = package_names.split(", ")
        self.source_packages = source_packages.split(", ")
