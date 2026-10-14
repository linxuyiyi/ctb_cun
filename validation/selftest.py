#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 辅助工具端到端自测（模拟题 + 模拟平台反馈）。

证明的是：工具链的收集/运行/审计/门禁/冻结/统计/停止事实计算按契约工作。
不证明：真实平台 API 字段、真实 JAR 启停、真实命中能力。
"""
import ast
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E2E = os.path.join(ROOT, ".opencode/skills/e2e-test/scripts/e2e_evidence.py")
LEDGER_DIR = os.path.join(ROOT, ".opencode/skills/loop/scripts")
LEDGER = os.path.join(LEDGER_DIR, "ctb_ledger.py")
FIXTURE = os.path.join(ROOT, "validation/fixture/900001")
WORK = os.path.join(ROOT, "validation/_work")
SUBJ = os.path.join(WORK, "softtest_answer/900001")
FW = os.path.join(SUBJ, "python_framework")

results = []


def check(name, cond, detail=None):
    results.append((name, bool(cond)))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "  -> %s" % detail))


def run(tool, *args):
    p = subprocess.run([sys.executable, tool] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    try:
        return p.returncode, json.loads(out)
    except ValueError:
        return p.returncode, {"_raw": out}


def ledger(*args):
    return run(LEDGER, *args)


def e2e(*args):
    return run(E2E, *args)


def write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def py38_syntax():
    for p in (E2E, LEDGER):
        try:
            ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
            check("py38 语法: " + os.path.basename(p), True)
        except SyntaxError as e:
            check("py38 语法: " + os.path.basename(p), False, e)


def make_design(val, hyps, increments=None):
    tests = {}
    for h in hyps:
        for t in h["tests"]:
            tests.setdefault(t, {"hypotheses": [], "mechanisms": []})["hypotheses"].append(h["id"])
    return {
        "schema": "ctb.design/v2", "subject_id": "900001", "profile": "BALANCED",
        "mechanisms": [{"id": "M1", "name": "存款累计", "state": ["balance", "deposited"],
                        "guards": [{"id": "M1.G1", "rule": "累计<=2000"}],
                        "effects": [{"id": "M1.E1", "write": "balance+=amt"}]},
                       {"id": "M2", "name": "取款", "state": ["balance"],
                        "guards": [{"id": "M2.G1", "rule": "amt<=balance"}],
                        "effects": [{"id": "M2.E1", "write": "balance-=amt"}]}],
        "hypotheses": hyps, "tests": tests, "gaps": [],
        "validation": val, "increments": increments or [],
        "delivery": {"ready": True, "blockers": [], "answer_origin": "self_authored"},
    }


def main():
    py38_syntax()
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.copytree(FIXTURE, SUBJ)
    os.makedirs(os.path.join(SUBJ, ".ctb"))

    # ---------- E2E 侧
    code, col = e2e("collect", "--framework", FW)
    check("collect: 真实收集 3 条", code == 0 and col["collected_count"] == 3, col)
    code, val = e2e("run", "--framework", FW, "--out-dir", os.path.join(SUBJ, ".ctb/evidence/v1"), "--trace")
    check("run: 正确环境通过且执行 ID 与收集 ID 一致", code == 0 and val["status"] == "passed" and val["id_match"], val.get("blockers"))
    code, lint = e2e("lint", "--framework", FW)
    rules = [w["rule"] for w in lint["warnings"]]
    check("lint: 识别 test_03 丢弃 AW 返回值", "unobserved_aw_call" in rules, lint)
    code, tr = e2e("trace-report", "--trace", val["trace"]["path"],
                   "--expected-codes", "E000,E001,E002,E003,E004,E005")
    never = tr["expected_codes_never_observed"]
    check("trace-report: 指出从未触发的响应码 E001/E002/E005", set(never) == {"E001", "E002", "E005"}, tr)

    ids = val["testcase_ids"]
    t1, t2, t3 = ids
    hyps = [
        {"id": "H1", "target": "M1.E1", "operator": "E-MISS", "priority": "P0", "status": "verified",
         "reach": "开户", "infect": "存 300", "propagate": "查询=300", "tests": [t1]},
        {"id": "H2", "target": "M1.G1", "operator": "G-BND", "priority": "P0", "status": "verified",
         "reach": "累计到 2000", "infect": "再存 1", "propagate": "E003 + 余额保持 2000", "tests": [t2]},
        {"id": "H3", "target": "M2.G1", "operator": "G-BND", "priority": "P1", "status": "verified",
         "reach": "开户", "infect": "余额 0 取 5", "propagate": "E004", "tests": [t3]},
    ]
    dpath = os.path.join(SUBJ, ".ctb/design.json")
    write(dpath, make_design(val, hyps))
    code, cd = e2e("check-design", "--design", dpath, "--framework", FW)
    check("check-design: 一致", code == 0, cd.get("errors"))

    # ---------- Loop 侧：准备
    code, pf = ledger("preflight", "--subject-root", SUBJ)
    check("preflight: 已有完整资料 → reuse（不调用 get）", pf["recommended_action"] == "reuse", pf)
    code, vt = ledger("vault", "--subject-root", SUBJ, "--vault-root", os.path.join(WORK, ".ctb_vault"))
    check("vault: 备份脚本与 .ctb", code == 0 and os.path.isdir(vt["vault_path"]), vt)
    ledger("init", "--subject-root", SUBJ, "--subject-id", "900001", "--title", "模拟钱包")
    code, again = ledger("init", "--subject-root", SUBJ, "--subject-id", "900001")
    check("init: 幂等，不重置历史", again.get("status") == "exists", again)
    contract = {"status": "confirmed_for_fixture", "source": "validation/selftest.py 模拟反馈",
                "mapping_complete": False,
                "feedback_map": {"status_path": "result.status", "success_values": ["DONE"],
                                 "pending_values": ["RUNNING"], "judge_id_path": "result.judgeId",
                                 "case_map_path": "result.cases", "case_map_format": "dict",
                                 "total_bug_path": "result.totalBug",
                                 "bug_category_path": "result.bugTypes",
                                 "category_totals_path": "result.typeTotals",
                                 "category_label_mode": "single_label",
                                 "judge_cost_path": "result.judgeCost"}}
    cpath = os.path.join(WORK, "contract.json")
    write(cpath, contract)
    ledger("contract", "--subject-root", SUBJ, "--file", cpath)
    ledger("start-run", "--subject-root", SUBJ, "--max-rounds", "3", "--mode", "exercise")

    # ---------- 第 1 轮
    code, g = ledger("gate", "--subject-root", SUBJ)
    check("gate r1: 通过", code == 0 and g["pass"], g)
    code, fz = ledger("freeze", "--subject-root", SUBJ)
    check("freeze r1: 生成 r01 快照", code == 0 and fz["round"] == 1, fz)
    sys.path.insert(0, LEDGER_DIR)
    import ctb_ledger as L
    L.mark_submitting(SUBJ, 1)
    raw1 = {"result": {"status": "DONE", "judgeId": "J-1", "totalBug": 6, "judgeCost": 12,
                       "cases": {"test_01_deposit_effect_observed": ["B1"],
                                 "MyTestCase1.test_02_cumulative_limit_boundary": ["B2", "B1"]},
                       "bugTypes": {"B1": "功能", "B2": "功能", "B3": "可靠性"},
                       "typeTotals": {"功能": 4, "可靠性": 1, "安全": 1, "性能": 0}},
            "token": "SECRET-SHOULD-BE-MASKED"}
    st = L.attach_raw(SUBJ, 1, raw1, 1.234)
    check("attach_raw: 提交前后摘要一致", st == "raw_saved", st)
    rawdoc = json.load(open(os.path.join(SUBJ, ".ctb/rounds/r01/feedback_raw.json")))
    check("attach_raw: 凭据脱敏", rawdoc["raw"]["token"] == "***")
    code, rec = ledger("record", "--subject-root", SUBJ, "--round", "1")
    check("record r1: 命中 2 个唯一 BUG（B1 去重）", rec["hit"] == 2 and rec["new"] == 2, rec)
    check("record r1: 映射不完整时未映射用例记 UNKNOWN 而非 MISS",
          rec["classification_counts"] == {"NEW": 2, "REPEAT": 0, "MISS": 0, "UNKNOWN": 1}, rec)
    code, rec2 = ledger("record", "--subject-root", SUBJ, "--round", "1")
    check("record: 同一 judge_id 重复记录幂等", rec2.get("status") == "already_recorded", rec2)
    sess = json.load(open(os.path.join(SUBJ, ".ctb/session.json")))
    cs = sess["rounds"][0]["category_stats"]["by_category"]
    check("分类统计: 功能类当前命中 2/4，性能总数 0 → 不适用",
          cs["功能"]["current_remaining"] == 2 and cs["性能"]["current_hit_rate"] == "not_applicable", cs)
    code, dc = ledger("decide", "--subject-root", SUBJ)
    check("decide r1: 有新增 → continue_if_new_hypotheses", dc["advice"] == "continue_if_new_hypotheses", dc)

    # ---------- 反例：验证后修改脚本 → 门禁阻塞
    udp = os.path.join(FW, "udpunittest.py")
    src = open(udp, encoding="utf-8").read()
    os.chmod(udp, 0o644)
    open(udp, "w", encoding="utf-8").write(src.replace(
        '        testaw.open_account("12345")\n        self.assertTrue(testaw.withdraw("12345", 5).startswith("E004"))',
        '        self.assertTrue(testaw.open_account("12345") == "E000:Success")\n'
        '        self.assertTrue(testaw.deposit("12345", 10) == "E000:Success")\n'
        '        self.assertTrue(testaw.withdraw("12345", 11) == "E004:Insufficient")\n'
        '        self.assertTrue(testaw.query("12345") == "E000:10")\n'
        '        self.assertTrue(testaw.withdraw("12345", 10) == "E000:Success")\n'
        '        self.assertTrue(testaw.query("12345") == "E000:0")\n\n'
        '    @timeout(global_default_timeout)\n'
        '    def test_04_accounts_isolated(self):\n'
        '        self.assertTrue(testaw.open_account("111") == "E000:Success")\n'
        '        self.assertTrue(testaw.open_account("222") == "E000:Success")\n'
        '        self.assertTrue(testaw.deposit("111", 700) == "E000:Success")\n'
        '        self.assertTrue(testaw.deposit("222", 1000) == "E000:Success")\n'
        '        self.assertTrue(testaw.deposit("222", 1000) == "E000:Success")\n'
        '        self.assertTrue(testaw.deposit("111", 1000) == "E000:Success")\n'
        '        self.assertTrue(testaw.query("111") == "E000:1700")'))
    code, g = ledger("gate", "--subject-root", SUBJ, "--dry")
    check("gate: 验证后改脚本 → 阻塞（摘要不一致）", not g["pass"] and any("摘要" in b for b in g["blockers"]), g)

    # ---------- 第 2 轮：E2E 重新验证 + 增量记录
    code, val2 = e2e("run", "--framework", FW, "--out-dir", os.path.join(SUBJ, ".ctb/evidence/v2"), "--trace")
    check("run v2: 重新完整验证通过（4 条）", code == 0 and val2["collected_count"] == 4, val2.get("blockers"))
    ids2 = val2["testcase_ids"]
    hyps[2].update(infect="余额 10 取 11（=余额+1）", propagate="E004 + 余额保持 10 + 取 10 成功归零",
                   tests=[ids2[2]])
    hyps.append({"id": "H4", "target": "M1.G1", "operator": "G-SCOPE", "priority": "P0", "status": "verified",
                 "reach": "两账户", "infect": "账户 222 累计满额、111 未满", "propagate": "111 仍可存且余额 1700",
                 "tests": [ids2[3]]})
    inc = [{"for_round": 2, "input_focus": "M2 拒绝路径仅看返回码前缀；M1 累计未区分账户范围",
            "new_hypotheses": ["H4"], "repaired_tests": [{"id": ids2[2], "fix": "补余额保持与后续操作观察"}],
            "why_old_insufficient": "test_03 余额为 0，'>=' 与 '>' 实现无差异；拒绝后未观察状态"}]
    design2 = make_design(val2, hyps, inc)
    design2["delivery"]["answer_origin"] = "self_authored"
    write(dpath, design2)
    code, g = ledger("gate", "--subject-root", SUBJ)
    check("gate r2: 通过（有增量说明）", code == 0 and g["pass"], g)
    ledger("freeze", "--subject-root", SUBJ)
    L.mark_submitting(SUBJ, 2)
    raw2 = {"result": {"status": "DONE", "judgeId": "J-2", "totalBug": 6,
                       "cases": {"test_01_deposit_effect_observed": ["B1"],
                                 "test_02_cumulative_limit_boundary": ["B2"],
                                 "test_03_withdraw_reject_keeps_state": ["B3"]},
                       "bugTypes": {"B1": "功能", "B2": "功能", "B3": "可靠性"},
                       "typeTotals": {"功能": 4, "可靠性": 1, "安全": 1, "性能": 0}}}
    L.attach_raw(SUBJ, 2, raw2, 2.5)
    code, rec = ledger("record", "--subject-root", SUBJ, "--round", "2")
    check("record r2: 新增 1（B3），累计 3", rec["new"] == 1 and rec["cumulative"] == 3, rec)
    sess = json.load(open(os.path.join(SUBJ, ".ctb/session.json")))
    r2 = sess["rounds"][1]
    check("record r2: new_testcase_count 基于冻结 ID 集合差 = 1", r2["new_testcase_count"] == 1, r2)
    check("record r2: NEW 只给命中新 BUG 的用例",
          r2["feedback"]["classifications"]["NEW"] == [ids2[2]], r2["feedback"]["classifications"])

    code, wo = ledger("work-order", "--subject-root", SUBJ, "--constraint", "test")
    check("work-order: 由台账生成，含用例命中令牌与分类，不含原始报告",
          wo["based_on_round"] == 2 and wo["case_hits"][ids2[2]] == ["B3"] and "raw" not in wo, wo)

    # ---------- 停止规则单元检查：无效轮不计入无收益
    def rnd(n, new, valid=True):
        return {"round": n, "run_id": "run-01", "status": "evaluated", "submission_verified": valid,
                "feedback": {"hit_bug_ids": ["B1"], "new_bug_ids": new}}
    s = {"runs": [{"run_id": "run-01", "max_rounds": 9}], "rounds": [rnd(1, ["B1"]), rnd(2, []), rnd(3, [])],
         "cumulative_hit_bug_ids": ["B1"], "total_bug": None, "best_round": 1}
    check("decide: 连续两个有效轮无新增 → stop_unless_model_correction",
          L.decide_facts(s)["advice"] == "stop_unless_model_correction", L.decide_facts(s))
    s["rounds"][2]["submission_verified"] = False
    f = L.decide_facts(s)
    check("decide: 提交未核实的轮次不计入无收益，且触发 submission_unverified",
          f["no_gain_streak"] == 1 and "submission_unverified" in f["hard_stops"], f)

    # ---------- 反例：评测异常且无返回 → evaluation_unknown → 硬停止，不重提
    open(dpath, "a").close()
    design3 = make_design(val2, hyps, inc + [{"for_round": 3, "new_hypotheses": ["H5"]}])
    write(dpath, design3)
    code, g = ledger("gate", "--subject-root", SUBJ)
    check("gate r3: 与上轮同版本 → 阻塞", not g["pass"] and any("完全相同" in b for b in g["blockers"]), g)
    code, g = ledger("gate", "--subject-root", SUBJ, "--allow-same-version")
    ledger("freeze", "--subject-root", SUBJ)
    L.mark_submitting(SUBJ, 3)
    L.attach_raw(SUBJ, 3, None, 30.0, error="ConnectionResetError()")
    code, dc = ledger("decide", "--subject-root", SUBJ)
    check("decide: 评测未知 → 硬停止 evaluation_unknown（不盲目重提）", "evaluation_unknown" in dc["hard_stops"], dc)
    code, fz = ledger("freeze", "--subject-root", SUBJ)
    check("freeze: 存在未结束轮次时拒绝新冻结", code != 0, fz)

    # ---------- 收尾与经验隔离
    gp = os.path.join(WORK, "gaps.json")
    write(gp, [{"id": "G1", "what": "E001/E002/E005 未触发", "next": "补无账户/金额非法/重复开户"}])
    code, fin = ledger("finalize", "--subject-root", SUBJ, "--status", "stopped",
                       "--stop-reason", "evaluation_unknown", "--gaps-file", gp)
    check("finalize: 最终版本指向最后有效轮 r2", fin["final_version"]["round"] == 2, fin)
    code, ls = ledger("lesson", "--subject-root", SUBJ, "--experience-root", os.path.join(WORK, "_exp"),
                      "--trigger", "累计限额规则", "--method", "B2 对应的用例写法",
                      "--evidence", "r2")
    check("lesson: 含 BUG ID 的经验被拒绝", code != 0 and "B2" in ls.get("leaked", []), ls)
    code, ls = ledger("lesson", "--subject-root", SUBJ, "--experience-root", os.path.join(WORK, "_exp"),
                      "--trigger", "按对象累计的限额", "--method", "多对象非对称取值观察未操作对象",
                      "--evidence", "900001 r2 增量")
    check("lesson: 抽象候选经验可写入", code == 0, ls)

    bad = [n for n, ok in results if not ok]
    print("\n%d/%d 通过" % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
