---
name: loop
description: CTB 取题、调用 e2e-test、版本冻结、真实评测、反馈解析、增量迭代及结果沉淀；由 ctb-exercise / ctb-loop 两个 Command 调用。以证据台账驱动：每一步都可核对、可恢复，并有明确的停止条件。
---

# CTB Loop

## 定位

Loop 只做三件事：**守住证据**（提交的是哪个版本、反馈说了什么）、**守住门禁**（没验证好不提交、结果不确定不重提），以及**管住预算**（无增量就停）。测试设计与脚本修复全部交给 e2e-test，Loop 不重复实现。

确定性操作一律通过台账工具完成，不靠模型手算或手写 JSON：

```text
L = python .opencode/skills/loop/scripts/ctb_ledger.py
```

`session.json` 只由台账写入；`design.json` 只读。

## 输入（来自 Command）

`subject_ids`（数字字符串列表，已去重保序）、`mode`（exercise|contest）、`max_rounds`（本次调用中每题**最多新增**的真实评测次数）、`profile`、`username`（可空）。

- 题号始终按字符串处理，只在已核实需要整数的 API 边界转换；
- `username` 为空时读 `.opencode/testagent.json` 的 `env.user_id`；仍没有则整批 `blocked: configuration_missing`。不硬编码工号，不输出凭据。

## 0. 核实平台接口（每个环境首次运行时做，结论写入契约）

读 `.opencode/skills/loop/api/subject_api.py` 及其依赖（`ilearning.py`、`uniportal.py`），确认以下事实，写成 JSON 后执行 `L contract --subject-root <root> --file <json>`：

| 契约字段 | 要确认的事实 |
| --- | --- |
| `evaluation_call` | 类名、构造参数、`get`/`evaluation` 的真实签名；language 参数取值 |
| `submission_reads` | evaluation 实际读取/打包哪些文件或目录；是否会合并默认模板 |
| `mode_support` | 是否真正支持 contest；依据在代码哪一行 |
| `feedback_map` | 返回值中状态、成功值、未就绪值、judge_id、用例→BUG 映射、总数、分类、judge_cost 的字段路径（台账 `record` 用它规范化） |
| `mapping_complete` | 用例映射是否列出了**全部**已执行用例（包括未命中的）。无法从代码或平台说明确认时保持 `false` |
| `notes` | 轮询/超时行为、提交次数限制等 |

- `api/` 不存在或签名与资料不符 → 停止，`configuration_missing`，不要猜参数；
- 映射路径只能来自代码或一次真实返回，不凭字段名猜测；拿到第一次真实返回后，再核对一次契约；
- 现场若已有 `count_tests.py`、`session_utils.py`，首轮把它们的收集数/统计结果与台账对比，有差异就报告差异，不悄悄二选一；`session.json` 仍只经台账写入，以免出现两个写入者。

## 每题流程

按 `session.phase` 推进；每一步完成后台账会记录事件，中断后从当前 phase 继续（见"恢复"）。

### P1 准备
1. `L preflight --subject-root <root>`，按 `recommended_action` 处理：
   - `reuse`：公开资料齐全，**不调用 get()**；
   - `vault_then_get`：先 `L vault --vault-root .ctb_vault`（放在 answer_root 之外），再调用 get()，然后 `L restore --vault-path <vault> --scripts`，恢复已有脚本与 `.ctb/`（get 拿到的新模板会另存，用于比对）；
   - `get`：首次取题。
2. `L init --subject-id <id> --title <标题>`（幂等，不会重置历史）；不是 Python 题时 `blocked`，不套用 unittest 骨架。
3. mode=contest 且 `mode_support.contest` 没有被确认为 true → 停止，`mode_unsupported`；不降级为 exercise。
4. `L start-run --max-rounds N --mode M --profile P`：本次调用的预算从这里开始计。

