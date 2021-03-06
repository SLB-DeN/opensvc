import os
import rcExceptions as ex
import rcStatus
m = __import__("resDisk")
from subprocess import *
import re

class Disk(m.Disk):
    def __init__(self,
                 rid=None,
                 name=None,
                 container_id=None,
                 **kwargs):
        self.label = "vmdg "+str(name)
        self.container_id = container_id
        m.Disk.__init__(self,
                          rid=rid,
                          name=name,
                          type='disk.vg',
                          **kwargs)

    def has_it(self):
        return True

    def is_up(self):
        return True

    def _status(self, verbose=False):
        return rcStatus.NA

    def do_start(self):
        pass

    def do_stop(self):
        pass

    def sub_devs(self):
        return self.exposed_devs()

    def exposed_devs(self):
        """
            VCC|name=vccname|...
            VDS|name=vdsname|...
            |vol=volname|..|dev=/dev/...|....
            |vol=volname1|..|dev=/dev/...|....
            VDS|name=vdsname1|..
            |vol=volname2|..|dev=/dev/...|....

            ldm list -o disk -p domname
            VERSION
            DOMAIN|..
            VDISK|name=...|vol=volname@vds|...
            VDISK|name=...|vol=volname2@vds2|...
        """
        vdevname2dev = {}
        devs = set()

        cmd = [ '/usr/sbin/ldm', 'list-services' , '-p' ]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, close_fds=True)
        buff = p.communicate()
        if p.returncode != 0:
            raise ex.excError
        vds = ''
        for line in buff[0].split('\n'):
            keys = line.split('|')
            if keys[0] == 'VDS' and len(keys) > 1 :
                for k in keys :
                    name_value = k.split('=')
                    if name_value[0] == 'name' and len(name_value) == 2 :
                        vds = name_value[1]
            elif vds != '' and keys[0] == '':
                volname = ''
                dev = ''
                for k in keys :
                    name_value = k.split('=')
                    if name_value[0] == 'vol' and len(name_value) == 2 :
                        volname = name_value[1]
                    elif name_value[0] == 'dev' and len(name_value) == 2 :
                        dev = name_value[1]
                        if re.match('^/dev/dsk/', dev) is None:
                            continue
                        dev = dev.replace('/dev/dsk/','/dev/rdsk/',1)
                        if re.match('^.*s[0-9]$',dev) is None:
                            dev = dev + 's2'
                        vdevname2dev[volname + '@' + vds ] = dev
            else:
                vds = ''

        cmd = ['/usr/sbin/ldm', 'list', '-o', 'disk', '-p',
               self.svc.resources_by_id[self.container_id].name]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, close_fds=True)
        buff = p.communicate()
        if p.returncode != 0:
            raise ex.excError
        for line in buff[0].split('\n'):
            keys = line.split('|')
            if keys[0] == 'VDISK' and len(keys) > 1 :
                for k in keys :
                    name_value = k.split('=')
                    if name_value[0] == 'vol' and len(name_value) == 2 :
                        vol = name_value[1]
                        if vol in vdevname2dev:
                            devs |= set([ vdevname2dev[vol] ])
        return devs
