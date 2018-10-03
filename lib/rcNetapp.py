from __future__ import print_function

import os
from subprocess import *

from six.moves import configparser as ConfigParser
import rcExceptions as ex
from rcGlobalEnv import rcEnv
from rcUtilities import justcall

if rcEnv.paths.pathbin not in os.environ['PATH']:
    os.environ['PATH'] += ":"+rcEnv.paths.pathbin

class Netapps(object):
    def __init__(self, objects=[]):
        self.objects = objects
        if len(objects) > 0:
            self.filtering = True
        else:
            self.filtering = False
        self.arrays = []
        cf = rcEnv.paths.authconf
        if not os.path.exists(cf):
            return
        conf = ConfigParser.RawConfigParser()
        conf.read(cf)
        m = {}

        for s in conf.sections():
            if not conf.has_option(s, "type") or \
               conf.get(s, "type") != "netapp":
                continue

            if self.filtering and not s in self.objects:
                continue

            server = None
            username = None
            password = None

            kwargs = {}

            for key in ("server", "username", "key"):
                try:
                    kwargs[key] = conf.get(s, key)
                except:
                    print("missing parameter: %s", s)
                    continue

            self.arrays.append(Netapp(s, **kwargs))

        del(conf)

    def __iter__(self):
        for array in self.arrays:
            yield(array)

class Netapp(object):
    def __init__(self, name, server=None, username=None, key=None):
        self.name = name
        self.server = server
        self.username = username
        self.key = key
        self.keys = [
          'aggr_show_space',
          'lun_show_v',
          'lun_show_m',
          'sysconfig_a',
          'df',
          'df_S',
          'fcp_show_adapter',
        ]

    def rcmd(self, cmd):
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-i", self.key, self.username+"@"+self.server, cmd]
        out, err, ret = justcall(cmd)
        return out, err

    def get_aggr_show_space(self):
        out, err = self.rcmd("aggr show_space -m")
        return out

    def get_lun_show_v(self):
        out, err = self.rcmd("lun show -v")
        return out

    def get_lun_show_m(self):
        out, err = self.rcmd("lun show -m")
        return out

    def get_sysconfig_a(self):
        out, err = self.rcmd("sysconfig -a")
        return out

    def get_df(self):
        out, err = self.rcmd("df")
        return out

    def get_df_S(self):
        out, err = self.rcmd("df -S")
        return out

    def get_fcp_show_adapter(self):
        out, err = self.rcmd("fcp show adapter")
        return out

if __name__ == "__main__":
    o = Netapps()
    for netapp in o:
        print(netapp.get_aggr_show_space())
        break