### P2 设计（调用 e2e-test）
在当前上下文加载 `.opencode/skills/e2e-test/SKILL.md`，传入 `subject_root, profile, mode, round_target=<历史轮数+1>, work_order`（首轮为空）。等它返回 `delivery`。不创建子 Agent。

### P3 门禁
`L gate`。台账会核对：`delivery.ready`；验证状态和摘要与当前文件一致；执行 ID 与收集 ID 一致且无失败/错误/跳过；重新收集的 ID 与验证记录一致；P0 假设已闭合；`answer_origin` 合法；增量轮存在 `increments[for_round]`；与上一有效轮不是同一版本；历史命中用例未被无理由删除。

- 不通过：把 `blockers` 原样交回 e2e-test 修复一次；第二次仍因同一原因不通过且没有新证据 → `validation_blocked`；
- e2e-test 返回 `no_new_hypothesis` → 停止，`no_new_hypothesis`。

### P4 冻结
`L freeze`：把两个正式文件、design.json、验证日志、真实收集的 ID 清单写入 `.ctb/rounds/rNN/`（只读），并记录 SHA-256。轮号 = 真实评测尝试序号，本地修订不占轮号。

### P5 评测
按契约中核实过的签名填写下面的片段并执行。片段在一个进程内完成提交前核对、计时和提交后核对：

```python
import sys, time
sys.path[:0] = ['.opencode/skills/loop/api', '.opencode/skills/loop/scripts']
import ctb_ledger as L
from subject_api import IlearningSubjectAPI          # 以契约 evaluation_call 为准
root, n = '<subject_root>', <N>
L.mark_submitting(root, n)                          # 当前文件与冻结快照不一致会抛异常，不提交
api = IlearningSubjectAPI(<契约中的构造参数>)
t0, raw, err = time.perf_counter(), None, None
try:
    raw = api.evaluation(<契约中的参数>)
except Exception as e:
    err = repr(e)
print(L.attach_raw(root, n, raw, time.perf_counter() - t0, err))
```

- 评测期间不得修改正式文件；
- `submission_reads` 显示 API 会合并默认模板或读取别的目录时，先解决这个问题，否则提交内容无法与快照对应；
- 抛异常且没有返回 → 本轮记为 `evaluation_unknown`。**不自动重提**，告诉用户需要人工确认平台上的状态。

### P6 记录
`L record --round N`：按 `feedback_map` 规范化原始返回，计算唯一 BUG 集合、新增集合、累计集合、用例分类、分类统计，并做幂等校验（同一 judge_id 重复记录不会重复累计）。输出中出现 `unmatched` / `ambiguous` 时，说明平台用例名与本地 ID 对不上，要在结果中报告，不能当作 MISS。

### P7 决策
`L decide` 给出客观事实：`hard_stops`、`no_gain_streak`（只计**有效**轮次：评测成功、提交前后摘要一致、反馈可解析）、`regression`、`advice`。

| 情况 | 行动 |
| --- | --- |
| `hard_stops` 非空 | 停止，stop_reason 取对应值 |
| `advice=continue_if_new_hypotheses` | 生成 work_order，回到 P2 |
| `advice=continue_only_with_new_mechanism_or_rip_repair`（上一有效轮无新增） | work_order 中写明：本轮增量必须包含新机制格子、R/I/P 修复或模型更正 |
| `advice=stop_unless_model_correction`（连续两个有效轮无新增） | 只有 e2e-test 能给出 `model_corrections` 时才再评测一次，否则停止，`no_gain_and_no_new_model` |
| 上一轮 `evaluation_failed`（平台明确报错） | 不算无收益轮；对照平台错误与本地日志定位原因；只有修复后形成新版本才再次提交（仍计入预算） |
| `regression` 非空 | work_order 带上最佳轮次和丢失的 BUG 令牌，要求 e2e-test 对比两个冻结版本，恢复丢失的判别能力 |

