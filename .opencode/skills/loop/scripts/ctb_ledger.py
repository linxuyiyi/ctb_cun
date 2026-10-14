#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTB Loop 证据台账（仅标准库，兼容 Python 3.8）。

不是循环控制器：只做确定性的状态读写、门禁核对、快照冻结、反馈规范化和集合统计。
session.json 的唯一写入入口；design.json 只读。

CLI 子命令：preflight / vault / restore / init / start-run / contract / gate / freeze /
verify-frozen / record / decide / work-order / summary / finalize / lesson
Python 函数（供评测片段调用）：mark_submitting / attach_raw
"""
from __future__ import print_function

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys

FORMAL_FILES = ("testaw.py", "udpunittest.py")
PUBLIC_REQUIRED = ("python_framework/testaw.py", "python_framework/udpunittest.py",
                   "python_framework/testtool.py")
HERE = os.path.dirname(os.path.abspath(__file__))
E2E_TOOL = os.path.normpath(os.path.join(HERE, "..", "..", "e2e-test", "scripts", "e2e_evidence.py"))
SENSITIVE = re.compile(r"(token|password|passwd|cookie|secret|authorization|session_?id|credential)", re.I)
ROUND_SUBMITTED = ("submitting", "evaluated", "evaluation_failed", "evaluation_unknown")


# ---------------------------------------------------------------- utils
def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def live_hashes(root):
    fw = os.path.join(root, "python_framework")
    return {n: (sha256_file(os.path.join(fw, n)) if os.path.isfile(os.path.join(fw, n)) else None)
            for n in FORMAL_FILES}


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def session_path(root):
    return os.path.join(root, ".ctb", "session.json")


def design_path(root):
    return os.path.join(root, ".ctb", "design.json")


def load_session(root):
    s = read_json(session_path(root))
    if s is None:
        raise SystemExit("session.json 不存在：先运行 init")
    return s


def save_session(root, s, event=None, detail=None):
    if event:
        s.setdefault("events", []).append({"ts": now(), "event": event, "detail": detail})
    s["updated_at"] = now()
    write_json(session_path(root), s)


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if SENSITIVE.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def to_jsonable(obj):
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        if hasattr(obj, "__dict__"):
            return to_jsonable(vars(obj))
        if isinstance(obj, (list, tuple, set)):
            return [to_jsonable(x) for x in obj]
        if isinstance(obj, dict):
            return {str(k): to_jsonable(v) for k, v in obj.items()}
        return repr(obj)


def get_path(obj, path):
    """点号路径；数字段表示列表下标；'*' 展开列表。缺失返回 None。"""
    if path is None:
        return None
    cur = [obj]
    for seg in path.split("."):
        nxt = []
        for c in cur:
            if seg == "*" and isinstance(c, list):
                nxt.extend(c)
            elif isinstance(c, dict) and seg in c:
                nxt.append(c[seg])
            elif isinstance(c, list) and seg.isdigit() and int(seg) < len(c):
                nxt.append(c[int(seg)])
        cur = nxt
        if not cur:
            return None
    return cur if "*" in path.split(".") else cur[0]


def run_collect(root):
    fw = os.path.join(root, "python_framework")
    p = subprocess.run([sys.executable, E2E_TOOL, "collect", "--framework", fw],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        return json.loads(p.stdout.decode("utf-8", "replace"))
    except ValueError:
        return {"status": "error", "error": p.stdout.decode("utf-8", "replace")[-2000:]}


def round_obj(s, n):
    for r in s.get("rounds", []):
        if r["round"] == n:
            return r
    raise SystemExit("round %s 不存在" % n)


def valid_rounds(s):
    return [r for r in s.get("rounds", []) if r.get("status") == "evaluated"
            and r.get("submission_verified") and (r.get("feedback") or {}).get("hit_bug_ids") is not None]


def cumulative_before(s, n):
    acc = set()
    for r in valid_rounds(s):
        if r["round"] < n:
            acc |= set(r["feedback"]["hit_bug_ids"])
    return acc


# ---------------------------------------------------------------- preflight / vault / restore
def cmd_preflight(a):
    root = os.path.abspath(a.subject_root)
    exists = os.path.isdir(root)
    missing = [p for p in PUBLIC_REQUIRED if not os.path.exists(os.path.join(root, p))]
    spec = [x for x in (os.listdir(root) if exists else []) if x.endswith(".md") and "规格" in x]
    has_ctb = os.path.isdir(os.path.join(root, ".ctb"))
    sess = read_json(session_path(root)) if exists else None
    if not exists:
        action = "get"
    elif not missing and spec:
        action = "reuse"
    elif has_ctb or not missing:
        action = "vault_then_get"
    else:
        action = "get"
    emit({"subject_root": root, "exists": exists, "public_missing": missing, "spec_files": spec,
          "has_ctb_state": has_ctb, "session_status": (sess or {}).get("status"),
          "rounds_recorded": len((sess or {}).get("rounds", [])),
          "live_sha256": live_hashes(root) if exists else None,
          "recommended_action": action})


def cmd_vault(a):
    root = os.path.abspath(a.subject_root)
    sid = os.path.basename(root.rstrip("/"))
    dst = os.path.join(os.path.abspath(a.vault_root), sid, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst)
    saved = {}
    for n in FORMAL_FILES:
        src = os.path.join(root, "python_framework", n)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, n))
            saved[n] = sha256_file(src)
    if os.path.isdir(os.path.join(root, ".ctb")):
        shutil.copytree(os.path.join(root, ".ctb"), os.path.join(dst, ".ctb"))
    write_json(os.path.join(dst, "vault_manifest.json"),
               {"subject_root": root, "created_at": now(), "files_sha256": saved,
                "has_ctb": os.path.isdir(os.path.join(dst, ".ctb"))})
    emit({"vault_path": dst, "files_sha256": saved})


def cmd_restore(a):
    root = os.path.abspath(a.subject_root)
    src = os.path.abspath(a.vault_path)
    man = read_json(os.path.join(src, "vault_manifest.json"))
    if not man:
        emit({"error": "vault_manifest.json 缺失"}, 2)
    from_get = os.path.join(src, "from_get")
    os.makedirs(from_get, exist_ok=True)
    restored = []
    for n in FORMAL_FILES:
        live = os.path.join(root, "python_framework", n)
        if os.path.isfile(live):
            shutil.copy2(live, os.path.join(from_get, n))
        if os.path.isfile(os.path.join(src, n)) and a.scripts:
            shutil.copy2(os.path.join(src, n), live)
            restored.append(n)
    if os.path.isdir(os.path.join(src, ".ctb")):
        tgt = os.path.join(root, ".ctb")
        if os.path.isdir(tgt):
            shutil.copytree(tgt, os.path.join(from_get, ".ctb"))
            shutil.rmtree(tgt)
        shutil.copytree(os.path.join(src, ".ctb"), tgt)
        restored.append(".ctb/")
    emit({"restored": restored, "template_from_get_saved_to": from_get, "live_sha256": live_hashes(root)})


# ---------------------------------------------------------------- init / start-run
def cmd_init(a):
    root = os.path.abspath(a.subject_root)
    p = session_path(root)
    s = read_json(p)
    if s is not None:
        if s.get("subject_id") != a.subject_id:
            emit({"error": "session.subject_id=%s 与参数 %s 不一致" % (s.get("subject_id"), a.subject_id)}, 2)
        emit({"status": "exists", "rounds": len(s.get("rounds", [])), "session_status": s.get("status")})
    s = {
        "schema": "ctb.session/v2", "subject_id": a.subject_id, "title": a.title,
        "language": "python", "status": "running", "phase": "prepare",
        "created_at": now(),
        "platform_contract": {"status": "unconfirmed", "source": None, "evaluation_call": None,
                              "submission_reads": None, "feedback_map": None,
                              "mapping_complete": False, "mode_support": {}},
        "runs": [], "rounds": [], "cumulative_hit_bug_ids": [], "latest_valid_round": None,
        "best_round": None, "total_bug": None,
        "work_order": None, "remaining_gaps": [], "stop_reason": None, "final_version": None,
        "events": [],
    }
    save_session(root, s, "init")
    emit({"status": "created", "path": p})


def cmd_start_run(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    open_runs = [r for r in s["runs"] if not r.get("ended_at")]
    for r in open_runs:
        r["ended_at"] = now()
        r["stop_reason"] = r.get("stop_reason") or "superseded_by_new_run"
    run_id = "run-%02d" % (len(s["runs"]) + 1)
    s["runs"].append({"run_id": run_id, "started_at": now(), "mode": a.mode, "profile": a.profile,
                      "max_rounds": a.max_rounds, "rounds": [], "stop_reason": None, "ended_at": None})
    s["status"] = "running"
    s["stop_reason"] = None
    save_session(root, s, "start_run", run_id)
    emit({"run_id": run_id, "history_rounds": len(s["rounds"])})


CONTRACT_KEYS = ("status", "source", "evaluation_call", "submission_reads", "feedback_map",
                 "mapping_complete", "mode_support", "notes")


def cmd_contract(a):
    """写入已从 subject_api 实际代码/真实返回核实的平台契约；未知字段保持 null/false。"""
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    c = read_json(a.file)
    bad = sorted(set(c) - set(CONTRACT_KEYS))
    if bad:
        emit({"error": "未知契约字段 %s" % bad}, 2)
    if c.get("mapping_complete") and not c.get("source"):
        emit({"error": "mapping_complete=true 必须给出 source（代码位置或平台说明）"}, 2)
    old = s.get("platform_contract") or {}
    s["platform_contract"] = dict(old, **c)
    save_session(root, s, "contract", {"changed": sorted(c)})
    emit({"platform_contract": s["platform_contract"]})


def current_run(s):
    if not s["runs"] or s["runs"][-1].get("ended_at"):
        raise SystemExit("没有进行中的 run：先运行 start-run")
    return s["runs"][-1]


# ---------------------------------------------------------------- gate
def cmd_gate(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    d = read_json(design_path(root))
    blockers, notes = [], []
    if d is None:
        emit({"pass": False, "blockers": ["design.json 不存在"]}, 2)
    live = live_hashes(root)
    val = d.get("validation") or {}
    dl = d.get("delivery") or {}
    if not dl.get("ready"):
        blockers.append("delivery.ready != true: %s" % (dl.get("blockers") or ""))
    if val.get("status") != "passed":
        blockers.append("validation.status=%s" % val.get("status"))
    if val.get("files_sha256") != live:
        blockers.append("validation 摘要与当前脚本不一致（验证后被修改或验证不属于本版本）")
    if val.get("id_match") is not True:
        blockers.append("validation.id_match != true（未逐条核对执行 ID 与收集 ID）")
    for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        if (val.get("summary") or {}).get(k):
            blockers.append("validation.%s=%s" % (k, val["summary"][k]))
    col = run_collect(root)
    if col.get("status") != "ok":
        blockers.append("台账重新收集失败: %s" % (col.get("error") or col.get("loader_errors")))
    elif sorted(col.get("testcase_ids", [])) != sorted(val.get("testcase_ids") or []):
        blockers.append("重新收集的 testcase ID 与验证记录不一致")
    for h in d.get("hypotheses") or []:
        if h.get("priority") == "P0" and h.get("status") not in ("verified", "gap"):
            blockers.append("P0 假设 %s 状态 %s" % (h.get("id"), h.get("status")))
    origin = dl.get("answer_origin")
    if origin not in ("self_authored", "reference_answer", "mixed", "unconfirmed"):
        blockers.append("delivery.answer_origin 缺失或非法")
    prev = valid_rounds(s)
    if prev:
        last = prev[-1]
        if last.get("files_sha256") == live and not a.allow_same_version:
            blockers.append("与第 %d 轮提交版本完全相同：无新内容不提交" % last["round"])
        nxt = len(s["rounds"]) + 1
        inc = [x for x in d.get("increments") or [] if x.get("for_round") == nxt]
        if not inc:
            blockers.append("缺少 increments[for_round=%d]：增量轮必须说明新假设/修复及旧用例为何不足" % nxt)
        else:
            i = inc[-1]
            if not (i.get("new_hypotheses") or i.get("repaired_tests") or i.get("model_corrections")):
                blockers.append("increments 中没有新假设、RIP 修复或模型更正")
        hit_tests = set()
        for r in prev:
            cls = (r.get("feedback") or {}).get("classifications") or {}
            hit_tests |= set(cls.get("NEW", [])) | set(cls.get("REPEAT", []))
        ids = set(col.get("testcase_ids") or [])
        removed_ok = set()
        for i in d.get("increments") or []:
            for x in i.get("removed_tests") or []:
                if isinstance(x, dict) and x.get("reason"):
                    removed_ok.add(x.get("id"))
        lost = sorted(t for t in hit_tests if t not in ids and t not in removed_ok)
        if lost:
            blockers.append("历史命中用例被删除且无理由: %s" % lost)
    res = {"pass": not blockers, "blockers": blockers, "notes": notes, "live_sha256": live,
           "collected_count": col.get("collected_count"), "answer_origin": origin}
    if not a.dry:
        s["phase"] = "gate_passed" if not blockers else "gate_blocked"
        save_session(root, s, "gate", {"pass": not blockers, "blockers": blockers})
    emit(res, 0 if not blockers else 2)


# ---------------------------------------------------------------- freeze / verify
def make_readonly(path):
    for base, dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(base, f)
            os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def cmd_freeze(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    run = current_run(s)
    if s.get("phase") != "gate_passed":
        emit({"error": "phase=%s：须先 gate 通过" % s.get("phase")}, 2)
    pending = [r for r in s["rounds"] if r["status"] in ("frozen", "submitting")]
    if pending:
        emit({"error": "存在未结束的轮次 %s，先处理（submitting 视为 evaluation_unknown）" %
              [r["round"] for r in pending]}, 2)
    d = read_json(design_path(root))
    n = len(s["rounds"]) + 1
    rdir = os.path.join(root, ".ctb", "rounds", "r%02d" % n)
    if os.path.exists(rdir):
        emit({"error": "%s 已存在，不覆盖历史快照" % rdir}, 2)
    os.makedirs(rdir)
    fw = os.path.join(root, "python_framework")
    for f in FORMAL_FILES:
        shutil.copy2(os.path.join(fw, f), os.path.join(rdir, f))
    shutil.copy2(design_path(root), os.path.join(rdir, "design.json"))
    val = d.get("validation") or {}
    logs = []
    for lp in val.get("log_paths") or []:
        src = lp if os.path.isabs(lp) else os.path.join(root, lp)
        if os.path.isfile(src):
            dst = os.path.join(rdir, "validation_" + os.path.basename(src))
            shutil.copy2(src, dst)
            logs.append(os.path.basename(dst))
    col = run_collect(root)
    hashes = live_hashes(root)
    frozen_hashes = {f: sha256_file(os.path.join(rdir, f)) for f in FORMAL_FILES}
    if frozen_hashes != hashes or hashes != val.get("files_sha256"):
        shutil.rmtree(rdir)
        emit({"error": "冻结过程中脚本摘要不一致，已撤销快照"}, 2)
    prev_ids = set()
    vr = valid_rounds(s)
    if vr:
        pm = read_json(os.path.join(root, vr[-1]["snapshot_path"], "manifest.json")) or {}
        prev_ids = set(pm.get("testcase_ids") or [])
    ids = col.get("testcase_ids") or []
    manifest = {"round": n, "run_id": run["run_id"], "created_at": now(), "files_sha256": hashes,
                "design_sha256": sha256_file(design_path(root)),
                "collected_count": col.get("collected_count"), "testcase_ids": ids,
                "run_count": val.get("run_count"), "validation_logs": logs,
                "answer_origin": (d.get("delivery") or {}).get("answer_origin"),
                "profile_resolved": d.get("profile")}
    write_json(os.path.join(rdir, "manifest.json"), manifest)
    make_readonly(rdir)
    rel = os.path.relpath(rdir, root)
    s["rounds"].append({
        "round": n, "run_id": run["run_id"], "status": "frozen", "frozen_at": now(),
        "snapshot_path": rel, "answer_origin": manifest["answer_origin"], "files_sha256": hashes,
        "collected_count": manifest["collected_count"], "run_count": manifest["run_count"],
        "new_testcase_count": len(set(ids) - prev_ids) if vr else len(ids),
        "removed_testcase_count": len(prev_ids - set(ids)) if vr else 0,
        "submission_verified": None, "judge_id": None, "wall_time_seconds": None,
        "judge_cost_raw": None, "eval_error": None, "feedback": None, "category_stats": None,
    })
    run["rounds"].append(n)
    s["phase"] = "frozen"
    save_session(root, s, "freeze", rel)
    emit({"round": n, "snapshot_path": rel, "files_sha256": hashes,
          "collected_count": manifest["collected_count"]})


def verify_frozen(root, n):
    s = load_session(root)
    r = round_obj(s, n)
    man = read_json(os.path.join(root, r["snapshot_path"], "manifest.json"))
    live = live_hashes(root)
    return {"round": n, "match": live == man["files_sha256"], "live": live, "frozen": man["files_sha256"]}


def cmd_verify_frozen(a):
    r = verify_frozen(os.path.abspath(a.subject_root), a.round)
    emit(r, 0 if r["match"] else 2)


# ---------------------------------------------------------------- evaluation hooks (python API)
def mark_submitting(subject_root, n):
    root = os.path.abspath(subject_root)
    v = verify_frozen(root, n)
    s = load_session(root)
    r = round_obj(s, n)
    if r["status"] != "frozen":
        raise RuntimeError("round %d status=%s，不能提交" % (n, r["status"]))
    if not v["match"]:
        raise RuntimeError("提交前摘要与冻结快照不一致，停止提交")
    r["status"] = "submitting"
    r["submit_started_at"] = now()
    r["pre_submit_match"] = True
    s["phase"] = "submitting"
    save_session(root, s, "submitting", n)
    return True


def attach_raw(subject_root, n, raw, wall_time_seconds, error=None):
    """保存原始返回（脱敏）与实测耗时。error 非空且无返回 → evaluation_unknown（请求可能已发出）。"""
    root = os.path.abspath(subject_root)
    s = load_session(root)
    r = round_obj(s, n)
    rdir = os.path.join(root, r["snapshot_path"])
    raw_path = os.path.join(rdir, "feedback_raw.json")
    os.chmod(rdir, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    write_json(raw_path, {"saved_at": now(), "error": error, "raw": redact(to_jsonable(raw))})
    os.chmod(raw_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    v = verify_frozen(root, n)
    r["post_submit_match"] = v["match"]
    r["submission_verified"] = bool(r.get("pre_submit_match") and v["match"])
    r["wall_time_seconds"] = round(float(wall_time_seconds), 3) if wall_time_seconds is not None else None
    r["eval_error"] = error
    r["raw_path"] = os.path.relpath(raw_path, root)
    r["status"] = "evaluation_unknown" if (error and raw is None) else "raw_saved"
    s["phase"] = "raw_saved" if r["status"] == "raw_saved" else "evaluation_unknown"
    save_session(root, s, "attach_raw", {"round": n, "status": r["status"], "error": error})
    return r["status"]


# ---------------------------------------------------------------- record (normalize + stats)
def match_case(name, ids):
    """平台用例名 → 本地完整 ID。全名 > Class.method > method；多候选为歧义。"""
    name = str(name).strip()
    if name in ids:
        return [name]
    parts = name.split(".")
    key = ".".join(parts[-2:]) if len(parts) >= 2 else name
    return [i for i in ids if i.endswith("." + key)]


def norm_case_map(raw_map, fmt, case_key, bugs_key):
    out = []
    if raw_map is None:
        return None
    if fmt == "dict" and isinstance(raw_map, dict):
        for k, v in raw_map.items():
            out.append((k, [str(x) for x in (v if isinstance(v, list) else [v]) if x is not None]))
    elif fmt == "list" and isinstance(raw_map, list):
        for it in raw_map:
            v = it.get(bugs_key)
            out.append((it.get(case_key), [str(x) for x in (v if isinstance(v, list) else [v]) if x is not None]))
    elif fmt == "bug_dict" and isinstance(raw_map, dict):
        tmp = {}
        for bug, cases in raw_map.items():
            for c in (cases if isinstance(cases, list) else [cases]):
                tmp.setdefault(c, []).append(str(bug))
        out = list(tmp.items())
    else:
        raise SystemExit("case_map 格式与 feedback_map.case_map_format=%s 不符" % fmt)
    return out


def category_stats(hit, before, cat_of, totals, label_mode):
    if cat_of is None:
        return None, sorted(hit)
    stats = {}
    cum = hit | before
    cats = set(totals or {}) | set(c for v in cat_of.values() for c in (v if isinstance(v, list) else [v]))
    unknown = sorted(b for b in cum if b not in cat_of)

    def members(ids, c):
        return sorted(b for b in ids if b in cat_of and c in (cat_of[b] if isinstance(cat_of[b], list) else [cat_of[b]]))
    for c in sorted(cats):
        cur, cu, new = members(hit, c), members(cum, c), members(hit - before, c)
        tot = (totals or {}).get(c)
        st = {"total_bug": tot, "current_hit_bug_ids": cur, "cumulative_hit_bug_ids": cu, "new_bug_ids": new}
        if isinstance(tot, int) and tot > 0 and not unknown:
            st.update(current_remaining=tot - len(cur), cumulative_remaining=tot - len(cu),
                      current_hit_rate=round(len(cur) / float(tot), 4),
                      cumulative_hit_rate=round(len(cu) / float(tot), 4))
        elif isinstance(tot, int) and tot == 0:
            st.update(current_hit_rate="not_applicable", cumulative_hit_rate="not_applicable")
        else:
            st.update(current_remaining=None, cumulative_remaining=None,
                      current_hit_rate=None, cumulative_hit_rate=None)
        stats[c] = st
    return {"label_mode": label_mode, "complete": not unknown, "by_category": stats}, unknown


def cmd_record(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    r = round_obj(s, a.round)
    fmap = read_json(a.map) if a.map else (s["platform_contract"].get("feedback_map"))
    if not fmap:
        emit({"error": "缺少 feedback_map：先依据 subject_api 实际代码确认字段路径"}, 2)
    rawdoc = read_json(os.path.join(root, r["raw_path"])) if r.get("raw_path") else None
    if rawdoc is None:
        emit({"error": "本轮没有 feedback_raw.json"}, 2)
    raw = rawdoc["raw"]
    judge_id = get_path(raw, fmap.get("judge_id_path"))
    if r.get("feedback") is not None:
        if r.get("judge_id") == judge_id:
            emit({"status": "already_recorded", "round": a.round})
        emit({"error": "本轮已记录不同 judge_id，不覆盖历史"}, 2)
    status_val = get_path(raw, fmap.get("status_path"))
    ok_values = [str(x) for x in fmap.get("success_values", [])]
    pending_values = [str(x) for x in fmap.get("pending_values", [])]
    if status_val is None or str(status_val) not in ok_values:
        r["status"] = ("evaluation_unknown" if str(status_val) in pending_values or status_val is None
                       else "evaluation_failed")
        r["judge_id"] = judge_id
        r["platform_status_raw"] = status_val
        s["phase"] = "recorded"
        save_session(root, s, "record", {"round": a.round, "status": r["status"]})
        emit({"round": a.round, "status": r["status"], "platform_status": status_val})
    man = read_json(os.path.join(root, r["snapshot_path"], "manifest.json"))
    ids = man["testcase_ids"]
    cmap = norm_case_map(get_path(raw, fmap.get("case_map_path")), fmap.get("case_map_format", "dict"),
                         fmap.get("case_key", "case"), fmap.get("bugs_key", "bugs"))
    hit_raw = get_path(raw, fmap.get("hit_bug_ids_path")) if fmap.get("hit_bug_ids_path") else None
    if hit_raw is not None:
        hit = set(str(x) for x in (hit_raw if isinstance(hit_raw, list) else [hit_raw]))
        hit_source = "hit_bug_ids_path"
    elif cmap is not None:
        hit = set(b for _, bs in cmap for b in bs)
        hit_source = "union_of_case_map"
    else:
        hit = None
        hit_source = None
    before = cumulative_before(s, a.round)
    per_case, unmatched, ambiguous = {}, [], []
    for name, bugs in cmap or []:
        m = match_case(name, ids)
        if len(m) == 1:
            per_case.setdefault(m[0], set()).update(bugs)
        elif not m:
            unmatched.append(name)
        else:
            ambiguous.append({"platform_name": name, "candidates": m})
    complete = bool(s["platform_contract"].get("mapping_complete")) and not unmatched and not ambiguous
    cls = {"NEW": [], "REPEAT": [], "MISS": [], "UNKNOWN": []}
    for t in ids:
        if t in per_case:
            b = per_case[t]
            if b - before:
                cls["NEW"].append(t)
            elif b:
                cls["REPEAT"].append(t)
            else:
                cls["MISS"].append(t)
        else:
            cls["MISS" if complete else "UNKNOWN"].append(t)
    total = get_path(raw, fmap.get("total_bug_path")) if fmap.get("total_bug_path") else None
    cat_of = get_path(raw, fmap.get("bug_category_path")) if fmap.get("bug_category_path") else None
    cat_totals = get_path(raw, fmap.get("category_totals_path")) if fmap.get("category_totals_path") else None
    if isinstance(cat_of, list):
        cat_of = {str(it.get(fmap.get("bug_id_key", "id"))): it.get(fmap.get("category_key", "type"))
                  for it in cat_of if isinstance(it, dict)}
    cstats, unknown_cat = category_stats(hit or set(), before, cat_of, cat_totals,
                                         fmap.get("category_label_mode", "unconfirmed"))
    r.update({
        "status": "evaluated" if hit is not None else "evaluation_unknown",
        "judge_id": judge_id, "judge_cost_raw": get_path(raw, fmap.get("judge_cost_path")),
        "feedback": {
            "hit_bug_ids": sorted(hit) if hit is not None else None, "hit_source": hit_source,
            "new_bug_ids": sorted(hit - before) if hit is not None else None,
            "mapping_testcase_count": len(cmap) if cmap is not None else None,
            "case_mapping_complete": complete,
            "classifications": cls,
            "classification_counts": {k: len(v) for k, v in cls.items()},
            "case_hits": {k: sorted(v) for k, v in per_case.items()},
            "unmatched_platform_cases": unmatched, "ambiguous_platform_cases": ambiguous,
            "total_bug": total,
        },
        "category_stats": cstats, "unknown_category_hit_bug_ids": unknown_cat if cstats else None,
    })
    if total is not None:
        s["total_bug"] = total
    if r["status"] == "evaluated" and r.get("submission_verified"):
        cum = before | hit
        later = set()
        for x in valid_rounds(s):
            if x["round"] > a.round:
                later |= set(x["feedback"]["hit_bug_ids"])
        s["cumulative_hit_bug_ids"] = sorted(cum | later)
        s["latest_valid_round"] = max(x["round"] for x in valid_rounds(s))
        best = max(valid_rounds(s), key=lambda x: (len(x["feedback"]["hit_bug_ids"]), x["round"]))
        s["best_round"] = best["round"]
    elif r["status"] == "evaluated":
        r["note"] = "提交前后摘要未全部核对一致：不计入有效轮次与累计统计"
    s["phase"] = "recorded"
    save_session(root, s, "record", {"round": a.round, "status": r["status"]})
    emit({"round": a.round, "status": r["status"], "submission_verified": r.get("submission_verified"),
          "hit": len(hit) if hit is not None else None,
          "new": len(hit - before) if hit is not None else None,
          "classification_counts": r["feedback"]["classification_counts"],
          "unmatched": len(unmatched), "ambiguous": len(ambiguous),
          "cumulative": len(s["cumulative_hit_bug_ids"])})


# ---------------------------------------------------------------- decide / summary / finalize
def decide_facts(s):
    run = s["runs"][-1] if s["runs"] else None
    used = [r for r in s["rounds"] if run and r["run_id"] == run["run_id"] and r["status"] in ROUND_SUBMITTED + ("raw_saved",)]
    vr = valid_rounds(s)
    streak = 0
    for r in reversed(vr):
        if r["feedback"]["new_bug_ids"]:
            break
        streak += 1
    hard = []
    last = s["rounds"][-1] if s["rounds"] else None
    if run and len(used) >= run["max_rounds"]:
        hard.append("max_rounds")
    if last and last["status"] in ("evaluation_unknown", "submitting", "raw_saved"):
        hard.append("evaluation_unknown")
    if last and last["status"] == "evaluated" and not last.get("submission_verified"):
        hard.append("submission_unverified")
    total = s.get("total_bug")
    if isinstance(total, int) and total > 0 and len(s.get("cumulative_hit_bug_ids", [])) >= total:
        hard.append("all_known_bugs_hit")
    regression = None
    if vr and s.get("best_round") and vr[-1]["round"] != s["best_round"]:
        best = round_obj(s, s["best_round"])
        lost = sorted(set(best["feedback"]["hit_bug_ids"]) - set(vr[-1]["feedback"]["hit_bug_ids"]))
        if lost:
            regression = {"best_round": s["best_round"], "latest_round": vr[-1]["round"], "lost_bug_ids": lost}
    if hard:
        advice = "stop"
    elif streak >= 2:
        advice = "stop_unless_model_correction"
    elif streak == 1:
        advice = "continue_only_with_new_mechanism_or_rip_repair"
    else:
        advice = "continue_if_new_hypotheses"
    return {"rounds_used_this_run": len(used), "max_rounds": run["max_rounds"] if run else None,
            "valid_rounds_total": len(vr), "no_gain_streak": streak, "hard_stops": hard,
            "regression": regression, "advice": advice,
            "cumulative_hit_count": len(s.get("cumulative_hit_bug_ids", [])), "total_bug": total}


def cmd_decide(a):
    emit(decide_facts(load_session(os.path.abspath(a.subject_root))))


def cmd_work_order(a):
    """由已记录的事实生成交给 e2e-test 的本题反馈摘要（不含原始报告、不含其他题信息）。"""
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    vr = valid_rounds(s)
    facts = decide_facts(s)
    wo = {"created_at": now(), "round_target": len(s["rounds"]) + 1,
          "constraint": a.constraint, "no_gain_streak": facts["no_gain_streak"],
          "regression": facts["regression"], "based_on_round": None}
    if vr:
        last = vr[-1]
        fb = last["feedback"]
        cats = None
        if last.get("category_stats"):
            cats = {c: {"total_bug": v["total_bug"], "current_hit": len(v["current_hit_bug_ids"]),
                        "cumulative_hit": len(v["cumulative_hit_bug_ids"])}
                    for c, v in last["category_stats"]["by_category"].items()}
        wo.update(based_on_round=last["round"], snapshot_path=last["snapshot_path"],
                  classifications=fb["classifications"], case_hits=fb.get("case_hits"),
                  mapping_complete=fb["case_mapping_complete"],
                  unmatched_platform_cases=len(fb["unmatched_platform_cases"]),
                  category_summary=cats, category_complete=(last.get("category_stats") or {}).get("complete"),
                  total_bug=s.get("total_bug"), cumulative_hit_count=len(s["cumulative_hit_bug_ids"]),
                  latest_hit_count=len(fb["hit_bug_ids"]))
    s["work_order"] = wo
    save_session(root, s, "work_order", wo["round_target"])
    emit(wo)


def short_summary(s):
    rows = []
    for r in s["rounds"]:
        fb = r.get("feedback") or {}
        rows.append({"round": r["round"], "run": r["run_id"], "status": r["status"],
                     "verified_submission": r.get("submission_verified"),
                     "collected": r.get("collected_count"), "new_tc": r.get("new_testcase_count"),
                     "hit": len(fb["hit_bug_ids"]) if fb.get("hit_bug_ids") is not None else None,
                     "new_bug": len(fb["new_bug_ids"]) if fb.get("new_bug_ids") is not None else None,
                     "cls": fb.get("classification_counts"), "wall_s": r.get("wall_time_seconds"),
                     "origin": r.get("answer_origin"), "snapshot": r.get("snapshot_path")})
    vr = valid_rounds(s)
    latest = vr[-1] if vr else None
    return {"subject_id": s["subject_id"], "title": s.get("title"), "status": s["status"],
            "stop_reason": s.get("stop_reason"), "total_bug": s.get("total_bug"),
            "latest_valid_round": latest["round"] if latest else None,
            "latest_hit_count": len(latest["feedback"]["hit_bug_ids"]) if latest else None,
            "cumulative_hit_count": len(s.get("cumulative_hit_bug_ids", [])),
            "best_round": s.get("best_round"),
            "latest_category_stats": latest.get("category_stats") if latest else None,
            "final_version": s.get("final_version"), "remaining_gaps": s.get("remaining_gaps"),
            "rounds": rows}


def cmd_summary(a):
    emit(short_summary(load_session(os.path.abspath(a.subject_root))))


def cmd_finalize(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    if not a.stop_reason:
        emit({"error": "stop_reason 必填"}, 2)
    gaps = read_json(a.gaps_file, []) if a.gaps_file else []
    vr = valid_rounds(s)
    final = None
    if vr:
        pick = round_obj(s, s["best_round"]) if a.final == "best" else vr[-1]
        final = {"round": pick["round"], "snapshot_path": pick["snapshot_path"],
                 "files_sha256": pick["files_sha256"],
                 "hit_count": len(pick["feedback"]["hit_bug_ids"]), "selection": a.final}
    s.update(status=a.status, stop_reason=a.stop_reason, stop_detail=a.detail,
             remaining_gaps=gaps, final_version=final, phase="settled")
    if s["runs"] and not s["runs"][-1].get("ended_at"):
        s["runs"][-1].update(ended_at=now(), stop_reason=a.stop_reason)
    save_session(root, s, "finalize", {"status": a.status, "stop_reason": a.stop_reason})
    emit(short_summary(s))


def cmd_lesson(a):
    root = os.path.abspath(a.subject_root)
    s = load_session(root)
    text = " ".join([a.trigger, a.method, a.evidence])
    banned = set(s.get("cumulative_hit_bug_ids", []))
    for r in s["rounds"]:
        man = read_json(os.path.join(root, r["snapshot_path"], "manifest.json")) or {}
        for t in man.get("testcase_ids") or []:
            banned.add(t.rsplit(".", 1)[-1])
    leaked = sorted(b for b in banned if b and re.search(r"(?<![\w])%s(?![\w])" % re.escape(b), text))
    if leaked:
        emit({"error": "候选经验包含本题 BUG ID 或用例名，拒绝写入", "leaked": leaked}, 2)
    rec = {"ts": now(), "subject_id": s["subject_id"], "status": "candidate",
           "trigger": a.trigger, "method": a.method, "evidence": a.evidence,
           "evidence_kind": a.kind, "answer_origin": a.origin}
    os.makedirs(a.experience_root, exist_ok=True)
    with open(os.path.join(a.experience_root, "candidates.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    save_session(root, s, "lesson_candidate", a.trigger[:80])
    emit({"written": True})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def sp(name, fn, *args):
        p = sub.add_parser(name)
        p.add_argument("--subject-root", required=True)
        for spec in args:
            flags, kw = spec
            p.add_argument(*flags, **kw)
        p.set_defaults(fn=fn)
        return p
    sp("preflight", cmd_preflight)
    sp("vault", cmd_vault, (("--vault-root",), {"required": True}))
    sp("restore", cmd_restore, (("--vault-path",), {"required": True}),
       (("--scripts",), {"action": "store_true", "help": "同时恢复 testaw.py/udpunittest.py"}))
    sp("init", cmd_init, (("--subject-id",), {"required": True}), (("--title",), {"default": None}))
    sp("start-run", cmd_start_run, (("--max-rounds",), {"type": int, "required": True}),
       (("--mode",), {"required": True, "choices": ["exercise", "contest"]}),
       (("--profile",), {"default": "auto"}))
    sp("contract", cmd_contract, (("--file",), {"required": True}))
    sp("gate", cmd_gate, (("--allow-same-version",), {"action": "store_true"}),
       (("--dry",), {"action": "store_true"}))
    sp("freeze", cmd_freeze)
    sp("verify-frozen", cmd_verify_frozen, (("--round",), {"type": int, "required": True}))
    sp("record", cmd_record, (("--round",), {"type": int, "required": True}), (("--map",), {"default": None}))
    sp("decide", cmd_decide)
    sp("work-order", cmd_work_order, (("--constraint",), {"default": None}))
    sp("summary", cmd_summary)
    sp("finalize", cmd_finalize,
       (("--status",), {"required": True, "choices": ["completed", "stopped", "blocked", "failed"]}),
       (("--stop-reason",), {"required": True}), (("--detail",), {"default": None}),
       (("--gaps-file",), {"default": None}), (("--final",), {"default": "latest", "choices": ["latest", "best"]}))
    sp("lesson", cmd_lesson, (("--experience-root",), {"required": True}),
       (("--trigger",), {"required": True}), (("--method",), {"required": True}),
       (("--evidence",), {"required": True}),
       (("--kind",), {"default": "single_subject", "choices": ["single_subject", "cross_subject", "blind_test"]}),
       (("--origin",), {"default": "self_authored"}))
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        sys.exit(1)
    a.fn(a)


if __name__ == "__main__":
    main()
