#
# Copyright (c) 2012 Christophe Varoqui <christophe.varoqui@opensvc.com>
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
import os

from rcGlobalEnv import rcEnv
import rcStatus
import resources as Res
from rcUtilities import which, qcall, justcall
import resContainer
import rcExceptions as ex

class Srp(resContainer.Container):
    def files_to_sync(self):
        return [self.export_file]

    def get_rootfs(self):
        return self.get_state()['state']

    def rcp(self, src, dst):
        rootfs = self.get_rootfs()
        if len(rootfs) == 0:
            raise ex.excError()
        dst = rootfs + dst
        cmd = ['cp', src, dst]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def install_drp_flag(self):
        rootfs = self.get_rootfs()
        flag = os.path.join(rootfs, ".drp_flag")
        self.log.info("install drp flag in container : %s"%flag)
        with open(flag, 'w') as f:
            f.write(' ')
            f.close()

    def container_start(self):
        cmd = ['srp', '-start', self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError()

    def container_stop(self):
        cmd = ['srp', '-stop', self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError()

    def container_forcestop(self):
        raise ex.excError

    def operational(self):
        cmd = self.runmethod + ['pwd']
        ret = qcall(cmd)
        if ret == 0:
            return True
        return False

    def get_verbose_list(self):
        """
        Name: iisdevs1  Template: system Service: provision ID: 1
        ----------------------------------------------------------------------

        autostart=0
        srp_name=iisdevs1
        ...
        """
        cmd = ['srp', '-list', self.name, '-v']
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("srp -list returned %d:\n%s"%(ret, err))

        data = {}

        words = out.split()
        for i, w in enumerate(words):
            if w == "Service:":
                service = words[i+1]
            if '=' in w:
                key = service + '.' + w[:w.index('=')]
                val = w[w.index('=')+1:]
                data[key] = val
        return data

    def get_verbose_status(self):
        """
        SRP Status:

        ----------------- Status for SRP:iisdevs1 ----------------------
            Status:MAINTENANCE

            Type:system    Subtype:private    Rootpath:/var/hpsrp/iisdevs1

            IP:10.102.184.12      Interface:lan0:1 (DOWN)   id: 1
            MEM Entitle:50.00%    MEM Max:(none)    Usage:0.00%
            CPU Entitle:9.09%    CPU Max:(none)    Usage:0.00%
        """
        cmd = ['srp', '-status', self.name, '-v']
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("srp -status returned %d:\n%s"%(ret, err))

        data = {'ip': [], 'mem': {}, 'cpu': {}}

        words = out.split()
        for i, w in enumerate(words):
            if w.startswith('IP'):
                ip = w.replace('IP:','')
                intf = words[i+1].replace('Interface:','')
                state = words[i+2].strip('(').strip(')')
                id = words[i+4]
                data['ip'].append({
                  'ip': ip,
                  'intf': intf,
                  'state': state,
                  'id': id,
                })
            elif w == "MEM" and words[i+1].startswith("Entitle"):
                entitle = words[i+1].replace('Entitle:','')
                max = words[i+3].replace('Max:','')
                usage = words[i+4].replace('Usage:','')
                data['mem'] = {
                  'entitle': entitle,
                  'max': max,
                  'usage': usage,
                }
            elif w == "CPU" and words[i+1].startswith("Entitle"):
                entitle = words[i+1].replace('Entitle:','')
                max = words[i+3].replace('Max:','')
                usage = words[i+4].replace('Usage:','')
                data['cpu'] = {
                  'entitle': entitle,
                  'max': max,
                  'usage': usage,
                }
        return data

    def get_status(self, nodename=None):
        """
        NAME         TYPE      STATE       SUBTYPE    ROOTPATH
        iisdevs1     system    maintenance private    /var/hpsrp/iisdevs1
        """
        cmd = ['srp', '-status', self.name]
        if nodename is not None:
            cmd = rcEnv.rsh.split() + [nodename] + cmd

        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("srp -status returned %d:\n%s"%(ret, err))
        lines = out.split('\n')
        if len(lines) < 2:
            raise ex.excError("srp -status output too short:\n%s"%out)
        l = lines[1].split()
        if l[0] != self.name:
            raise ex.excError("srp -status second line, first entry does not match container name")
        if len(l) != 5:
            raise ex.excError("unexpected number of entries in %s"%str(l))
        _type, _state, _subtype, _rootpath = l[1:]
        return {
          'type': l[1],
          'state': l[2],
          'subtype': l[3],
          'rootpath': l[4],
        }

    def is_down(self):
        d = self.get_status()
        if d['state'] == 'stopped':
            return True
        return False

    def is_up_on(self, nodename):
        return self.is_up(nodename)

    def is_up(self, nodename=None):
        d = self.get_status(nodename)
        if d['state'] == 'started':
            return True
        return False

    def get_container_info(self):
        return {'vcpus': '0', 'vmem': '0'}

    def check_manual_boot(self):
        try:
            val = self.get_verbose_list()['init.autostart']
        except ex.excError:
            return False
        if val == 'yes' or val == '1':
            return False
        return True

    def check_capabilities(self):
        if not which('srp'):
            self.log.debug("srp is not in PATH")
            return False
        return True

    def presync(self):
        self.container_export()

    def postsync(self):
        self.container_import()

    def container_import(self):
        if not os.path.exists(self.export_file):
            raise ex.excError("%s does not exist"%self.export_file)
        cmd = ['srp', '-batch', '-import', '-xfile', self.export_file, 'allow_sw_mismatch=yes', 'autostart=no']
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError()

    def container_export(self):
        cmd = ['srp', '-batch', '-export', self.name, '-xfile', self.export_file]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError()

    def __init__(self, rid, name, guestos="HP-UX", optional=False, disabled=False, monitor=False,
                 tags=set([]), always_on=set([])):
        resContainer.Container.__init__(self, rid=rid, name=name,
                                        type="container.srp",
                                        guestos=guestos,
                                        optional=optional, disabled=disabled,
                                        monitor=monitor, tags=tags, always_on=always_on)
        self.export_file = os.path.join(rcEnv.pathvar, name + '.xml')
        self.runmethod = ['srp_su', name, 'root', '-c']

    def provision(self):
        m = __import__("provSrp")
        prov = m.ProvisioningSrp(self)
        prov.provisioner()

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

