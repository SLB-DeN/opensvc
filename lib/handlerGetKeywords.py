import handler
import osvcd_shared as shared
from rcExceptions import HTTP
from rcUtilities import factory

class Handler(handler.Handler):
    """
    Return the supported <kind> object keywords.
    """
    routes = (
        ("GET", "keywords"),
        (None, "get_keywords"),
    )
    prototype = [
        {
            "name": "kind",
            "format": "string",
            "candidates": ["node", "svc", "vol", "usr", "sec", "cfg", "ccfg"],
            "desc": "The object kind or 'node'.",
        },
    ]
    access = {}

    def action(self, nodename, thr=None, **kwargs):
        options = self.parse_options(kwargs)
        if options.kind == "node":
            obj = shared.NODE
        elif options.kind:
            obj = factory(options.kind)(name="dummy", node=shared.NODE, volatile=True)
        else:
            raise HTTP(400, "A kind must be specified.")
        return obj.kwdict.KEYS.dump()

