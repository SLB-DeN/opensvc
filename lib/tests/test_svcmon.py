# coding: utf8

from __future__ import print_function

import sys
import os
mod_d = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, mod_d)

import svcmon


class TestSvcmon:
    def test_011_svcmon(self):
        """
        svcmon
        """
        ret = svcmon.main(argv=[])
        assert ret == 0

    def test_012_svcmon_filter(self):
        """
        svcmon -s 'abcdef'
        """
        ret = svcmon.main(argv=["-s", "abdcef"])
        assert ret == 1

