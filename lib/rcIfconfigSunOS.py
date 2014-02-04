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

from subprocess import *

import rcIfconfig

class ifconfig(rcIfconfig.ifconfig):

    def __init__(self, ifconfig=None):
        self.intf = []
        if ifconfig is not None:
            out = ifconfig
        else:
            out = Popen(['/usr/sbin/ifconfig', '-a'], stdin=None, stdout=PIPE,stderr=PIPE,close_fds=True).communicate()[0]
        self.parse(out)

    def set_hwaddr(self, i):
        if i is None or i.hwaddr != '' or ':' not in i.name:
            return i
        base_ifname, index = i.name.split(':')
        base_intf = self.interface(base_ifname)
        if base_intf is not None and len(base_intf.hwaddr) > 0:
            i.hwaddr = base_intf.hwaddr
        else:
            d = self.load_arp()
            if base_ifname in d:
                i.hwaddr = d[base_ifname]
        return i

    def load_arp(self):
        d = {}
        cmd = ['/usr/sbin/arp', '-n', '-a']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return d
        for line in out.split('\n'):
            if '.' not in line:
                continue
            l = line.split()
            if ':' not in l[-1]:
                continue
            d[l[0]] = l[-1]
        return d

    def parse(self, out):
        i = None
        for l in out.split("\n"):
            if l == '' : break
            if l[0]!='\t' :
                i = self.set_hwaddr(i)
                (ifname,ifstatus)=l.split(': ')

                i=rcIfconfig.interface(ifname)
                self.intf.append(i)

                # defaults
                i.link_encap = ''
                i.scope = ''
                i.bcast = ''
                i.mask = ''
                i.mtu = ''
                i.ipaddr = ''
                i.ip6addr = []
                i.ip6mask = []
                i.hwaddr = ''
                i.flag_up = False
                i.flag_broadcast = False
                i.flag_running = False
                i.flag_multicast = False
                i.flag_ipv4 = False
                i.flag_ipv6 = False
                i.flag_loopback = False

                if 'UP' in ifstatus : i.flag_up = True
                elif 'BROADCAST' in ifstatus : i.flag_broadcast = True
                elif 'RUNNING' in ifstatus   : i.flag_running = True
                elif 'MULTICAST' in ifstatus : i.flag_multicast = True
                elif 'IPv4' in ifstatus      : i.flag_ipv4 = True
                elif 'IPv6' in ifstatus      : i.flag_ipv6 = True
            else:
                n=0
                w=l.split()
                while n < len(w) :
                    [p,v]=w[n:n+2]
                    if p == 'inet' : i.ipaddr=v
                    elif p == 'netmask' : i.mask=v
                    elif p == 'broadcast' : i.bcast=v
                    elif p == 'ether' : i.hwaddr=v
                    elif p == 'inet6' :
                        (a, m) = v.split('/')
                        i.ip6addr += [a]
                        i.ip6mask += [m]
                    n+=2
        i = self.set_hwaddr(i)


if __name__ == "__main__":
    #help(ifconfig)
    for i in ifconfig().intf:
        print(i)
