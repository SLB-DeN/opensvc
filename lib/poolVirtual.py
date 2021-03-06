from __future__ import print_function

import os

import pool
import rcExceptions as ex
from rcUtilities import lazy, justcall
from rcGlobalEnv import rcEnv
from rcUtilities import is_service, split_path, factory

class Pool(pool.Pool):
    type = "virtual"
    capabilities = []

    def __init__(self, *args, **kwargs):
        pool.Pool.__init__(self, *args, **kwargs)
        self.capabilities = self.oget("capabilities")

    @lazy
    def template(self):
        return self.oget("template")

    @lazy
    def volume_env(self):
        return self.oget("volume_env")

    def configure_volume(self, volume, size=None, fmt=True, access="rwo", shared=False, nodes=None, env=None):
        if self.template is None:
            raise ex.excError("pool#%s.template is not set" % self.name)
        if not is_service(self.template):
            raise ex.excError("%s template volume not found" % self.template)
        name = self.default_disk_name(volume)
        tname, tnamespace, tkind = split_path(self.template)
        if tkind != "vol":
            raise ex.excError("%s template kind is not vol")
        svc = factory(tkind)(tname, tnamespace, volatile=True, node=self.node)
        config = svc.print_config_data()
        try:
            del config["DEFAULT"]["disable"]
        except KeyError:
            pass
        if "DEFAULT" not in config:
            config["DEFAULT"] = {}
        if "env" not in config:
            config["env"] = {}
        config["DEFAULT"]["pool"] = self.name
        config["DEFAULT"]["access"] = access
        if access in ("rox", "rwx"):
            config["DEFAULT"]["topology"] = "flex"
            config["DEFAULT"]["flex_min"] = 0
        if nodes:
            config["DEFAULT"]["nodes"] = nodes
        config["env"]["size"] = size
        if env:
            config["env"].update(env)
        self.node.install_svc_conf_from_data(volume.name, volume.namespace, volume.kind, config)

    def pool_status(self):
        data = {
            "type": self.type,
            "name": self.name,
            "capabilities": self.capabilities,
            "head": self.template,
        }
        return data

