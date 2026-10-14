# -*- coding: utf-8 -*-
"""MOCK 官方工具：进程内模拟 DUT，仅用于 Skill 工具链验收。真实题目使用平台提供的 testtool.py。"""


class _Wallet(object):
    def __init__(self):
        self.acc = {}
        self.dep = {}

    def handle(self, line):
        p = line.split()
        if not p:
            return "E002:Bad"
        c = p[0]
        if c == "INIT":
            self.acc, self.dep = {}, {}
            return "E000:Success"
        if c == "OPEN":
            if p[1] in self.acc:
                return "E005:Exists"
            self.acc[p[1]] = 0
            self.dep[p[1]] = 0
            return "E000:Success"
        if c in ("DEP", "WD", "QRY") and p[1] not in self.acc:
            return "E001:NoAccount"
        if c == "QRY":
            return "E000:%d" % self.acc[p[1]]
        amt = int(p[2])
        if amt < 1 or amt > 1000:
            return "E002:BadAmount"
        if c == "DEP":
            if self.dep[p[1]] + amt > 2000:
                return "E003:Limit"
            self.dep[p[1]] += amt
            self.acc[p[1]] += amt
            return "E000:Success"
        if amt > self.acc[p[1]]:
            return "E004:Insufficient"
        self.acc[p[1]] -= amt
        return "E000:Success"


class _Tool(object):
    def __init__(self):
        self._dut = None
        self.failed = []

    def start_dut(self):
        self._dut = _Wallet()

    def stop_dut(self):
        self._dut = None

    def case_start(self, case_id):
        pass

    def case_finished(self, case_id):
        pass

    def case_failed(self, case_id):
        self.failed.append(case_id)

    def send(self, cmd):
        return self._dut.handle(cmd)


tool = _Tool()
