#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTB E2E 证据工具（仅标准库，兼容 Python 3.8）。

子命令：
  collect       在 python_framework 下用 unittest loader 真实收集 testcase ID
  run           按官方入口真实运行，保存日志、解析结果、核对 ID 与 SHA-256
  lint          静态检查 udpunittest.py / testaw.py 的判别力与框架约定
  trace-report  基于 AW 调用轨迹做可达/区分/冗余审计
  check-design  核对 .ctb/design.json 与真实收集结果、当前脚本摘要的一致性

所有输出均为 JSON（stdout），退出码：0 正常，2 检查发现阻塞项，1 工具自身错误。
"""
from __future__ import print_function

import argparse
import ast
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

FORMAL_FILES = ("testaw.py", "udpunittest.py")
MARK = "@@CTB_COLLECT@@"

COLLECT_SNIPPET = r'''
import importlib, json, sys, unittest
sys.path.insert(0, '.')
mod = importlib.import_module(sys.argv[1])
loader = unittest.TestLoader()
suite = loader.loadTestsFromModule(mod)
ids = []
def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            walk(t)
        else:
            ids.append(t.id())
walk(suite)
print("%s" + json.dumps({"ids": ids, "count": suite.countTestCases(),
      "loader_errors": [str(e) for e in getattr(loader, "errors", [])]}))
''' % MARK


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def formal_hashes(framework):
    out = {}
    for name in FORMAL_FILES:
        p = os.path.join(framework, name)
        out[name] = sha256_file(p) if os.path.isfile(p) else None
    return out


def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- collect
def main_block_custom_suite(udp_path):
    """检测 __main__ 中是否自定义了 suite/过滤，若有则 loader 收集可能与实际运行不同。"""
    try:
        src = open(udp_path, encoding="utf-8").read()
    except (IOError, OSError, UnicodeDecodeError):
        return []
    hits = []
    m = re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:(.*)", src, re.S)
    if m:
        for kw in ("addTest", "makeSuite", "defaultTest", "loadTestsFromTestCase",
                   "loadTestsFromName", "argv="):
            if kw in m.group(1):
                hits.append(kw)
    return hits


def collect(framework, module="udpunittest", python=None, timeout=120):
    python = python or sys.executable
    framework = os.path.abspath(framework)
    res = {"framework": framework, "module": module, "python": python,
           "files_sha256": formal_hashes(framework)}
    try:
        p = subprocess.run([python, "-c", COLLECT_SNIPPET, module], cwd=framework,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        text = p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        res.update(status="error", error="collect_timeout: 导入模块可能在 import 时启动了 DUT 或阻塞")
        return res
    payload = None
    noise = []
    for line in text.splitlines():
        if line.startswith(MARK):
            payload = json.loads(line[len(MARK):])
        elif line.strip():
            noise.append(line)
    if payload is None:
        res.update(status="error", error="collect_failed", output_tail=noise[-30:])
        return res
    ids = payload["ids"]
    methods = {}
    for i in ids:
        methods.setdefault(i.rsplit(".", 1)[-1], []).append(i)
    res.update(
        status="ok" if not payload["loader_errors"] else "error",
        collected_count=payload["count"],
        testcase_ids=ids,
        loader_errors=payload["loader_errors"],
        duplicate_method_names={k: v for k, v in methods.items() if len(v) > 1},
        import_output=noise[-20:],
        custom_suite_in_main=main_block_custom_suite(os.path.join(framework, module + ".py")),
    )
    warns = []
    if noise:
        warns.append("import 时有输出：确认导入模块不会自动启动 DUT/产生副作用")
    if res["custom_suite_in_main"]:
        warns.append("__main__ 自定义了 suite/参数：以 run 的实际执行 ID 为准核对收集结果")
    if res["duplicate_method_names"]:
        warns.append("不同类中存在同名方法：平台若只给方法名，映射将有歧义")
    res["warnings"] = warns
    return res


def cmd_collect(a):
    r = collect(a.framework, a.module, a.python)
    if a.out:
        write_json(a.out, r)
    emit(r, 0 if r.get("status") == "ok" else 2)


# ---------------------------------------------------------------- run
VERBOSE_HEAD = re.compile(r"^(test\w*) \(([\w\.]+)\)")
RESULT_TAIL = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)\s*$")
RAN = re.compile(r"^Ran (\d+) tests? in ([\d\.]+)s", re.M)
FINAL = re.compile(r"^(OK|FAILED)(?: \((.*)\))?\s*$", re.M)


def parse_unittest_text(text):
    lines = text.splitlines()
    per = []
    for idx, line in enumerate(lines):
        m = VERBOSE_HEAD.match(line)
        if not m:
            continue
        name, inner = m.group(1), m.group(2)
        full = inner if inner.endswith("." + name) else inner + "." + name
        outcome = None
        for j in range(idx, min(idx + 4, len(lines))):
            t = RESULT_TAIL.search(lines[j])
            if t:
                outcome = t.group(1)
                break
        if outcome is not None:
            if outcome.startswith("skipped"):
                outcome = "skipped"
            per.append({"id": full, "outcome": outcome})
    summary = {}
    m = None
    for m in RAN.finditer(text):
        pass
    if m:
        summary["ran"] = int(m.group(1))
        summary["seconds"] = float(m.group(2))
    f = None
    for f in FINAL.finditer(text):
        pass
    if f:
        summary["final"] = f.group(1)
        kv = {}
        for part in (f.group(2) or "").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip()] = int(v.strip())
        summary["failures"] = kv.get("failures", 0)
        summary["errors"] = kv.get("errors", 0)
        summary["skipped"] = kv.get("skipped", 0)
        summary["expected_failures"] = kv.get("expected failures", 0)
        summary["unexpected_successes"] = kv.get("unexpected successes", 0)
    timeouts = sorted(set(re.findall(r"^(?:ERROR|FAIL): (test\w*) .*\n(?:.*\n){0,40}?.*(?:Timeout|timed out|TimeoutError)",
                                     text, re.M)))
    return per, summary, timeouts


def cmd_run(a):
    fw = os.path.abspath(a.framework)
    out_dir = os.path.abspath(a.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    before = formal_hashes(fw)
    col = collect(fw, a.module, a.python)
    env = dict(os.environ)
    trace_path = None
    if a.trace:
        trace_path = os.path.join(out_dir, "trace.jsonl")
        if os.path.exists(trace_path):
            os.remove(trace_path)
        env["CTB_TRACE_FILE"] = trace_path
    log_mtimes = {p: os.path.getmtime(p) for p in glob.glob(os.path.join(fw, "*.log"))}
    started = time.time()
    cmd = [a.python or sys.executable, a.entry]
    timed_out = False
    try:
        p = subprocess.run(cmd, cwd=fw, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=a.timeout)
        exit_code = p.returncode
        console = p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = None
        console = (e.stdout or b"").decode("utf-8", "replace")
    elapsed = round(time.time() - started, 3)
    with open(os.path.join(out_dir, "console.log"), "w", encoding="utf-8") as f:
        f.write(console)
    fresh_logs = []
    for pth in glob.glob(os.path.join(fw, "*.log")):
        mt = os.path.getmtime(pth)
        if mt >= started - 1 and mt != log_mtimes.get(pth):
            dst = os.path.join(out_dir, os.path.basename(pth))
            shutil.copyfile(pth, dst)
            fresh_logs.append(dst)
    texts = [console] + [open(x, encoding="utf-8", errors="replace").read() for x in fresh_logs]
    chosen = None
    for t in texts:
        if RAN.search(t):
            chosen = t
    per, summary, timeouts = parse_unittest_text(chosen or "")
    after = formal_hashes(fw)
    for x in per:
        if x["id"].startswith("__main__."):
            x["id"] = a.module + x["id"][len("__main__"):]
    executed_ids = [x["id"] for x in per] if per else None
    coll_ids = col.get("testcase_ids") or []
    id_match = None
    if executed_ids is not None:
        id_match = sorted(executed_ids) == sorted(coll_ids)
    blockers = []
    if col.get("status") != "ok":
        blockers.append("collect_failed")
    if timed_out:
        blockers.append("run_timeout")
    if chosen is None:
        blockers.append("no_unittest_summary_found_in_fresh_logs")
    if summary.get("final") != "OK":
        blockers.append("unittest_not_ok")
    for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        if summary.get(k):
            blockers.append("%s=%d" % (k, summary[k]))
    if summary.get("ran") is not None and summary.get("ran") != col.get("collected_count"):
        blockers.append("ran_count_differs_from_collected")
    if id_match is False:
        blockers.append("executed_ids_differ_from_collected")
    if id_match is None:
        blockers.append("no_per_test_lines: 需 verbosity=2 的日志才能逐条核对 ID")
    if before != after:
        blockers.append("formal_files_changed_during_run")
    trace_info = None
    if trace_path:
        trace_info = {"path": trace_path, "exists": os.path.exists(trace_path)}
        if not trace_info["exists"]:
            blockers.append("trace_requested_but_not_written: testaw 未接入 CTB_TRACE_FILE 钩子")
    res = {
        "status": "passed" if not blockers else "failed",
        "blockers": blockers,
        "command": " ".join(cmd),
        "cwd": fw,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "elapsed_seconds": elapsed,
        "exit_code": exit_code,
        "files_sha256": after,
        "files_sha256_before": before,
        "collected_count": col.get("collected_count"),
        "testcase_ids": coll_ids,
        "run_count": summary.get("ran"),
        "summary": summary,
        "executed_ids_available": executed_ids is not None,
        "id_match": id_match,
        "not_ok": [x for x in per if x["outcome"] != "ok"],
        "timeout_suspects": timeouts,
        "log_paths": [os.path.join(out_dir, "console.log")] + fresh_logs,
        "trace": trace_info,
        "collect_warnings": col.get("warnings"),
    }
    write_json(os.path.join(out_dir, "validation.json"), res)
    emit(res, 0 if res["status"] == "passed" else 2)


# ---------------------------------------------------------------- lint
ALLOWED_ASSERTS = ("assertTrue", "assertFalse")


def _call_name(node):
    f = node.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def lint_udp(path, aw_module="testaw"):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    errors, warns = [], []
    seen_methods = {}
    per_test = {}
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
        overrides = set(names) & set(ALLOWED_ASSERTS)
        dup = sorted(set(x for x in names if names.count(x) > 1))
        for d in dup:
            errors.append({"rule": "duplicate_method", "where": "%s.%s" % (cls.name, d),
                           "why": "后定义覆盖前定义，收集数会少于源码方法数"})
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]:
            where = "%s.%s" % (cls.name, fn.name)
            seen_methods.setdefault(fn.name, []).append(cls.name)
            info = {"line": fn.lineno, "asserts": 0, "aw_calls": 0, "unobserved_aw_calls": []}
            if not re.match(r"^test_\d+_", fn.name):
                warns.append({"rule": "naming", "where": where, "why": "模板约定 test_序号_功能描述"})
            decos = [ast.dump(d) for d in fn.decorator_list]
            if not any("timeout" in d for d in decos):
                warns.append({"rule": "no_timeout_decorator", "where": where})
            if any(("skip" in d) or ("expectedFailure" in d) for d in decos):
                errors.append({"rule": "skip_or_expected_failure", "where": where})
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    name = _call_name(node)
                    if name.startswith("self.assert"):
                        meth = name.split(".", 1)[1]
                        if meth in ALLOWED_ASSERTS:
                            info["asserts"] += 1
                        elif overrides:
                            errors.append({"rule": "assert_bypasses_case_failed", "where": where,
                                           "line": node.lineno, "call": meth})
                    if name.startswith(aw_module + "."):
                        info["aw_calls"] += 1
                if isinstance(node, ast.Assert):
                    errors.append({"rule": "native_assert", "where": where, "line": node.lineno})
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    name = _call_name(node.value)
                    if name.startswith(aw_module + "."):
                        info["unobserved_aw_calls"].append({"line": node.lineno, "call": name})
                if isinstance(node, ast.ExceptHandler):
                    body_kinds = set(type(b).__name__ for b in node.body)
                    reraises = any(isinstance(b, ast.Raise) for b in ast.walk(node))
                    if not reraises and body_kinds <= {"Pass", "Continue", "Expr"}:
                        errors.append({"rule": "swallowed_exception", "where": where, "line": node.lineno})
            if info["asserts"] == 0:
                errors.append({"rule": "no_assertion", "where": where})
            if info["unobserved_aw_calls"]:
                warns.append({"rule": "unobserved_aw_call", "where": where,
                              "lines": [x["line"] for x in info["unobserved_aw_calls"]],
                              "why": "返回值被丢弃：前置失败会静默，后续断言可能在错误状态上验证"})
            per_test[where] = info
    for m, classes in seen_methods.items():
        if len(classes) > 1:
            warns.append({"rule": "same_method_in_classes", "where": m, "classes": classes})
    return errors, warns, per_test


def lint_aw(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    warns = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]:
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
        if not returns:
            warns.append({"rule": "aw_returns_nothing", "where": fn.name,
                          "why": "AW 不返回响应，unittest 无法观察结果"})
    src = open(path, encoding="utf-8").read()
    if "socket" in src and "import socket" in src:
        warns.append({"rule": "raw_socket_in_aw", "why": "AW 应通过官方 testtool 通信"})
    return warns


def cmd_lint(a):
    fw = os.path.abspath(a.framework)
    errors, warns, per = lint_udp(os.path.join(fw, "udpunittest.py"), a.aw_module)
    warns += lint_aw(os.path.join(fw, "testaw.py"))
    res = {"files_sha256": formal_hashes(fw), "errors": errors, "warnings": warns,
           "tests": len(per),
           "tests_with_unobserved_calls": sum(1 for v in per.values() if v["unobserved_aw_calls"]),
           "per_test": per if a.verbose else None}
    emit(res, 2 if errors else 0)


# ---------------------------------------------------------------- trace-report
def cmd_trace_report(a):
    code_re = re.compile(a.code_regex)
    cmd_re = re.compile(a.cmd_regex)
    ok_codes = set(x.strip() for x in a.ok_codes.split(",") if x.strip())
    cases = {}
    order = []
    with open(a.trace, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            cid = ev.get("case") or "<no-case>"
            if cid not in cases:
                cases[cid] = []
                order.append(cid)
            cm = cmd_re.search(str(ev.get("cmd", "")))
            rm = code_re.search(str(ev.get("resp", "")))
            cases[cid].append((cm.group(1) if cm else str(ev.get("cmd"))[:20],
                               rm.group(1) if rm else "?" + str(ev.get("resp"))[:20]))
    matrix = {}
    observed_codes = set()
    shapes = {}
    stuck, ends_rejected = [], []
    for cid in order:
        steps = cases[cid]
        for c, r in steps:
            matrix.setdefault(c, {}).setdefault(r, set()).add(cid)
            observed_codes.add(r)
        shapes.setdefault(tuple(steps), []).append(cid)
        first_bad = next((i for i, s in enumerate(steps) if s[1] not in ok_codes), None)
        if first_bad is not None and first_bad < len(steps) - 1:
            tail = steps[first_bad:]
            if len(set(r for _, r in tail)) == 1 and len(tail) >= 3:
                stuck.append({"case": cid, "from_step": first_bad, "code": tail[0][1], "len": len(tail)})
        if steps and steps[-1][1] not in ok_codes and all(r in ok_codes for _, r in steps[:-1]):
            ends_rejected.append(cid)
    expected = set(x.strip() for x in (a.expected_codes or "").split(",") if x.strip())
    dup_groups = [v for v in shapes.values() if len(v) > 1]
    res = {
        "cases_traced": len(order),
        "no_case_events": len(cases.get("<no-case>", [])),
        "command_response_matrix": {c: {r: len(s) for r, s in rs.items()} for c, rs in matrix.items()},
        "observed_codes": sorted(observed_codes),
        "expected_codes_never_observed": sorted(expected - observed_codes) if expected else None,
        "stuck_on_same_rejection": stuck,
        "single_rejection_cases": len(ends_rejected),
        "identical_trace_groups": dup_groups,
        "notes": [
            "stuck_on_same_rejection：前置失败后一直收到同一拒绝码，目标步骤大概率未被到达",
            "identical_trace_groups：命令/响应码序列完全相同的用例，若无独立断言差异即为冗余",
            "expected_codes_never_observed：规格/JAR 中存在但从未触发的响应，是对应守卫的可达缺口",
        ],
    }
    if a.cases:
        res["cases"] = {cid: cases[cid] for cid in order}
    emit(res)


# ---------------------------------------------------------------- check-design
def cmd_check_design(a):
    d = load_json(a.design)
    fw = os.path.abspath(a.framework)
    col = load_json(a.collected) if a.collected else collect(fw)
    ids = set(col.get("testcase_ids") or [])
    live = formal_hashes(fw)
    errors, warns = [], []
    tests = d.get("tests") or {}
    hyps = d.get("hypotheses") or []
    mechs = d.get("mechanisms") or []
    untracked = sorted(ids - set(tests))
    dangling = sorted(set(tests) - ids)
    if untracked:
        warns.append({"rule": "untracked_testcases", "ids": untracked,
                      "why": "未登记假设的用例无法说明其判别价值"})
    if dangling:
        errors.append({"rule": "design_tests_not_collected", "ids": dangling})
    val = d.get("validation") or {}
    v_ok = val.get("status") == "passed" and val.get("files_sha256") == live
    if val.get("status") == "passed" and val.get("files_sha256") != live:
        errors.append({"rule": "validation_stale", "why": "脚本在验证后被修改，需重新完整验证"})
    elements = set()
    for m in mechs:
        for kind in ("guards", "effects", "outputs", "transitions"):
            for e in m.get(kind) or []:
                if isinstance(e, dict) and e.get("id"):
                    elements.add(e["id"])
    targeted = set()
    matrix = {}
    for h in hyps:
        tgt = h.get("target")
        targeted.add(tgt)
        mech = (tgt or "?").split(".")[0]
        matrix.setdefault(mech, {}).setdefault(h.get("operator", "?"), 0)
        matrix[mech][h.get("operator", "?")] += 1
        ht = h.get("tests") or []
        st = h.get("status")
        if h.get("priority") == "P0" and st not in ("verified", "gap"):
            errors.append({"rule": "p0_not_verified", "id": h.get("id"), "status": st})
        if st == "verified":
            missing = [t for t in ht if t not in ids]
            if not ht or missing:
                errors.append({"rule": "verified_without_collected_test", "id": h.get("id"), "missing": missing})
            if not v_ok:
                errors.append({"rule": "verified_but_validation_not_current", "id": h.get("id")})
            for k in ("reach", "infect", "propagate"):
                if not h.get(k):
                    errors.append({"rule": "rip_incomplete", "id": h.get("id"), "missing": k})
        if st == "gap" and not h.get("gap_reason"):
            errors.append({"rule": "gap_without_reason", "id": h.get("id")})
    untargeted = sorted(e for e in elements if e not in targeted)
    if untargeted:
        warns.append({"rule": "model_elements_without_hypothesis", "ids": untargeted})
    for m in mechs:
        if m.get("id") not in matrix:
            warns.append({"rule": "mechanism_untargeted", "id": m.get("id")})
    for rt in d.get("required_techniques") or []:
        if rt.get("status") == "met":
            ev = rt.get("evidence_tests") or []
            if not ev or any(t not in ids for t in ev):
                errors.append({"rule": "technique_met_without_tests", "id": rt.get("id")})
            if rt.get("definition") != "confirmed":
                errors.append({"rule": "technique_met_with_unconfirmed_definition", "id": rt.get("id")})
    res = {"collected_count": len(ids), "files_sha256": live, "validation_current": v_ok,
           "mechanisms": len(mechs), "hypotheses": len(hyps),
           "mechanism_operator_matrix": matrix, "errors": errors, "warnings": warns}
    emit(res, 2 if errors else 0)


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("collect")
    p.add_argument("--framework", required=True)
    p.add_argument("--module", default="udpunittest")
    p.add_argument("--python", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("run")
    p.add_argument("--framework", required=True)
    p.add_argument("--entry", default="udpunittest.py")
    p.add_argument("--module", default="udpunittest")
    p.add_argument("--python", default=None)
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--trace", action="store_true", help="设置 CTB_TRACE_FILE，要求 testaw 已接入轨迹钩子")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("lint")
    p.add_argument("--framework", required=True)
    p.add_argument("--aw-module", default="testaw")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_lint)
    p = sub.add_parser("trace-report")
    p.add_argument("--trace", required=True)
    p.add_argument("--code-regex", default=r"^\s*([A-Za-z]*\d+)")
    p.add_argument("--cmd-regex", default=r"^\s*([^\s:]+)")
    p.add_argument("--ok-codes", default="E000")
    p.add_argument("--expected-codes", default=None, help="逗号分隔：规格/JAR 中出现的全部响应码")
    p.add_argument("--cases", action="store_true")
    p.set_defaults(fn=cmd_trace_report)
    p = sub.add_parser("check-design")
    p.add_argument("--design", required=True)
    p.add_argument("--framework", required=True)
    p.add_argument("--collected", default=None)
    p.set_defaults(fn=cmd_check_design)
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        sys.exit(1)
    a.fn(a)


if __name__ == "__main__":
    main()
