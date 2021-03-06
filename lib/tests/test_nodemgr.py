# coding: utf8

from __future__ import print_function
from __future__ import unicode_literals

import json
import logging
import pytest

import nodemgr

UNICODE_STRING = "bêh"
logging.disable(logging.CRITICAL)


@pytest.fixture(scope='function')
def has_privs(mocker):
    mocker.patch('node.check_privs', return_value=None)


@pytest.fixture(scope='function')
def parse_args(mocker):
    parse_args = mocker.patch('nodemgr.NodemgrOptParser.parse_args',
                              return_value=(mocker.Mock(symcli_db_file=None),
                                            'my_action'))
    return parse_args


@pytest.fixture(scope='function')
def node(mocker):
    node = mocker.patch('nodemgr.node_mod.Node', autospec=True).return_value
    node.options = dict()
    node.action.return_value = 0
    return node


@pytest.mark.ci
@pytest.mark.usefixtures('has_privs', 'osvc_path_tests')
class TestNodemgr:
    @staticmethod
    @pytest.mark.parametrize('action_return_value', [0, 13])
    def test_it_call_once_node_action_and_returns_node_action_return_value(node, parse_args, action_return_value):
        node.action.return_value = action_return_value

        ret = nodemgr.main(argv=["my_action", "--format", "json"])

        assert ret == action_return_value
        node.action.assert_called_once_with('my_action')

    @staticmethod
    def test_get_extra_argv():
        assert nodemgr.get_extra_argv(["hello", 'world']) == (['hello', 'world'], [])

    @staticmethod
    def test_get_extra_argv_when_array():
        assert nodemgr.get_extra_argv(["array", '--', 'value=1']) == (['array', '--'], ['value=1'])
        assert nodemgr.get_extra_argv(["array", 'value=1']) == (['array'], ['value=1'])
        assert nodemgr.get_extra_argv(["myaction", 'value=1']) == (['myaction', 'value=1'], [])

    @staticmethod
    def test_print_schedule():
        """
        Print node schedules
        """
        ret = nodemgr.main(argv=["print", "schedule"])
        assert ret == 0

    @staticmethod
    def test_print_schedule_json(tmp_file, capture_stdout):
        """
        Print node schedules (json format)
        """
        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["print", "schedule", "--format", "json", "--color", "no"])

        assert ret == 0
        with open(tmp_file) as json_file:
            schedules = json.load(json_file)
        assert isinstance(schedules, list)
        assert len(schedules) > 0

    @staticmethod
    @pytest.mark.parametrize('argv',
                             (['pool', 'ls', '--debug'],
                              ['pool', 'status', '--debug'],
                              ['pool', 'status', '--verbose', '--debug']),
                             ids=['ls', 'status', 'status --verbose'])
    def test_pool_action(argv):
        assert nodemgr.main(argv=argv) == 0

    @staticmethod
    def test_node_has_a_pool(tmp_file, capture_stdout):
        with capture_stdout(tmp_file):
            assert nodemgr.main(argv=['pool', 'status', '--format', 'json']) == 0
        with open(tmp_file) as json_file:
            pools = json.load(json_file).values()
            assert len([pool for pool in pools if pool['type'] != 'unknown']) > 0

    def test_print_config(self):
        """
        Print node config
        """
        ret = nodemgr.main(argv=["print", "config"])
        assert ret == 0

    @staticmethod
    def test_print_config_json(tmp_file, capture_stdout):
        """
        Print node config (json format)
        """
        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["print", "config", "--format", "json", "--color", "no"])

        with open(tmp_file) as json_file:
            config = json.load(json_file)

        assert ret == 0
        assert isinstance(config, dict)

    @staticmethod
    @pytest.mark.parametrize('get_set_arg', ['--param', '--kw'])
    def test_set_get_unset_some_env_value(tmp_file, capture_stdout, get_set_arg):
        ret = nodemgr.main(argv=["set", "--param", "env.this_is_test", "--value", "true"])
        assert ret == 0

        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["get", get_set_arg, "env.this_is_test"])
            assert ret == 0
        from rcUtilities import try_decode
        with open(tmp_file) as output_file:
            assert try_decode(output_file.read()).strip() == "true"

        ret = nodemgr.main(argv=["unset", get_set_arg, "env.this_is_test"])
        assert ret == 0

        tmp_file_1 = tmp_file + '-1'
        with capture_stdout(tmp_file_1):
            ret = nodemgr.main(argv=["get", get_set_arg, "env.this_is_test"])
            assert ret == 0
        with open(tmp_file_1) as output_file:
            assert output_file.read().strip() == "None"

    @staticmethod
    def test_set_env_comment():
        """
        Set node env.comment to a unicode string
        """
        ret = nodemgr.main(argv=["set", "--param", "env.comment", "--value", UNICODE_STRING])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_get_env_comment(tmp_file, capture_stdout):
        """
        Get node env.comment
        """

        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["get", "--param", "env.comment"])

        assert ret == 0
        from rcUtilities import try_decode
        with open(tmp_file) as output_file:
            assert try_decode(output_file.read()) == UNICODE_STRING
            # assert try_decode(output_file.read()).strip() == UNICODE_STRING

    @staticmethod
    def test_043_unset():
        """
        Unset env.comment
        """
        ret = nodemgr.main(argv=["unset", "--param", "env.comment"])
        assert ret == 0

    @pytest.mark.skip
    def test_044_get_not_found(self):
        """
        Get an unset keyword
        """
        assert nodemgr.main(argv=["get", "--param", "env.comment"]) == 1

    @staticmethod
    def test_checks_return_0():
        """
        Run node checks
        """
        ret = nodemgr.main(argv=["checks"])
        assert ret == 0

    @staticmethod
    def test_sysreport():
        ret = nodemgr.main(argv=["sysreport"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_pushasset_return_0():
        ret = nodemgr.main(argv=["pushasset"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_08_nodemgr_collect_stats():
        """
        Run node collect stats
        """
        ret = nodemgr.main(argv=["collect_stats"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_09_nodemgr_pushstats():
        """
        Run node pushstats
        """
        ret = nodemgr.main(argv=["pushstats"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_10_nodemgr_pushpkg():
        """
        Run node pushpkg
        """
        ret = nodemgr.main(argv=["pushpkg"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_11_nodemgr_pushpatch():
        """
        Run node pushpatch
        """
        ret = nodemgr.main(argv=["pushpatch"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_12_nodemgr_pushdisks():
        """
        Run node pushdisks
        """
        ret = nodemgr.main(argv=["pushdisks"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_131_nodemgr_schedule_reboot():
        """
        Run schedule reboot
        """
        ret = nodemgr.main(argv=["schedule", "reboot"])
        assert ret == 0

    @staticmethod
    # @pytest.mark.skip
    def test_132_nodemgr_unschedule_reboot():
        """
        Run unschedule reboot
        """
        ret = nodemgr.main(argv=["unschedule", "reboot"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_133_nodemgr_print_reboot_status():
        """
        Print reboot schedule status
        """
        ret = nodemgr.main(argv=["schedule", "reboot", "status"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_14_nodemgr_logs():
        """
        Print node logs
        """
        ret = nodemgr.main(argv=["logs"])
        assert ret == 0

    @staticmethod
    def test_network_ls():
        """
        List node networks
        """
        ret = nodemgr.main(argv=["network", "ls"])
        assert ret == 0

    @staticmethod
    def test_network_ls_json(tmp_file, capture_stdout):
        """
        List node networks (json format)
        """

        nodemgr.main(argv=["network", "ls", "--format", "json", "--color", "no"])
        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["network", "ls", "--format", "json", "--color", "no"])

        assert ret == 0
        with open(tmp_file) as std_out:
            assert isinstance(json.load(std_out), dict)

    @staticmethod
    @pytest.mark.skip
    def test_161_nodemgr_print_devs():
        """
        Print node device tree
        """
        ret = nodemgr.main(argv=["print", "devs"])
        assert ret == 0

    @staticmethod
    def test_prkey_create_initial_value_when_absent(tmp_file, capture_stdout):
        with capture_stdout(tmp_file):
            ret = nodemgr.main(argv=["prkey"])
        assert ret == 0
        with open(tmp_file) as std_out:
            assert std_out.read().startswith('0x')

    @staticmethod
    def test_prkey_show_existing_prkey(tmp_file, capture_stdout):
        nodemgr.main(argv=['set', '--kw', 'node.prkey=0x8796759710111'])
        with capture_stdout(tmp_file):
            assert nodemgr.main(argv=["prkey"]) == 0
        with open(tmp_file) as output_file:
            assert output_file.read().strip() == '0x8796759710111'

    @staticmethod
    @pytest.mark.skip
    def test_163_nodemgr_dequeue_actions():
        """
        Dequeue actions
        """
        ret = nodemgr.main(argv=["dequeue", "actions"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_164_nodemgr_scan_scsi():
        """
        Scan scsi buses
        """
        ret = nodemgr.main(argv=["scanscsi"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_164_nodemgr_collector_networks():
        """
        Collector networks
        """
        ret = nodemgr.main(argv=["collector", "networks"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_164_nodemgr_collector_search():
        """
        Collector search
        """
        ret = nodemgr.main(argv=["collector", "search", "--like", "safe:%"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0251_compliance():
        """
        Node compliance auto
        """
        ret = nodemgr.main(argv=["compliance", "auto"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0252_compliance():
        """
        Node compliance check
        """
        ret = nodemgr.main(argv=["compliance", "check"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0253_compliance():
        """
        Node compliance fix
        """
        ret = nodemgr.main(argv=["compliance", "fix"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0254_compliance():
        """
        Node compliance show moduleset
        """
        ret = nodemgr.main(argv=["compliance", "show", "moduleset"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0255_compliance():
        """
        Node compliance list moduleset
        """
        ret = nodemgr.main(argv=["compliance", "list", "moduleset"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0256_compliance():
        """
        Node compliance show ruleset
        """
        ret = nodemgr.main(argv=["compliance", "show", "ruleset"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0257_compliance():
        """
        Node compliance list ruleset
        """
        ret = nodemgr.main(argv=["compliance", "list", "ruleset"])
        assert ret == 0

    @staticmethod
    @pytest.mark.skip
    def test_0258_compliance():
        """
        Node compliance attach
        """
        ret = nodemgr.main(argv=["compliance", "attach", "--ruleset", "abcdef", "--moduleset", "abcdef"])
        assert ret == 1

    @staticmethod
    @pytest.mark.skip
    def test_0259_compliance():
        """
        Node compliance detach
        """
        ret = nodemgr.main(argv=["compliance", "detach", "--ruleset", "abcdef", "--moduleset", "abcdef"])
        assert ret == 0
