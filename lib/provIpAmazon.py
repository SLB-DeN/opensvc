import provisioning
import rcExceptions as ex

class Prov(provisioning.Prov):
    def __init__(self, r):
        provisioning.Prov.__init__(self, r)

    def provisioner(self):
        self.provisioner_private()
        self.provisioner_public()
        self.provisioner_docker_ip()
        self.cascade_allocation()
        self.r.log.info("provisioned")
        self.r.start()
        return True

    def cascade_allocation(self):
        cascade = self.r.svc.oget(self.r.rid, "cascade_allocation")
        if not cascade:
            return
        changes = []
        for e in cascade:
            try:
                rid, param = e.split(".")
            except:
                self.r.log.warning("misformatted cascade entry: %s (expected <rid>.<param>[@<scope>])" % e)
                continue
            if not rid in self.r.svc.cd:
                self.r.log.warning("misformatted cascade entry: %s (rid does not exist)" % e)
                continue
            self.r.log.info("cascade %s to %s" % (self.r.ipname, e))
            changes.append("%s.%s=%s" % (rid, param, self.r.ipname))
            self.r.svc.resources_by_id[rid].ipname = self.r.svc.conf_get(rid, param)
            self.r.svc.resources_by_id[rid].addr = self.r.svc.resources_by_id[rid].ipname
        self.r.svc.set_multi(changes)

    def provisioner_docker_ip(self):
        if not self.r.svc.oget(self.r.rid, "docker_daemon_ip"):
            return
        args = self.r.svc.oget('DEFAULT', 'docker_daemon_args')
        args += ["--ip", self.r.ipname]
        self.r.svc.set_multi(["DEFAULT.docker_daemon_args=%s" % " ".join(args)])
        for r in self.r.svc.get_resources("container.docker"):
            # reload docker daemon args
            r.on_add()

    def provisioner_private(self):
        if self.r.ipname != "<allocate>":
            self.r.log.info("private ip already provisioned")
            return

        eni = self.r.get_network_interface()
        if eni is None:
            raise ex.excError("could not find ec2 network interface for %s" % self.r.ipdev)

        ips1 = set(self.r.get_instance_private_addresses())
        data = self.r.aws([
          "ec2", "assign-private-ip-addresses",
          "--network-interface-id", eni,
          "--secondary-private-ip-address-count", "1"
        ])
        ips2 = set(self.r.get_instance_private_addresses())
        new_ip = list(ips2 - ips1)[0]

        self.r.svc.set_multi(["%s.ipname=%s" % (self.r.rid, new_ip)])
        self.r.ipname = new_ip

    def provisioner_public(self):
        if self.r.eip != "<allocate>":
            self.r.log.info("public ip already provisioned")
            return

        data = self.r.aws([
          "ec2", "allocate-address",
          "--domain", "vpc",
        ])

        self.r.svc.set_multi(["%s.eip=%s" % (self.r.rid, data["PublicIp"])])
        self.r.eip = data["PublicIp"]

