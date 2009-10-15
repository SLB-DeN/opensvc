#
# Copyright (c) 2009 Christophe Varoqui <christophe.varoqui@free.fr>'
# Copyright (c) 2009 Cyril Galibern <cyril.galibern@free.fr>'
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
# To change this template, choose Tools | Templates
# and open the template in the editor.

import svc
import lxc

class svcLxc(svc.Svc):
    """ Define Lxc services"""

    def __init__(self, svcname, optional=False, disabled=False):
        svc.Svc.__init__(self, svcname, optional, disabled)
        self += lxc.Lxc(svcname)

    def action(self,action=None):
        print "Calling action %s on %s" % (action,self.__class__.__name__)
        if action == "start" : self.start()
        if action == "stop" : self.start()

    def start(self):
        """start a Lxc
        check ping
        start loops
        start VGs
        start mounts
        start lxc
        start apps
        """
        print "starting %s" % self.__class__.__name__
        self.subSetAction("ip", "check_ping")
        self.subSetAction("loop", "start")
        self.subSetAction("vg", "start")
        self.subSetAction("mount", "start")
        self.subSetAction("lxc", "start")
        self.subSetAction("app", "start")

    def stop(self):
        """stop a zone:
        stop apps
        stop lxc
        stop mounts
        stop VGs
        stop loops
        """
        print "stopping %s" % self.__class__.__name__
        self.subSetAction("app", "stop")
        self.subSetAction("lxc", "stop")
        self.subSetAction("mount", "stop")
        self.subSetAction("vg", "stop")
        self.subSetAction("loop", "stop")

    def status(self):
        """status of the resources of a Lxc service"""
        self.subSetAction("ip", "status")
        self.subSetAction("loop", "status")
        self.subSetAction("vg", "status")
        self.subSetAction("mount", "status")
        self.subSetAction("lxc", "status")
        self.subSetAction("app", "status")

    def startlxc(self):
        self.subSetAction("lxc", "start")

    def stoplxc(self):
        self.subSetAction("lxc", "stop")

    def startapp(self):
        self.subSetAction("app", "start")

    def stopapp(self):
        self.subSetAction("app", "stop")

    def startip(self):
        self.subSetAction("ip", "check_ping")
        self.subSetAction("ip", "start")

    def stopip(self):
        self.subSetAction("ip", "stop")

    def startloop(self):
        self.subSetAction("loop", "start")

    def stoploop(self):
        self.subSetAction("loop", "stop")

    def startvg(self):
        self.subSetAction("vg", "start")

    def stopvg(self):
        self.subSetAction("vg", "stop")

    def mount(self):
        self.subSetAction("mount", "start")

    def umount(self):
        self.subSetAction("mount", "stop")


if __name__ == "__main__":
    for c in (svcLxc,) :
        help(c)
    import mountLinux as mount
    import ipLinux as ip
    print """
    Z=svcLxc()
    Z+=mount.Mount("/mnt1","/dev/sda")
    Z+=mount.Mount("/mnt2","/dev/sdb")
    Z+=ip.Ip("eth0","192.168.0.173")
    Z+=ip.Ip("eth0","192.168.0.174")
    """

    Z=svcLxc()
    Z+=mount.Mount("/mnt1","/dev/sda")
    Z+=mount.Mount("/mnt2","/dev/sdb")
    Z+=ip.Ip("eth0","192.168.0.173")
    Z+=ip.Ip("eth0","192.168.0.174")

    print "Show Z: ", Z
    print "start Z:"
    Z.start()

    print "stop Z:"
    Z.stop()