宣布"无收益"之前，先确认：本轮提交已核实、`unmatched/ambiguous` 为 0、反馈不是未就绪状态。不满足就是**过程问题**，不是测试没有收益。

**work_order**：`L work-order --constraint "<上表中的本轮约束>"` 由台账从已记录的事实生成并写入 `session.work_order`，内容只含本题信息：上一有效轮的快照路径、每条用例的分类（NEW/REPEAT/MISS/UNKNOWN）、每条用例命中的 BUG 令牌、映射是否完整、已确认的分类汇总（总数、当前命中、累计命中）、`no_gain_streak`、`regression`。把它的输出原样交给 e2e-test；不传原始评分报告，不传其他题的任何内容。e2e-test 只读该字段。

### P8 结算
1. `L finalize --status <completed|stopped|blocked|failed> --stop-reason <原因> --gaps-file <json> [--final latest|best]`：默认以最近一个有效轮为最终版本；最近轮出现退化时用 `best`，并在报告中说明；
2. 候选经验：`L lesson --experience-root softtest_answer/_ctb_experience --trigger ... --method ... --evidence ...`。台账会拒绝包含本题 BUG ID 或用例名的文本。候选经验**不会**写入 playbook；只有在至少两道结构不同的题上各有独立证据（或经过一次未接触答案的盲测）后，才由人工审定是否并入 `design-playbook.md`；
3. `L summary` 的输出作为返回给 Command 的数据。

## 恢复

| 中断时的 phase | 处理 |
| --- | --- |
| `prepare` / `gate_blocked` | 从 P1/P2 继续 |
| `gate_passed` | 重新执行 `L gate` 后冻结 |
| `frozen` | `L verify-frozen` 一致则进入 P5；不一致说明冻结后文件被改动，重新走 P2–P4（旧快照保留） |
| `submitting` / `evaluation_unknown` | **不重提**；把它视为 evaluation_unknown，请用户确认平台状态 |
| `raw_saved` | 直接 `L record` |
| `recorded` | 进入 P7 |

## 统计口径（由台账计算）

| 指标 | 含义 |
| --- | --- |
| `collected_count` | 冻结版本经 unittest loader 实际收集的 testcase 数 |
| `run_count` | 正确环境验证日志中的实际执行数 |
| `mapping_testcase_count` | 平台反馈中的映射条目数（≠ collected_count） |
| `new_testcase_count` | 本轮冻结 ID 集合 − 上一有效轮冻结 ID 集合 |
| `hit_bug_ids` / `new_bug_ids` | 本轮唯一命中集合 / 本轮 − 此前有效轮累计 |
| `latest_valid_round` / `cumulative_hit_bug_ids` | 当前版本的命中 / 历次有效轮次的并集，两者分开报告 |
| NEW / REPEAT / MISS / UNKNOWN | 命中新 BUG / 只命中旧 BUG / 平台明确未命中 / 无法判断。`mapping_complete=false` 时，未出现在映射中的用例一律记 UNKNOWN |
| `wall_time_seconds` | evaluation 调用的实测耗时；`judge_cost_raw` 原样保存，不解释单位 |
| `category_stats` | 只按平台给出的 BUG→类别映射计算；总数未知为 null；总数为 0 时命中率记"不适用"；存在未分类命中时不计算剩余数和命中率 |
| `answer_origin` | 来自 `design.delivery`；用到参考答案的轮次不计入独立能力统计 |

## 多题

严格串行：一道题结算后才开始下一道。每题结束后只保留 `L summary` 的短结果，不把本题的脚本、BUG ID、用例名带到下一题。单题失败只影响该题；身份/认证失败、公共 API 故障或用户停止则终止整批，剩余题目标记为未执行。

## 返回给 Command 的结果

每题返回：`L summary` 的 JSON + `stop_reason` + 最终快照路径 + 两个脚本路径 + design.json 中 `delivery.summary` 与 `gaps` 的要点。未知值保持未知，不补 0。
