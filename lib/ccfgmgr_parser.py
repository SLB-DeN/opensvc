"""
ccfgmgr command line actions and options
"""
import svc
import mgr_parser as mp
from rcOptParser import OptParser
from optparse import Option

PROG = "ccfgmgr"

OPT = mp.OPT
ACTIONS = mp.ACTIONS

DEPRECATED_OPTIONS = [
]

DEPRECATED_ACTIONS = [
]

ACTIONS_TRANSLATIONS = {
}

class CcfgmgrOptParser(OptParser):
    """
    The secmgr-specific options parser class
    """
    def __init__(self, args=None, colorize=True, width=None, formatter=None,
                 indent=6):
        OptParser.__init__(self, args=args, prog=PROG, options=OPT,
                           actions=ACTIONS,
                           deprecated_options=DEPRECATED_OPTIONS,
                           deprecated_actions=DEPRECATED_ACTIONS,
                           actions_translations=ACTIONS_TRANSLATIONS,
                           global_options=mp.GLOBAL_OPTS,
                           svc_select_options=mp.SVC_SELECT_OPTS,
                           colorize=colorize, width=width,
                           formatter=formatter, indent=indent, async_actions=svc.ACTION_ASYNC)

