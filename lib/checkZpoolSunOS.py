# Copyright (c) 2012 Lucien Hercaud <hercaud@hercaud.com>
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
from rcUtilities import justcall
import os
import re
 
class check(checks.check):
    """
    # zpool status
      pool: rpool
     state: DEGRADED
    status: One or more devices has been taken offline by the administrator.
            Sufficient replicas exist for the pool to continue functioning in a
            degraded state.
    action: Online the device using 'zpool online' or replace the device with
            'zpool replace'.
     scrub: scrub completed after 0h40m with 0 errors on Sun Jun 24 05:41:27 2012
    config:
 
            NAME          STATE     READ WRITE CKSUM
            rpool         DEGRADED     0     0     0
              mirror      DEGRADED     0     0     0
                c0t0d0s0  ONLINE       0     0     0
                c0t1d0s0  OFFLINE      0     0     0
    """
    chk_type = "zpool"
 
    def find_svc(self, pool):
        devs = []
        cmd = ['zpool', 'status', pool]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return ''
        lines = out.split('\n')
        getdevs = 0
        for line in lines:
            if line.startswith('errors:'):
                getdevs = 0
                continue
            if len(line) < 2:
                continue
            if 'NAME' in line and 'STATE' in line and 'CKSUM' in line:
                getdevs = 1
                continue
            if pool in line and getdevs == 1:
                getdevs = 2
                continue
            if getdevs == 2 and ( 'mirror' in line or 'raidz' in line) :
                continue
            if getdevs == 2:
                l = line.split()
                if 'd0' in l[0] and l[0].startswith('c'):
                    d = l[0]
                    if re.match("^.*[sp][0-9]*$", d) is not None:
                        # partition, substitute s2 to given part
                       regex = re.compile("[sp][0-9]*$", re.UNICODE)
                       d = regex.sub("s2", d)
                    else:
                        # base device, append s2
                        d += 's2'
                    if os.path.exists('/dev/rdsk/'+d):
                        devs.append(d)
        for d in devs:
            for svc in self.svcs:
                if '/dev/rdsk/'+d in svc.disklist():
                    return svc.svcname
        return ''
 
    def do_check(self):
        cmd = ['zpool', 'list', '-H', '-o', 'name,health']
        out, err, ret = justcall(cmd)
        if ret != 0:
            return self.undef
        lines = out.split('\n')
        if len(lines) < 2:
            return self.undef
        r = []
        for line in lines:
            if len(line) < 2:
                continue
            l = line.split()
            pool = l[0]
            stat = l[1]
            if stat == 'ONLINE':
                stat_val = 0
            elif stat == 'DEGRADED':
                stat_val = 1
            elif stat == 'FAULTED':
                stat_val = 2
            elif stat == 'OFFLINE':
                stat_val = 3
            elif stat == 'REMOVED':
                stat_val = 4
            elif stat == 'UNAVAIL':
                stat_val = 5
            else:
                stat_val = 6
            if pool is not None:
                r.append({'chk_instance': pool,
                          'chk_value': str(stat_val),
                          'chk_svcname': self.find_svc(pool),
                        })
        return r
