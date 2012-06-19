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
import checks
import glob
import re
import os
from rcUtilities import justcall
import rcAdvfs

class check(checks.check):
    def __init__(self, svcs=[]):
        checks.check.__init__(self, svcs)

    chk_type = "fs_u"

    def find_svc(self, name):
        for svc in self.svcs:
            for rs in svc.get_res_sets('pool'):
                for r in rs.resources:
                    if r.poolname == name:
                        return svc.svcname
        return ''

    def do_check(self):
        o = rcAdvfs.Fdmns()
        r = []
        for dom in o.list_fdmns():
            try:
                d = o.get_fdmn(dom)
                r.append({
                          'chk_instance': dom,
                          'chk_value': str(d.used_pct),
                          'chk_svcname': self.find_svc(dom),
                         })
            except rcAdvfs.ExInit:
                pass
        return r
