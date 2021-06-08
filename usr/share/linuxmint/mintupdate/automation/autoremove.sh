#!/bin/sh
while [ $(/bin/fuser -s /var/lib/dpkg/lock) ]; do
    sleep 60
done
ln -s /usr/share/linuxmint/mintupdate/automation/99-mintupdate-temporary.pkla \
    /etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla
echo "\n-- Automatic Removal starting $(date):" >> /var/log/mintupdate.log
DEBIAN_FRONTEND=noninteractive
systemd-inhibit --why="Performing autoremoval" --who="Update Manager" --what=shutdown --mode=block \
    /usr/bin/apt-get autoremove --purge --yes --quiet=2 >> /var/log/mintupdate.log 2>&1
rm -f /etc/polkit-1/localauthority/90-mandatory.d/99-mintupdate-temporary.pkla
echo "\n-- Automatic Removal finished" >> /var/log/mintupdate.log
