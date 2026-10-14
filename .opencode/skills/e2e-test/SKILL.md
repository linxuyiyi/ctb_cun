---
name: e2e-test
description: CTB 单题端到端测试设计、脚本实现、正确环境验证及增量修复；不负责取题和平台评测。以"可达-激发-传播"判别条件驱动设计，产出可验证的 testaw.py / udpunittest.py 与 .ctb/design.json。
---

# CTB E2E Test

## 核心思路

隐藏 BUG 只有在 testcase **走到**错误逻辑（可达）、输入让错误实现**表现不同**（激发）、差异被断言**观察到**（传播）时才会被判出。用例很多却不命中，几乎总是这三个条件之一系统性失效，或者某些业务机制根本没有被建模。所以本 Skill 的工作单位不是"测试点清单"，而是：

> 机制元素 → 故障假设 → 合法可达的故事 → 足够深的观察 → 当前版本真实验证的 testcase

方法细节见 `references/design-playbook.md`（下文用 §n 指代其章节），框架约定与验证工具见 `references/ilearning-framework.md`。

## 输入、输出与边界

**输入**（由 loop 提供）：`subject_root`、`profile`（FAST/BALANCED/THOROUGH/auto）、`mode`、`round_target`（下一次评测的轮号）、`work_order`（首轮为空；增量轮包含反馈摘要，见"增量模式"）。

**输出**：
- `python_framework/testaw.py`、`python_framework/udpunittest.py`：唯二的正式交付文件；
- `.ctb/design.json`：本 Skill 是**唯一写入者**；
- `.ctb/evidence/<标签>/`：验证日志、`validation.json`、轨迹；
- `.ctb/tmp/`：探测与诊断（不属于交付）。

**禁止**：
- 调用 `subject_api.get()/evaluation()`；写 `session.json`；修改 testtool.py、timeout.py、tp000.jar、规格；
- 用隐藏答案、其他题的具体答案、BUG ID 或分类去猜缺陷内容；
- 通过删断言、放宽匹配、吞异常、伪造日志让测试通过；
- 自建 socket/UDP 客户端；用 testcase 依赖其他 testcase 的执行顺序。

## 工作流

每一步都在 design.json 中留下产出。重复接手同一题时，先读已有 design.json，只做变化的部分。

### S1 契约卡（§2 + 框架文档 §1）
- **触发**：首次接题，或规格/模板摘要变化（`spec_sha256` 与记录不同）。
- **做**：读规格、模板、AW、testtool；整理命令表、观察通道、复位、限制、`oracle_policy`、歧义点。
- **产出**：`contract`、`spec_sha256`。
- **自检**：每个命令都有完整响应列表和出处；"不考某接口"与"可否用它观察"已区分。
- **停**：入口、启停或命令格式无法从公开资料确认，且具体阻塞了用例时，只做一次最小探测（框架文档 §8）；仍不清楚则记 GAP，返回 `blocked`。

### S2 机制模型（§3，需要时加 §11）
- **触发**：S1 完成后；增量轮发现模型缺口时。
- **做**：按八类来源列出机制卡；完成交叉核对 (a)–(d)；JAR 可读时做 §11 的定点读取。
- **产出**：`mechanisms`、`reference_analysis`；题目规定的技术写入 `required_techniques`（原文出处、档位、覆盖单元）。
- **自检**：规格中的每个响应码都对应到某个守卫；CRUD 矩阵中没有只写不读或只读不写的变量（或已写明理由）。
- **停**：不在这一步写用例。

### S3 故障假设（§4 §5，规定技术 §7，质量目标 §8）
- **做**：对每个机制元素过一遍故障算子，写出 `hypotheses[]`：`target / operator / fault / reach / infect / propagate / priority`；`infect` 中记录两错检验（§5.1）的结论。
- **广度优先**：先保证每个机制的核心效果和每个守卫都有 P0 假设，再加交互类算子，最后补规定技术的剩余单元。
- **自检**：`check-design` 的机制×算子矩阵中没有空白机制；规定技术的每个单元都能指向某个假设或用例。

### S4 判别用例（§6 + 框架文档 §3–§5）
- **做**：把假设组装成故事型 testcase：构造（每步断言）→ 激发 → 按观察阶梯观察 → 必要的后续操作。在 testaw.py 中接入默认关闭的轨迹钩子（框架文档 §4）。每条用例都登记到 `tests{}`。
- **分批**：先写一批覆盖各机制 P0 的用例并跑通框架（S5），再补其余，避免一个框架问题拖垮整批。
- **自检**：`lint` 无 error；`unobserved_aw_call` 逐条消除或说明。

### S5 真实验证（框架文档 §6）
- **做**：`e2e_evidence.py run --trace`；把 `validation.json` 的关键字段写入 `design.validation`（`status/files_sha256/collected_count/run_count/testcase_ids/id_match/summary/log_paths/command`）。
- **自检**：`status=passed`，没有任何 blocker。用例在正确环境失败时按框架文档 §7 定位。
- **停**：同一失败两次都没有新证据 → 记录后返回 `blocked`。正确环境通过只说明脚本与正确实现兼容，不说明能判出 BUG。

