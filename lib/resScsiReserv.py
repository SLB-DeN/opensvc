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

import resources as Res
import uuid
import re
import time
import rcStatus
import rcExceptions as ex
from rcUtilities import which
from subprocess import *
from rcGlobalEnv import rcEnv
hostId = __import__('hostid'+rcEnv.sysname)


class ScsiReserv(Res.Resource):
    """Define method to acquire and release scsi SPC-3 persistent reservations
    on disks held by a service
    """
    def __init__(self, rid=None, disks=set([])):
        self.hostid = '0x'+hostId.hostid()
        self.disks = disks
        if len(', '.join(disks)) > 48:
            self.label = 'preserv '+', '.join(disks)[0:48]
            self.label += " ..."
        else:
            self.label = ', '.join(disks)
        self.preempt_timeout = 10
        self.prtype = '5'
        Res.Resource.__init__(self, rid=rid+"pr", type="disk.scsireserv")

    def scsireserv_supported(self):
        return False

    def ack_unit_attention(self, d):
        raise ex.notImplemented

    def disk_registered(self, disk):
        raise ex.notImplemented

    def disk_register(self, disk):
        raise ex.notImplemented

    def disk_unregister(self, disk):
        raise ex.notImplemented

    def get_reservation_key(self, disk):
        raise ex.notImplemented

    def disk_reserved(self, disk):
        raise ex.notImplemented

    def disk_release(self, disk):
        raise ex.notImplemented

    def disk_reserve(self, disk):
        raise ex.notImplemented

    def disk_preempt_reservation(self, disk, oldkey):
        raise ex.notImplemented

    def ack_all_unit_attention(self):
        for d in self.disks:
            try:
                if self.ack_unit_attention(d) != 0:
                    return 1
            except ex.excScsiPrNotsupported:
                continue
        return 0

    def register(self):
        r = 0
        for d in self.disks:
            try:
                r += self.ack_unit_attention(d)
                r += self.disk_register(d)
            except ex.excScsiPrNotsupported:
                continue
        return r

    def unregister(self):
        r = 0
        for d in self.disks:
            try:
                r += self.ack_unit_attention(d)
                if not self.disk_registered(d):
                    continue
                r += self.disk_unregister(d)
            except ex.excScsiPrNotsupported:
                continue
        return r

    def disk_wait_reservation(self, disk):
        i = 0
        while i>0:
            i -= 1
            if self.disk_reserved(disk):
                self.log.info("reservation acquired for disk %s" % disk)
                return 0
            time.sleep(1)
        self.log.error("timed out waiting for reservation for disk %s" % disk)
        return 1

    def reserve(self):
        r = 0
        for d in self.disks:
            try:
                r += self.ack_unit_attention(d)
                key = self.get_reservation_key(d)
                if key is None:
                    r += self.disk_reserve(d)
                elif key == self.hostid:
                    continue
                else:
                    r += self.disk_preempt_reservation(d, key)
                    r += self.disk_wait_reservation(d)
            except ex.excScsiPrNotsupported:
                continue
        return r

    def release(self):
        r = 0
        for d in self.disks:
            try:
                r += self.ack_unit_attention(d)
                if not self.disk_reserved(d):
                    continue
                r += self.disk_release(d)
            except ex.excScsiPrNotsupported:
                continue
        return r

    def checkreserv(self):
        if self.ack_all_unit_attention() != 0:
            return rcStatus.WARN
        r = rcStatus.Status()
        for d in self.disks:
            try:
                key = self.get_reservation_key(d)
                if key is None:
                    self.log.debug("disk %s is not reserved" % d)
                    r += rcStatus.DOWN
                elif key != self.hostid:
                    self.log.debug("disk %s is reserved by another host whose key is %s" % (d, key))
                    r += rcStatus.DOWN
                else:
                    self.log.debug("disk %s is correctly reserved" % d)
                    r += rcStatus.UP
            except ex.excScsiPrNotsupported:
                continue
        return r.status

    def scsireserv(self):
        if not self.scsireserv_supported():
            return
        r = 0
        r += self.register()
        r += self.reserve()
        return r

    def scsirelease(self):
        if not self.scsireserv_supported():
            return
        r = 0
        r += self.release()
        r += self.unregister()
        return r

    def scsicheckreserv(self):
        if not self.scsireserv_supported():
            return
        return self.checkreserv()

    def status(self):
        if not self.scsireserv_supported():
            return rcStatus.NA
        return self.scsicheckreserv()

    def start(self):
        if not self.scsireserv_supported():
            return
        if self.scsireserv() != 0:
            raise ex.excError

    def stop(self):
        if not self.scsireserv_supported():
            return
        if self.scsirelease() != 0:
            raise ex.excError


