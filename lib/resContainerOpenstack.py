import rcStatus
import resources as Res
import time
import os
import rcExceptions as ex
from rcGlobalEnv import rcEnv
from rcUtilities import justcall, lazy
from rcUtilitiesLinux import check_ping
import resContainer

class CloudVm(resContainer.Container):
    save_timeout = 240

    def __init__(self,
                 rid,
                 name,
                 guestos=None,
                 cloud_id=None,
                 size="tiny",
                 key_name=None,
                 shared_ip_group=None,
                 osvc_root_path=None,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        type="container.openstack",
                                        guestos=guestos,
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)
        self.cloud_id = cloud_id
        self.save_name = name + '.save'
        self.size_name = size
        self.key_name = key_name
        self.shared_ip_group = shared_ip_group
        self.addr = None

    def keyfile(self):
        kf = [os.path.join(rcEnv.paths.pathetc, self.key_name+'.pem'),
              os.path.join(rcEnv.paths.pathetc, self.key_name+'.pub'),
              os.path.join(rcEnv.paths.pathvar, self.key_name+'.pem'),
              os.path.join(rcEnv.paths.pathvar, self.key_name+'.pub')]
        for k in kf:
            if os.path.exists(k):
                return k
        raise ex.excError("key file for key name '%s' not found"%self.key_name)

    def rcp_from(self, src, dst):
        if self.guestos == "windows":
            """ Windows has no sshd.
            """
            raise ex.excNotSupported("remote copy not supported on Windows")

        self.getaddr()
        if self.addr is None:
            raise ex.excError('no usable public ip to send files to')

        timeout = 5
        cmd = [ 'scp', '-o', 'StrictHostKeyChecking=no',
                       '-o', 'ConnectTimeout='+str(timeout),
                       '-i', self.keyfile(),
                        self.addr+':'+src, dst]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def rcp(self, src, dst):
        if self.guestos == "windows":
            """ Windows has no sshd.
            """
            raise ex.excNotSupported("remote copy not supported on Windows")

        self.getaddr()
        if self.addr is None:
            raise ex.excError('no usable public ip to send files to')

        timeout = 5
        cmd = [ 'scp', '-o', 'StrictHostKeyChecking=no',
                       '-o', 'ConnectTimeout='+str(timeout),
                       '-i', self.keyfile(),
                        src, self.addr+':'+dst]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def rcmd(self, cmd):
        if self.guestos == "windows":
            """ Windows has no sshd.
            """
            raise ex.excNotSupported("remote commands not supported on Windows")

        self.getaddr()
        if self.addr is None:
            raise ex.excError('no usable public ip to send command to')

        if type(cmd) == str:
            cmd = cmd.split(" ")

        timeout = 5
        cmd = [ 'ssh', '-o', 'StrictHostKeyChecking=no',
                       '-o', 'ForwardX11=no',
                       '-o', 'BatchMode=yes',
                       '-n',
                       '-o', 'ConnectTimeout='+str(timeout),
                       '-i', self.keyfile(),
                        self.addr] + cmd
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("'%s' execution error:\n%s"%(' '.join(cmd), err))
        return out, err, ret

    def get_size(self):
        for size in self.cloud.driver.list_sizes():
            if size.name == self.size_name:
                return size
        raise ex.excError("%s size not found"%self.size_name)

    @lazy
    def cloud(self):
        return self.svc.node.cloud_get(self.cloud_id)

    def get_node(self):
        l = self.cloud.list_nodes()
        for n in l:
            if n.name == self.name:
                return n
        return

    def get_save_name(self):
        import datetime
        now = datetime.datetime.now()
        save_name = self.save_name + now.strftime(".%Y-%m-%d.%H:%M:%S")
        return save_name

    def purge_saves(self):
        l = self.cloud.driver.list_images()
        d = {}
        for image in l:
            if image.name.startswith(self.save_name):
                d[image.name] = image
        if len(d) == 0:
             raise ex.excError("no save image found")
        elif len(d) == 1:
             self.log.info("no previous save image to delete")
        for k in sorted(d.keys())[:-1]:
             self.log.info("delete previous save image %s"%d[k].name)
             self.cloud.driver.ex_delete_image(d[k])

    def get_last_save(self):
        return self.get_image(self.save_name)

    def get_template(self):
        template = self.svc.oget(self.rid, "template")
        return self.get_image(template)

    def get_image(self, name):
        l = self.cloud.driver.list_images()
        d = {}
        for image in l:
            if image.name == name:
                # exact match
                return image
            elif image.name.startswith(name):
                d[image.name] = image
        if len(d) == 0:
             raise ex.excError("image %s not found"%name)
        for k in sorted(d.keys()):
             last = d[k]
        return last

    def has_image(self, name):
        l = self.cloud.driver.list_images()
        for image in l:
            if image.name == name:
                return True
        return False

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def getaddr(self):
        if self.addr is not None:
            return
        n = self.get_node()
        if n is None:
            raise ex.excError("could not get node details")
        ips = set(n.public_ips+n.private_ips)
        if len(ips) == 0:
            return 0

        # find first pinging ip
        for ip in ips:
            if check_ping(ip, timeout=1, count=1):
                self.addr = ip
                break

        return 0

    def check_capabilities(self):
        return True

    def ping(self):
        if self.addr is None:
            return 0
        return check_ping(self.addr, timeout=1, count=1)

    def start(self):
        if self.is_up():
            self.log.info("container %s already started" % self.name)
            return
        if rcEnv.nodename in self.svc.drpnodes:
            self.install_drp_flag()
        self.container_start()
        self.can_rollback = True
        self.wait_for_startup()

    def container_start(self):
        n = self.get_node()
        if n is not None:
            if n.state == 4:
                self.log.info("reboot %s"%self.name)
                self.container_reboot()
            else:
                raise ex.excError("abort reboot because vm is in state %d (!=4)"%n.state)
        else:
            self.container_restore()

    def container_reboot(self):
        n = self.get_node()
        try:
            self.cloud.driver.reboot_node(n)
        except Exception as e:
            raise ex.excError(str(e))

    def container_restore(self):
        image = self.get_last_save()
        size = self.get_size()
        self.log.info("create instance %s, size %s, image %s, key %s"%(self.name, size.name, image.name, self.key_name))
        n = self.cloud.driver.create_node(name=self.name, size=size, image=image, ex_keyname=self.key_name, ex_shared_ip_group_id=self.shared_ip_group)
        self.log.info("wait for container up status")
        self.wait_for_fn(self.is_up, self.start_timeout, 5)
        #n = self.cloud.driver.ex_update_node(n, accessIPv4='46.231.128.84')

    def wait_for_startup(self):
        pass

    def stop(self):
        if self.is_down():
            self.log.info("container %s already stopped" % self.name)
            return
        self.container_stop()
        try:
            self.wait_for_shutdown()
        except ex.excError:
            self.container_forcestop()
            self.wait_for_shutdown()

    def container_stop(self):
        cmd = "shutdown -h now"
        self.log.info("remote command: %s"%cmd)
        self.rcmd(cmd)

    def container_forcestop(self):
        n = self.get_node()
        self.container_save()
        self.cloud.driver.destroy_node(n)
        self.purge_saves()

    def print_obj(self, n):
        for k in dir(n):
            if '__' in k:
                continue
            print(k, "=", getattr(n, k))

    def container_save(self):
        n = self.get_node()
        save_name = self.get_save_name()
        if self.has_image(save_name):
            return
        #self.print_obj(n)
        if n.state == 9999:
            self.log.info("a save is already in progress")
            return
        self.log.info("save new image %s"%save_name)
        try:
            image = self.cloud.driver.ex_save_image(n, save_name)
        except Exception as e:
            raise ex.excError(str(e))
        import time
        delay = 5
        for i in range(self.save_timeout//delay):
            img = self.cloud.driver.ex_get_image(image.id)
            if img.extra['status'] != 'SAVING':
                break
            time.sleep(delay)
        if img.extra['status'] != 'ACTIVE':
            raise ex.excError("save failed, image status %s"%img.extra['status'])

    def is_up(self):
        n = self.get_node()
        if n is not None and n.state == 0:
            return True
        return False

    def get_container_info(self):
        self.info = {'vcpus': '0', 'vmem': '0'}
        n = self.get_node()
        try:
            size = self.cloud.driver.ex_get_size(n.extra['flavorId'])
            self.info['vmem'] = str(size.ram)
        except:
            pass
        return self.info

    def check_manual_boot(self):
        return True

    def install_drp_flag(self):
        pass


