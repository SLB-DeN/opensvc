import os
import json

import resources as Res
from rcUtilities import which, justcall, lazy
from rcGlobalEnv import rcEnv
from storage import Storage
import resContainer
import rcExceptions as ex

lxc = "/usr/bin/lxc"
lxd = "/usr/bin/lxd"

class Container(resContainer.Container):
    refresh_provisioned_on_unprovision = True

    def __init__(self,
                 rid,
                 name,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        type="container.lxd",
                                        **kwargs)

        self.runmethod = ['lxc', 'exec', name, '--']
        self.getaddr = self.dummy
        self.label = "lxd " + name if name else "<undefined>"

    def files_to_sync(self):
        return ["/var/lib/lxd/containers/"+self.name]

    def rcp_from(self, src, dst):
        cmd = [lxc, "file", "pull", self.name+src, dst]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def rcp(self, src, dst):
        cmd = [lxc, "file", "push", src, self.name+dst]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def lxc_info(self, nodename=None):
        if nodename:
            name = nodename + ":" + self.name
        else:
            name = self.name
        cmd = [lxc, "list", name, "--format", "json"]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return
        try:
            return json.loads(out)[0]
        except (ValueError, IndexError):
            return

    def postsync(self):
        self.lxd_import()

    def lxd_import(self):
        if self.lxc_info() is not None:
            return
        cmd = [lxd, "import", self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def lxc_start(self):
        self.lxd_import()
        cmd = [lxc, "start", self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def lxc_stop(self):
        cmd = [lxc, "stop", self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def set_cpuset_clone_children(self):
        ppath = "/sys/fs/cgroup/cpuset"
        if not os.path.exists(ppath):
            self.log.debug("set_clone_children: %s does not exist" % ppath)
            return
        path = "/sys/fs/cgroup/cpuset/lxc"
        val = "1"
        if not os.path.exists(path):
            self.log.info("mkdir %s" % path)
            os.makedirs(path)
        for parm in ("cpuset.mems", "cpuset.cpus"):
            current_val = self.get_sysfs(path, parm)
            if current_val is None:
                continue
            if current_val == "":
                parent_val = self.get_sysfs(ppath, parm)
                self.set_sysfs(path, parm, parent_val)
        parm = "cgroup.clone_children"
        current_val = self.get_sysfs(path, parm)
        if current_val is None:
            return
        if current_val == "1":
            self.log.debug("set_cpuset_clone_children: %s/%s already set to 1" % (path, parm))
            return
        self.set_sysfs(path, parm, "1")

    def get_sysfs(self, path, parm):
        fpath = os.sep.join([path, parm])
        if not os.path.exists(fpath):
            self.log.debug("get_sysfs: %s does not exist" % path)
            return
        with open(fpath, "r") as f:
            current_val = f.read().rstrip("\n")
        self.log.debug("get_sysfs: %s contains %s" % (fpath, repr(current_val)))
        return current_val

    def set_sysfs(self, path, parm, val):
        fpath = os.sep.join([path, parm])
        self.log.info("echo %s >%s" % (val, fpath))
        with open(fpath, "w") as f:
            f.write(val)

    def cleanup_cgroup(self, t="*"):
        import glob
        for p in glob.glob("/sys/fs/cgroup/%s/lxc/%s-[0-9]" % (t, self.name)) + \
                 glob.glob("/sys/fs/cgroup/%s/lxc/%s" % (t, self.name)):
            try:
                os.rmdir(p)
                self.log.info("removed leftover cgroup %s" % p)
            except Exception as e:
                self.log.debug("failed to remove leftover cgroup %s: %s" % (p, str(e)))

    def _migrate(self):
        cmd = [lxc, 'move', self.name, self.svc.options.to+":"+self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_start(self):
        if not self.svc.create_pg:
            self.cleanup_cgroup()
        self.set_cpuset_clone_children()
        self.lxc_start()

    def container_stop(self):
        self.lxc_stop()

    def post_container_stop(self):
        self.cleanup_links()
        self.cleanup_cgroup()

    def container_forcestop(self):
        """ no harder way to stop a lxc container, raise to signal our
            helplessness
        """
        raise ex.excError

    def get_links(self):
        data = self.lxc_info()
        if data is None:
            return  []
        return list(data.get("Ips", {}).keys())

    def cleanup_link(self, link):
        cmd = ["ip", "link", "del", "dev", link]
        out, err, ret = justcall(cmd)
        if ret == 0:
            self.log.info(" ".join(cmd))
        else:
            self.log.debug(" ".join(cmd)+out+err)

    def cleanup_links(self):
        for link in self.get_links():
            self.cleanup_link(link)

    def has_it(self):
        data = self.lxc_info()
        if data is None:
            return False
        return True

    def is_up_on(self, nodename):
        return self.is_up(nodename=nodename)

    def is_up(self, nodename=None):
        data = self.lxc_info(nodename=nodename)
        #print(json.dumps(data, indent=4))
        if data is None:
            return False
        return data["status"] == "Running"

    def get_container_info(self):
        return {'vcpus': '0', 'vmem': '0'}

    def check_manual_boot(self):
        return True

    def check_capabilities(self):
        if not which(lxc):
            self.log.debug("lxc is not in installed")
            return False
        return True

    def _status(self, verbose=False):
        return resContainer.Container._status(self, verbose=verbose)

    def dummy(self, cache_fallback=False):
        pass

    def operational(self):
        if not resContainer.Container.operational(self):
            return False

        cmd = self.runmethod + ['test', '-f', '/bin/systemctl']
        out, err, ret = justcall(cmd, stdin=self.svc.node.devnull)
        if ret == 1:
            # not a systemd container. no more checking.
            self.log.debug("/bin/systemctl not found in container")
            return True

        # systemd on-demand loading will let us start the encap service before
        # the network is fully initialized, causing start issues with nfs mounts
        # and listening apps.
        # => wait for systemd default target to become active
        cmd = self.runmethod + ['systemctl', 'is-active', 'default.target']
        out, err, ret = justcall(cmd, stdin=self.svc.node.devnull)
        if ret == 1:
            # if systemctl is-active fails, retry later
            self.log.debug("systemctl is-active failed")
            return False
        if out.strip() == "active":
            self.log.debug("systemctl is-active succeeded")
            return True

        # ok, wait some more
        self.log.debug("waiting for lxc to come up")
        return False

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def abort_start(self):
        for nodename in self.svc.peers:
            if nodename == rcEnv.nodename:
                continue
            if self.is_up_on(nodename):
                self.log.error("already up on %s", nodename)
                return True
        return False

    def cni_containerid(self):
        """
        Used by ip.cni
        """
        return self.name

    def cni_netns(self):
        """
        Used by ip.cni and ip.netns
        """
        try:
            return "/proc/%d/ns/net" % self.get_pid()
        except (TypeError, ValueError):
            return

    def get_pid(self):
        try:
            return self.lxc_info()["state"]["pid"]
        except (KeyError, TypeError) as exc:
            return

    def start(self):
        resContainer.Container.start(self)
        self.svc.sub_set_action("ip", "start", tags=set([self.rid]))

    def stop(self):
        self.svc.sub_set_action("ip", "stop", tags=set([self.rid]))
        resContainer.Container.stop(self)

    def provision(self):
        resContainer.Container.provision(self)
        self.svc.sub_set_action("ip", "provision", tags=set([self.rid]))

    def unprovision(self):
        self.svc.sub_set_action("ip", "unprovision", tags=set([self.rid]))
        resContainer.Container.unprovision(self)

