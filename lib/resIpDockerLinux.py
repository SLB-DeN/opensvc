import os
import resIpLinux as Res
import rcExceptions as ex
import rcIfconfigLinux as rcIfconfig
import rcStatus
from rcGlobalEnv import rcEnv
from rcUtilitiesLinux import check_ping
from rcUtilities import which, justcall, to_cidr, lazy, unset_lazy

class Ip(Res.Ip):
    def __init__(self,
                 rid=None,
                 ipdev=None,
                 ipname=None,
                 mask=None,
                 gateway=None,
                 network=None,
                 del_net_route=False,
                 container_rid=None,
                 **kwargs):
        Res.Ip.__init__(self,
                        rid,
                        type="ip.docker",
                        ipdev=ipdev,
                        ipname=ipname,
                        gateway=gateway,
                        mask=mask,
                        **kwargs)
        self.network = network
        self.del_net_route = del_net_route
        self.container_rid = str(container_rid)
        self.label = str(ipname) + '@' + ipdev + '@' + self.container_rid
        self.tags = self.tags | set(["docker"])
        self.tags.add(container_rid)

    def on_add(self):
        self.svc.register_dependency("start", self.rid, self.container_rid)
        self.svc.register_dependency("start", self.container_rid, self.rid)
        self.svc.register_dependency("stop", self.container_rid, self.rid)

    @lazy
    def guest_dev(self):
        """
        Find a free eth netdev.

        Execute a ip link command in the container net namespace to parse
        used eth netdevs.
        """
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip" , "link"]
        out, err, ret = justcall(cmd)
        used = []
        for line in out.splitlines():
            if ": eth" not in line:
                continue
            idx = line.split()[1].replace(":", "").replace("eth", "")
            if "@" in idx:
                # strip "@if<n>" suffix
                idx = idx[:idx.index("@")]
            used.append(int(idx))
        idx = 0
        while True:
            idx += 1
            if idx not in used:
                return "eth%d" % idx

    @lazy
    def container(self):
        if self.container_rid not in self.svc.resources_by_id:
            raise ex.excError("rid %s not found" % self.container_rid)
        return self.svc.resources_by_id[self.container_rid]

    def container_id(self, refresh=False):
        return self.svc.dockerlib.get_container_id_by_name(self.container, refresh=refresh)

    def arp_announce(self):
        """ disable the generic arping. We do that in the guest namespace.
        """
        pass

    def get_docker_ifconfig(self):
        try:
            nspid = self.get_nspid()
        except ex.excError as e:
            return
        if nspid is None:
            return

        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "addr"]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return

        ifconfig = rcIfconfig.ifconfig(ip_out=out)
        return ifconfig

    def abort_start(self):
        if not self.container.docker_service:
            return Res.Ip.abort_start(self)
        return False

    def is_up(self):
        if not self.container.is_up():
            return False
        ifconfig = self.get_docker_ifconfig()
        if ifconfig is None:
            return False
        if ifconfig.has_param("ipaddr", self.addr):
            return True
        return False

    def get_docker_interface(self):
        ifconfig = self.get_docker_ifconfig()
        if ifconfig is None:
            return
        for intf in ifconfig.intf:
            if self.addr in intf.ipaddr+intf.ip6addr:
                name = intf.name
                if "@" in name:
                    name = name[:name.index("@")]
                return name
        return

    def container_running_elsewhere(self):
        if not self.container.docker_service:
            return False
        if len(self.container.service_hosted_instances()) == 0 and \
           len(self.container.service_running_instances()) > 0:
            return True
        return False

    def _status(self, verbose=False):
        unset_lazy(self, "sandboxkey")
        if self.container_running_elsewhere():
            self.status_log("%s is hosted by another host" % self.container_rid, "info")
            return rcStatus.NA
        ret = Res.Ip._status(self)
        if self.container.docker_service and ret == rcStatus.DOWN:
            if check_ping(self.addr, timeout=1, count=1):
                return rcStatus.STDBY_UP
            else:
                self.status_log("ip is not up in the swarm. declare ourself 'stdby down' so we can takeover.")
                return rcStatus.STDBY_DOWN
        return ret

    def startip_cmd(self):
        unset_lazy(self, "sandboxkey")
        if self.container.docker_service and \
           self._status() != rcStatus.STDBY_DOWN:
            return 0, "", ""
        if self.container_running_elsewhere():
            return 0, "", ""

        if "dedicated" in self.tags:
            self.log.info("dedicated mode")
            return self.startip_cmd_dedicated()
        else:
            return self.startip_cmd_shared()

    def startip_cmd_shared(self):
        if os.path.exists("/sys/class/net/%s/bridge" % self.ipdev):
            self.log.info("bridge mode")
            return self.startip_cmd_shared_bridge()
        else:
            self.log.info("macvlan mode")
            return self.startip_cmd_shared_macvlan()

    def startip_cmd_dedicated(self):
        # assign interface to the nspid
        cmd = [rcEnv.syspaths.ip, "link", "set", self.ipdev, "netns", nspid, "name", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # plumb the ip
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "addr", "add", "%s/%s" % (self.addr, to_cidr(self.mask)), "dev", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # activate
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "link", "set", self.guest_dev, "up"]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # add default route
        if self.gateway:
            cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "route", "add", "default", "via", self.gateway, "dev", self.guest_dev]
            ret, out, err = self.vcall(cmd)
            if ret != 0:
                return ret, out, err

        # announce
        if which("arping") is not None:
            cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "arping" , "-c", "1", "-A", "-I", self.guest_dev, self.addr]
            self.log.info(" ".join(cmd))
            out, err, ret = justcall(cmd)

        return 0, "", ""

    def startip_cmd_shared_bridge(self):
        nspid = self.get_nspid()
        tmp_guest_dev = "v%spg%s" % (self.guest_dev, nspid)
        tmp_local_dev = "v%spl%s" % (self.guest_dev, nspid)
        mtu = self.ip_get_mtu()

        # create peer devs
        cmd = [rcEnv.syspaths.ip, "link", "add", "name", tmp_local_dev, "mtu", mtu, "type", "veth", "peer", "name", tmp_guest_dev, "mtu", mtu]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # activate the parent dev
        cmd = [rcEnv.syspaths.ip, "link", "set", tmp_local_dev, "master", self.ipdev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err
        cmd = [rcEnv.syspaths.ip, "link", "set", tmp_local_dev, "up"]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # assign the macvlan interface to the container namespace
        cmd = [rcEnv.syspaths.ip, "link", "set", tmp_guest_dev, "netns", nspid]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # rename the tmp guest dev
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "link", "set", tmp_guest_dev, "name", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # plumb ip
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "addr", "add", self.addr+"/"+to_cidr(self.mask), "dev", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # setup default route
        self.ip_setup_route()

        self.ip_wait()
        return 0, "", ""

    def startip_cmd_shared_macvlan(self):
        nspid = self.get_nspid()

        tmp_guest_dev = "ph%s%s" % (nspid, self.guest_dev)
        mtu = self.ip_get_mtu()

        # create a macvlan interface
        cmd = [rcEnv.syspaths.ip, "link", "add", "link", self.ipdev, "dev", tmp_guest_dev, "mtu", mtu, "type", "macvlan", "mode", "bridge"]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # activate the parent dev
        cmd = [rcEnv.syspaths.ip, "link", "set", self.ipdev, "up"]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # assign the macvlan interface to the container namespace
        cmd = [rcEnv.syspaths.ip, "link", "set", tmp_guest_dev, "netns", nspid]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # rename the tmp guest dev
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "link", "set", tmp_guest_dev, "name", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # plumb the ip
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "addr", "add", "%s/%s" % (self.addr, to_cidr(self.mask)), "dev", self.guest_dev]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        # setup default route
        self.ip_setup_route()

        self.ip_wait()
        return 0, "", ""

    def ip_get_mtu(self):
        # get mtu
        cmd = [rcEnv.syspaths.ip, "link", "show", self.ipdev]
        ret, out, err = self.call(cmd)
        if ret != 0:
            raise ex.excError("failed to get %s mtu: %s" % (self.ipdev, err))
        mtu = out.split()[4]
        return mtu

    def ip_setup_route(self):
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "route", "del", "default"]
        ret, out, err = self.call(cmd, errlog=False)

        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "link", "set", self.guest_dev, "up"]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            return ret, out, err

        if self.gateway:
            cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "route", "replace", "default", "via", self.gateway]
            ret, out, err = self.vcall(cmd)
            if ret != 0:
                return ret, out, err

        if self.del_net_route and self.network:
            cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "route", "del", self.network+"/"+to_cidr(self.mask), "dev", self.guest_dev]
            ret, out, err = self.vcall(cmd)
            if ret != 0:
                return ret, out, err

        # announce
        if which("arping") is not None:
            cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "arping" , "-c", "1", "-A", "-I", self.guest_dev, self.addr]
            self.log.info(" ".join(cmd))
            out, err, ret = justcall(cmd)

    def ip_wait(self):
        # ip activation may still be incomplete
        # wait for activation, to avoid startapp scripts to fail binding their listeners
        for _ in range(15, 0, -1):
            if check_ping(self.addr, timeout=1, count=1):
                return
        raise ex.excError("timed out waiting for ip activation")

    def get_nspid(self):
        container_id = self.container_id(refresh=True)
        if container_id is None:
            return
        cmd = self.svc.dockerlib.docker_cmd + ["inspect", "--format='{{ .State.Pid }}'", container_id]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("failed to get nspid: %s" % err)
        nspid = out.strip()
        if "'" in nspid:
            nspid = nspid.replace("'","")
        if nspid == "0":
            raise ex.excError("nspid is 0")
        return nspid

    @lazy
    def sandboxkey(self):
        container_id = self.container_id(refresh=True)
        if container_id is None:
            return
        cmd = self.svc.dockerlib.docker_cmd + ["inspect", "--format='{{ .NetworkSettings.SandboxKey }}'", container_id]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("failed to get sandboxkey: %s" % err)
        sandboxkey = out.strip()
        if "'" in sandboxkey:
            sandboxkey = sandboxkey.replace("'","")
        if sandboxkey == "":
            raise ex.excError("sandboxkey is empty")
        return sandboxkey

    def stopip_cmd(self):
        intf = self.get_docker_interface()
        if intf is None:
            raise ex.excContinueAction("can't find on which interface %s is plumbed in container %s" % (self.addr, self.container_id()))
        if self.mask is None:
            raise ex.excContinueAction("netmask is not set")
        cmd = [rcEnv.syspaths.nsenter, "--net="+self.sandboxkey, "ip", "addr", "del", self.addr+"/"+to_cidr(self.mask), "dev", intf]
        ret, out, err = self.vcall(cmd)
        unset_lazy(self, "sandboxkey")
        return ret, out, err