### S6 判别审计（§9）
- **做**：`trace-report`（`--expected-codes` 填规格中的全部响应码）、`lint`、`check-design`，按 A–F 逐项处理。审计导致脚本变化时回到 S5 完整重跑。
- **产出**：`audit`（每项结论 + 工具输出位置）、`gaps`（每条写明是什么、为什么做不到、依据、下一步）。

### S7 交付
- **产出** `delivery`：`ready`、`blockers`、`answer_origin`（self_authored / reference_answer / mixed / unconfirmed；只用公开规格、本题正确 JAR 和本手册时为 self_authored）、`summary`（≤10 行：机制数、假设数及 P0 完成情况、收集/执行数、规定技术完成情况、主要 GAP）。
- `ready=true` 的条件：S5 通过且摘要与当前文件一致、P0 假设全部 `verified` 或有理由的 `gap`、S6 中 A–F 无未处理项。否则 `ready=false` 并列出 blockers；不要为了提交而勉强设为 true。
- 返回给 loop：`delivery` 原文和 `validation.status`，不复述设计细节。

## 增量模式

loop 的 `work_order` 包含：上一轮的冻结快照路径、每条 testcase 的 NEW/REPEAT/MISS/UNKNOWN 分类、每条用例命中的 BUG 令牌（不透明，只用来判断是否命中同一 BUG）、已确认的分类汇总、loop 观察到的事实（例如连续无新增的轮数）。

1. 按 §10 做命中热图和 R/I/P 分诊；优先级：模型缺口 > 未命中机制的 R/I/P 修复 > 已命中机制的交互类算子；
2. 已有用例默认全部保留。曾经命中的用例如需删除或改变意图，写入 `increments[].removed_tests`（含理由）；
3. 写 `increments[]`：`{for_round, input_focus, new_hypotheses, repaired_tests, model_corrections, removed_tests, why_old_insufficient}`；
4. 重新执行 S5–S7（S6 只针对改动部分，但 S5 必须完整运行）；
5. 拿不出合格增量（§10）时，`delivery.ready=false`，`blockers=["no_new_hypothesis"]`，并在 summary 中写明已检查的范围。

## Profile

profile 只调整广度和深度，不降低规定技术的档位、断言要求和框架要求。

| profile | 假设范围 |
| --- | --- |
| FAST | 所有机制的 P0 假设 + 规定技术的单元 |
| BALANCED | FAST + 存在相应结构的交互类算子（`G-SCOPE/E-SCOPE/E-ACCUM/L-STALE/S-TRANS/R-LEAK`） |
| THOROUGH | BALANCED + 跨机制业务轨迹、`G-ORDER`（规格定义了优先级时）、`T-TIME` 细粒度、§11 的覆盖诊断 |
| auto | 机制 ≤3 且无状态机/时间/多对象 → FAST；否则 BALANCED；规格包含多实体、状态机、时间推进中至少两类 → THOROUGH。结论与理由写入 `profile` |

默认单 Agent 执行，不因 profile 拆分子 Agent。

## design.json 结构

```json
{
  "schema": "ctb.design/v2",
  "subject_id": "题号字符串",
  "spec_sha256": null,
  "profile": "BALANCED（auto：3 类有状态实体 + 状态机）",
  "oracle_policy": {"precedence": "spec_default", "source": null},
  "contract": {"framework": {}, "commands": [], "observers": [], "reset": null, "limits": {}, "ambiguities": []},
  "mechanisms": [],
  "required_techniques": [
    {"id": "RT1", "name": "原文名称", "level": "原文档位", "source": "规格出处",
     "definition": "confirmed|unconfirmed", "units": [], "evidence_tests": [], "status": "open|met|gap"}
  ],
  "quality_applicability": {"功能": {"status": "applicable", "basis": "..."}},
  "hypotheses": [
    {"id": "H01", "target": "M1.G2", "operator": "G-BND", "priority": "P0",
     "fault": "可能的错误实现", "reach": "合法前置", "infect": "区分值 + 两错检验结论",
     "propagate": "观察级别与内容", "basis": "public_spec|reference_observed",
     "tests": ["udpunittest.MyTestCase1.test_05_xxx"], "status": "planned|implemented|verified|gap",
     "gap_reason": null, "origin_round": 1}
  ],
  "tests": {"udpunittest.MyTestCase1.test_05_xxx": {"hypotheses": ["H01"], "mechanisms": ["M1"]}},
  "reference_analysis": {"status": "not_run|done|partial|unavailable", "read": [], "findings": [], "uncertain": []},
  "audit": {},
  "gaps": [],
  "validation": {"status": "not_run"},
  "increments": [],
  "delivery": {"ready": false, "blockers": [], "answer_origin": "unconfirmed", "summary": null}
}
```

未知填 `null`，不填 0；`verified` 只用于当前验证版本中真实收集到的用例；没有真实运行就保持 `validation.status=not_run`。
