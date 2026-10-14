# -*- coding: utf-8 -*-
import json
import os

from testtool import tool

_TRACE_FILE = os.environ.get("CTB_TRACE_FILE")
_CASE = [None]


def trace_case(case_id):
    _CASE[0] = case_id


def _send(cmd):
    resp = tool.send(cmd)
    if _TRACE_FILE:
        with open(_TRACE_FILE, "a") as f:
            f.write(json.dumps({"case": _CASE[0], "cmd": cmd, "resp": resp}) + "\n")
    return resp


def init():
    return _send("INIT")


def open_account(acc_id):
    return _send("OPEN %s" % acc_id)


def deposit(acc_id, amount):
    return _send("DEP %s %s" % (acc_id, amount))


def withdraw(acc_id, amount):
    return _send("WD %s %s" % (acc_id, amount))


def query(acc_id):
    return _send("QRY %s" % acc_id)
