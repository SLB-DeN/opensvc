import handler
import osvcd_shared as shared
import rcExceptions as ex
from rcGlobalEnv import rcEnv

class Handler(handler.Handler):
    """
    Reset the generation number of the dataset of a peer node to force him
    to resend a full.
    """
    routes = (
        ("POST", "ask_full"),
        (None, "ask_full"),
    )
    prototype = [
        {
            "name": "peer",
            "format": "string",
            "desc": "The peer node to ask a full data sync to.",
            "required": True,
        },
    ]

    def action(self, nodename, thr=None, **kwargs):
        options = self.parse_options(kwargs)
        if options.peer is None:
            raise ex.excError("The 'peer' option must be set")
        if options.peer == rcEnv.nodename:
            raise ex.excError("Can't ask a full from ourself")
        if options.peer not in thr.cluster_nodes:
            raise ex.excError("Can't ask a full from %s: not in cluster.nodes" % options.peer)
        shared.REMOTE_GEN[options.peer] = 0
        result = {
            "info": "remote %s asked for a full" % options.peer,
            "status": 0,
        }
        return result


