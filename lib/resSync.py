#
# Copyright (c) 2011 Christophe Varoqui <christophe.varoqui@opensvc.com>
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
import logging

import rcExceptions as ex
import resources as Res
import datetime
import time
from rcGlobalEnv import rcEnv
from rcScheduler import *

cache_remote_node_type = {}

class Sync(Res.Resource, Scheduler):
    def __init__(self,
                 rid=None,
                 sync_max_delay=None,
                 schedule=None,
                 optional=False,
                 disabled=False,
                 tags=set([]),
                 type=type,
                 subset=None):
        if sync_max_delay is None:
            self.sync_max_delay = 1500
        else:
            self.sync_max_delay = sync_max_delay

        if schedule is None:
            self.schedule = "03:59-05:59@121"
        else:
            self.schedule = schedule

        Res.Resource.__init__(self,
                              rid=rid,
                              type=type,
                              optional=optional,
                              disabled=disabled,
                              tags=tags,
                              subset=subset)

    def check_timestamp(self, ts, comp='more', delay=10):
        """ Return False if timestamp is fresher than now-interval
            Return True otherwize.
            Zero is a infinite interval
        """
        if delay == 0:
            raise
        limit = ts + datetime.timedelta(minutes=delay)
        if comp == "more" and datetime.datetime.now() < limit:
            return False
        elif comp == "less" and datetime.datetime.now() < limit:
            return False
        else:
            return True
        return True

    def skip_sync(self, ts):
        if not self.svc.cron:
            return False
        if self.skip_action_schedule(self.rid, "sync_schedule", last=ts):
            return True
        return False

    def alert_sync(self, ts):
        if ts is None:
            return True
        if not self.check_timestamp(ts, comp="less", delay=self.sync_max_delay):
            return False
        return True

    def remote_fs_mounted(self, node):
        """
        Verify the remote fs is mounted. Some sync resource might want to abort in
        this case.
        """
        if self.dstfs is None:
            # No dstfs check has been configured. Assume the admin knows better.
            return True
        ruser = self.svc.node.get_ruser(node)
        cmd = rcEnv.rsh.split(' ')+['-l', ruser, node, '--', 'df', self.dstfs]
        (ret, out, err) = self.call(cmd, cache=True)
        if ret != 0:
            raise ex.excError

        """
        # df /zones
        /zones             (rpool/zones       ):131578197 blocks 131578197 files
               ^
               separator !

        # df /zones/frcp03vrc0108/root
        /zones/frcp03vrc0108/root(rpool/zones/frcp03vrc0108/rpool/ROOT/solaris-0):131578197 blocks 131578197 files
                                 ^
                                 no separator !
        """
        if self.dstfs+'(' not in out and self.dstfs not in out.split():
            self.log.error("The destination fs %s is not mounted on node %s. refuse to sync %s to protect parent fs"%(self.dstfs, node, self.dst))
            return False
        return True

    def remote_node_type(self, node, target):
        if target == 'drpnodes':
            expected_type = list(set(rcEnv.allowed_svctype) - set(['PRD']))
        elif target == 'nodes':
            expected_type = [self.svc.svctype]
        else:
            self.log.error('unknown sync target: %s'%target)
            raise ex.excError

        ruser = self.svc.node.get_ruser(node)
        rcmd = [os.path.join(rcEnv.pathbin, 'nodemgr'), 'get', '--param', 'node.host_mode']
        if ruser != "root":
            rcmd = ['sudo'] + rcmd

        if node not in cache_remote_node_type:
            cmd = rcEnv.rsh.split(' ')+['-l', ruser, node, '--'] + rcmd
            (ret, out, err) = self.call(cmd, cache=True)
            if ret != 0:
                return False
            words = out.split()
            if len(words) == 1:
                cache_remote_node_type[node] = words[0]
            else:
                cache_remote_node_type[node] = out

        if cache_remote_node_type[node] in expected_type:
            return True
        self.log.error("incompatible remote node '%s' host mode: '%s' (expected in %s)"%\
                       (node, cache_remote_node_type[node], str(expected_type)))
        return False

