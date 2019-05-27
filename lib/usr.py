import os
import sys
import base64
import re
import fnmatch
import shutil
import glob
import tempfile

from rcGlobalEnv import rcEnv
from rcUtilities import lazy, makedirs, split_svcpath, fmt_svcpath, factory, bdecode
from svc import BaseSvc
from rcSsl import gen_cert
from sec import Sec
import rcExceptions as ex

DEFAULT_STATUS_GROUPS = [
]

OPENSSL_CRL_CONF = """
[ ca ]
default_ca = %(clustername)s

[ %(clustername)s ]
database = %(p_crlindex)s
crlnumber = %(p_crlnumber)s
default_days = 365
default_crl_days = 30
default_md = default
preserve = no

[ crl_ext ]
authorityKeyIdentifier = keyid:always,issuer:always
"""

class Usr(Sec, BaseSvc):
    kind = "usr"
    desc = "user"

    @lazy
    def kwdict(self):
        return __import__("usrdict")

    def on_create(self):
        if not self.oget("DEFAULT", "cn"):
            self.set_multi(["cn=%s" % self.svcname])
        if not self.oget("DEFAULT", "ca"):
            ca = self.node.oget("cluster", "ca")
            if ca is None:
                ca = "system/sec/ca-" + self.node.cluster_name
            self.set_multi(["ca=%s" % ca])
        if "certificate" not in self.data_keys():
            self.gen_cert()

    @lazy
    def ca(self):
        capath = self.oget("DEFAULT", "ca")
        name, namespace, kind = split_svcpath(capath)
        return factory("sec")(name, namespace=namespace, volatile=True, node=self.node)

    def revoke(self):
        if "certificate" not in self.data_keys():
            raise ex.excError("can not revoke: this certificate is signed by an external CA, and should be revoked there.")
        p_ca_key = os.path.join(rcEnv.paths.certs, "ca_private_key")
        p_ca_crt = os.path.join(rcEnv.paths.certs, "ca_certiticate")
        p_crl = os.path.join(rcEnv.paths.certs, "ca_crl")
        p_crlconf = os.path.join(rcEnv.paths.certs, "openssl-crl.conf")
        p_crlnumber = os.path.join(rcEnv.paths.certs, "crlnumber")
        p_crlindex = os.path.join(rcEnv.paths.certs, "crlindex")
        p_usr_crt = os.path.join(rcEnv.paths.certs, "%s_certificate" % self.svcname)
        if "crlnumber" not in self.data_keys():
            self.ca.add_key("crlnumber", "00")
        if "crlconf" not in self.data_keys():
            self.ca.add_key("crlconf", OPENSSL_CRL_CONF % dict(p_crlindex=p_crlindex, p_crlnumber=p_crlnumber, clustername=self.node.cluster_name))
        if "crlindex" in self.data_keys():
            self.ca.install_key("crlindex", p_crlindex)
        else:
            with open(p_crlindex, "w") as f:
                pass
        self.ca.install_key("crlnumber", p_crlnumber)
        self.ca.install_key("crlconf", p_crlconf)
        self.ca.install_key("private_key", p_ca_key)
        self.ca.install_key("certificate", p_ca_crt)
        self.install_key("certificate", p_usr_crt)
        cmd = ["openssl", "ca",
               "-keyfile", p_ca_key,
               "-cert", p_ca_crt,
               "-revoke", p_usr_crt,
               "-config", p_crlconf]
        self.vcall(cmd)
        cmd = ["openssl", "ca",
               "-keyfile", p_ca_key,
               "-cert", p_ca_crt,
               "-gencrl",
               "-out", p_crl,
               "-config", p_crlconf]
        self.vcall(cmd)
        with open(p_crlindex) as f:
            buff = f.read()
        self.ca.add_key("crlindex", buff)
        with open(p_crl) as f:
            buff = f.read()
        self.ca.add_key("crl", buff)

