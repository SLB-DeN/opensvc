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
import resources as Res
import os
import rcExceptions as ex
import resContainer

rcU = __import__("rcUtilities" + os.uname()[0])

class Xen(resContainer.Container):
    startup_timeout = 180
    shutdown_timeout = 120

    def __init__(self, name, optional=False, disabled=False, tags=set([])):
        resContainer.Container.__init__(self, rid="xen", name=name, type="container.xen",
                                        optional=optional, disabled=disabled, tags=tags)

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def list_conffiles(self):
        cf = os.path.join(os.sep, 'opt', 'opensvc', 'var', self.name+'.xml')
        if os.path.exists(cf):
            return [cf]
        return []

    def files_to_sync(self):
        return self.list_conffiles()

    def check_capabilities(self):
        cmd = ['virsh', 'capabilities']
        (ret, out) = self.call(cmd, errlog=False)
        if ret != 0:
            self.status_log("can not fetch capabilities")
            return False
        if 'hvm' not in out:
            self.status_log("hvm not supported by host")
            return False
        return True

    def ping(self):
        return rcU.check_ping(self.addr, timeout=1, count=1)

    def container_start(self):
        cf = os.path.join(os.sep, 'opt', 'opensvc', 'var', self.name+'.xml')
        if os.path.exists(cf):
            cmd = ['virsh', 'define', cf]
            (ret, buff) = self.vcall(cmd)
            if ret != 0:
                raise ex.excError
        cmd = ['virsh', 'start', self.name]
        (ret, buff) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_stop(self):
        cmd = ['virsh', 'shutdown', self.name]
        (ret, buff) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_forcestop(self):
        cmd = ['virsh', 'destroy', self.name]
        (ret, buff) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def is_up(self):
        cmd = ['virsh', 'dominfo', self.name]
        (ret, out) = self.call(cmd, errlog=False)
        if ret != 0:
            return False
        if "running" in out.split() or "idle" in out.split() :
            return True
        return False

    def get_container_info(self):
        cmd = ['virsh', 'dominfo', self.name]
        (ret, out) = self.call(cmd, errlog=False, cache=True)
        self.info = {'vcpus': '0', 'vmem': '0'}
        if ret != 0:
            return self.info
        for line in out.split('\n'):
            if "CPU(s):" in line: self.info['vcpus'] = line.split(':')[1].strip()
            if "Max memory" in line: self.info['vmem'] = line.split(':')[1].strip()
            if "Autostart:" in line: self.info['autostart'] = line.split(':')[1].strip()
        return self.info           

    def check_manual_boot(self):
        self.get_container_info()
        if self.info['autostart'] == 'disable' :
                return True
        else:
                return False
