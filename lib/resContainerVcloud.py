import rcStatus
import resources as Res
import time
import os
import rcExceptions as ex
from rcGlobalEnv import rcEnv
from rcUtilities import qcall, lazy
from rcUtilitiesLinux import check_ping
import resContainer

try:
    from libcloud.utils.py3 import urlparse
    urlparse = urlparse.urlparse
    def get_url_path(url):
        return urlparse(url.strip()).path
except ImportError:
    urlparse = None
    def get_url_path(url):
        return

class CloudVm(resContainer.Container):
    save_timeout = 240

    def __init__(self,
                 rid,
                 name,
                 vapp=None,
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
                                        type="container.vcloud",
                                        guestos=guestos,
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)
        self.cloud_id = cloud_id
        self.save_name = name + '.save'
        self.size_name = size
        self.key_name = key_name
        self.vapp = vapp
        self.shared_ip_group = shared_ip_group

    def _vm_perform_power_operation(self, vapp_or_vm_id, operation):
        drv = self.cloud.driver
        vms = drv._get_vm_elements(vapp_or_vm_id)
        for vm in vms:
            path = get_url_path(vm.get('href'))
            if path is None:
                raise ex.excError("libcloud is not installed")
            res = drv.connection.request(
                '%s/power/action/%s' % (path, operation),
                method='POST')
            drv._wait_for_task_completion(path)
            res = drv.connection.request(path)

    def get_size(self):
        for size in self.cloud.driver.list_sizes():
            if size.name == self.size_name:
                return size
        raise ex.excError("%s size not found"%self.size_name)

    @lazy
    def cloud(self):
        return self.svc.node.cloud_get(self.cloud_id)

    def get_vapp(self):
        try:
            vapp = self.cloud.driver.ex_find_node(self.vapp)
        except Exception as e:
            print(e)
            raise
        return vapp

    def get_node(self):
        try:
            vapp = self.cloud.driver.ex_find_node(self.vapp)
            vms = vapp.extra['vms']
        except Exception as e:
            print(e)
            raise
        for vm in vms:
            if vm['name'] == self.name:
                return vm
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
        template = self.svc.oget(self.rid, 'template')
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
        if hasattr(self, 'addr'):
            return
        n = self.get_node()

        ips = set(n['public_ips']+n['private_ips'])
        if len(ips) == 0:
            return 0

        # find first pinging ip
        for ip in ips:
            if check_ping(ip, timeout=1, count=1):
                self.addr = ip
                break

    def check_capabilities(self):
        return True

    def ping(self):
        if not hasattr(self, "addr"):
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
        self.log.info("power on container %s"%self.name)
        self._vm_perform_power_operation(n['id'], 'powerOn')
        self.log.info("wait for container up status")
        self.wait_for_fn(self.is_up, self.start_timeout, 5)

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
        n = self.get_node()
        self.log.info("shutdown container %s"%self.name)
        self._vm_perform_power_operation(n['id'], 'shutdown')

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
        if n['state'] == 9999:
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

    def container_forcestop(self):
        n = self.get_node()
        self.log.info("power off container %s"%self.name)
        self._vm_perform_power_operation(n['id'], 'powerOff')

    def is_up(self):
        n = self.get_node()
        if n is not None and n['state'] == 0:
            return True
        return False

    def get_container_info(self):
        self.info = {'vcpus': '0', 'vmem': '0'}
        n = self.get_node()
        top = self.cloud.driver._get_vm_elements(n['id'])[0]
        def recurse(x, info={}, desc=None):
            if x.tag == '{http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData}Description':
                desc = x.text
                info[desc] = {}
                return info, desc
            if desc is not None:
                info[desc][x.tag[x.tag.index('}')+1:]] = x.text
            for c in x._children:
                info, desc = recurse(c, info, desc)
            return info, desc
        info, desc = recurse(top)
        self.info['vcpus'] = info['Number of Virtual CPUs']['VirtualQuantity']
        self.info['vmem'] = info['Memory Size']['VirtualQuantity']
        #print(self.info)
        return self.info

    def check_manual_boot(self):
        return True

    def install_drp_flag(self):
        pass


