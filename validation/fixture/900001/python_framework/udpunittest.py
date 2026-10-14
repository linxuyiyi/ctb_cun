# -*- coding: utf-8 -*-
import os
import unittest

import testaw
from testtool import tool
from timeout import timeout

global_default_timeout = 300


class MyTestCase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tool.start_dut()

    @classmethod
    def tearDownClass(cls):
        tool.stop_dut()

    def setUp(self):
        tool.case_start(self.id())
        testaw.trace_case(self.id())

    def tearDown(self):
        try:
            tool.case_finished(self.id())
        finally:
            testaw.init()

    def assertTrue(self, expr, msg=None):
        if not expr:
            tool.case_failed(self.id())
        super(MyTestCase1, self).assertTrue(expr, msg)

    def assertFalse(self, expr, msg=None):
        if expr:
            tool.case_failed(self.id())
        super(MyTestCase1, self).assertFalse(expr, msg)

    @timeout(global_default_timeout)
    def test_01_deposit_effect_observed(self):
        self.assertTrue(testaw.open_account("12345") == "E000:Success")
        self.assertTrue(testaw.deposit("12345", 300) == "E000:Success")
        self.assertTrue(testaw.query("12345") == "E000:300")

    @timeout(global_default_timeout)
    def test_02_cumulative_limit_boundary(self):
        self.assertTrue(testaw.open_account("12345") == "E000:Success")
        self.assertTrue(testaw.deposit("12345", 1000) == "E000:Success")
        self.assertTrue(testaw.deposit("12345", 1000) == "E000:Success")
        self.assertTrue(testaw.deposit("12345", 1) == "E003:Limit")
        self.assertTrue(testaw.query("12345") == "E000:2000")

    @timeout(global_default_timeout)
    def test_03_withdraw_reject_keeps_state(self):
        testaw.open_account("12345")
        self.assertTrue(testaw.withdraw("12345", 5).startswith("E004"))


if __name__ == "__main__":
    logfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "udpunittest.log")
    with open(logfile, "w") as stream:
        unittest.main(testRunner=unittest.TextTestRunner(stream, verbosity=2))
