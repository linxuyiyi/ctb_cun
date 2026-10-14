# -*- coding: utf-8 -*-
"""MOCK timeout 装饰器，仅用于验收。"""
import functools


def timeout(seconds):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            return fn(*a, **k)
        return wrapper
    return deco
