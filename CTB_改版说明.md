# CTB e2e-test / loop 改版说明

## 1. 诊断：为什么"堆用例但命中不增"

**已有证据**（来自旧版文件本身，`CTB_Skills_Python38_Java8_v2.zip`）

- 旧版 E2E 指令约 4.9 万字符，含 539 处"不/禁止"类约束句；"可能错误 / 错误实现 / 怎样写错"合计只出现 7 次。指令的重心在合规与记账，而不在"怎样让错误实现失败"；
- 设计主线是技术驱动：覆盖要求 → 覆盖项 → 测试点，再配合 `design_tools.py` 生成 ECP/BVA/DCoT/STT/PCT 组合。产出可以计数的覆盖项，但没有任何步骤检查数据能否让错误实现表现不同、断言能否看到差异；
- 旧 loop 对 `count_tests.py` / `session_utils.py` 的要求写成"修正规则，由 GLM5.2 核对当前代码、完成适配"，也就是说文档要求的能力是否已在代码中实现，没有证据。

**合理假设**（未验证，需用第 5 节的方法确认）

1. *模型缺口*：复杂题（6/46）中有整类业务机制从未被建模，用例数增加只是在已覆盖的机制上加密；
2. *可达失效*：大量用例卡在同一前置或同一拒绝码，没有到达目标逻辑；
3. *激发失效*：用典型值（0、1、单对象、首次操作），错误实现与正确实现输出相同；
4. *传播失效*：只断言返回码或前缀，不观察状态保持和后续行为；
5. *反馈轮漂移*：next_focus 过于抽象，模型退回"补边界、换参数"。

**待验证事项**：拿到 6/46 那道题的冻结脚本后，对它跑 `trace-report`（统计卡在同一拒绝码的用例、从未触发的响应码）和 `lint`（统计丢弃的 AW 返回值），并把命中用例映射到机制，看命中是否集中在少数机制上。这一步能直接区分假设 1–4，耗时很短。

## 2. 新方案：保留什么、去掉什么、重写什么

| 项目 | 处理 | 理由 |
| --- | --- | --- |
| 2 个 Command + 2 个 Skill 的框架、参数与默认值 | 保留 | 需求红线 |
| 设计主线 | **重写**：机制模型 → 故障算子 → 可达-激发-传播三条件 → 判别审计 | 让每条用例都对应一个具体的错误实现 |
| 八种设计技术 | 降级为"合规下限"对照表（playbook §7） | 题目要求仍须满足，但不再作为命中来源 |
| 四类质量 | 保留"有依据才设计"原则，并映射到具体算子 | 防止为不存在的需求发明场景 |
| `design_tools.py` 组合生成器、GraphWalker、vendor wheel | 移除 | 它产出的是覆盖数量，不是判别力；体积约 44MB |
| 新增 `e2e_evidence.py` | 新建 | 真实收集、真实运行、lint、调用轨迹审计、设计一致性检查 |
| 新增 `ctb_ledger.py` | 新建 | session.json 唯一写入入口：门禁、冻结、提交前后核对、反馈规范化、集合统计、停止事实、经验泄漏检查 |
| 旧 loop 中散落的统计/分类规则 | 改为由台账代码计算，文档只描述口径 | 不靠模型手算 |

## 3. 端到端数据契约

```text
Command ──{subject_ids[str], mode, max_rounds, profile, username|null}──▶ loop
loop ──{subject_root, profile, mode, round_target, work_order|null}──▶ e2e-test
e2e-test ──写 design.json（delivery / validation / increments）──▶ loop 只读
loop ──写 session.json（仅经 ctb_ledger.py）──▶ e2e-test 只读 work_order
loop ──L summary JSON──▶ Command 展示
```

| 文件 | 唯一写入者 | 读者 |
| --- | --- | --- |
| `python_framework/testaw.py`、`udpunittest.py` | e2e-test | loop（冻结） |
| `.ctb/design.json`、`.ctb/evidence/` | e2e-test | loop（门禁/冻结） |
| `.ctb/session.json` | loop（经台账） | e2e-test（只读 work_order） |
| `.ctb/rounds/rNN/` | 台账 freeze / attach_raw，之后只读 | 两者 |
| `softtest_answer/_ctb_experience/candidates.jsonl` | 台账 lesson | 人工审定 |

**平台反馈口径**：`platform_contract.feedback_map` 中的字段路径必须来自 `subject_api.py` 代码或一次真实返回；`mapping_complete` 未确认时一律为 false，未映射的用例记 UNKNOWN。

**停止条件**：硬停止为 max_rounds、evaluation_unknown、submission_unverified、all_known_bugs_hit、validation_blocked、no_new_hypothesis、用户停止、配置缺失、mode 不支持。软停止：连续两个**有效**轮无新增时，只有带模型更正才允许再评测一次。无效轮（提交未核实、反馈不可解析）不计入无收益。

## 4. 验收（本仓库内已执行）

`python3 validation/selftest.py`：基于模拟题 `validation/fixture/900001`（mock testtool，进程内模拟 DUT）和模拟平台返回，共 34 项全部通过，覆盖：Python 3.8 语法、真实 unittest 收集、官方入口运行与 `__main__` ID 规范化、lint 查出丢弃的返回值、轨迹审计查出从未触发的响应码、门禁（验证后改脚本、同版本重提、缺少增量说明）、冻结与提交前后摘要核对、凭据脱敏、唯一 BUG 去重、UNKNOWN 与 MISS 的区分、幂等记录、分类统计（总数为 0 时记"不适用"）、基于 ID 集合差的新增用例数、work_order 生成、停止规则、评测未知不重提、经验泄漏拦截。

**未验证**（本机没有真实题目、`subject_api.py` 和 Java 运行时）：真实 JAR 启停、真实平台字段路径、contest 支持、真实命中效果。

## 5. 改版前后的对比方法

1. **防退化（同题回归）**：在接触过的旧题上用新版跑一次，只确认流程正确、命中不低于旧版的**独立首轮**（不能与用过参考答案后的成绩比较）；
2. **泛化能力（主指标）**：选 ≥3 道双方都没接触过具体答案、结构不同的新题，新旧版本各在独立工作区做**首轮**评测，比较当前版本命中数 / total_bug。同时记录收集数，以便看"每条用例的命中"；
3. **诊断指标**（不需要平台、可以先做）：对两版首轮脚本各跑一次 `trace-report` 与 `lint`，比较卡在同一拒绝码的用例比例、从未触发的响应码数、丢弃返回值的调用数、机制覆盖数；
4. 所有比较都只用 `answer_origin=self_authored` 的轮次。

## 6. 遗留 GAP

- 公司现场的 `subject_api.py / ilearning.py / uniportal.py / count_tests.py / session_utils.py / testagent.json` 本次不可见。新台账没有覆盖这些文件名；首次部署时须按 loop §0 核实接口，并与现有脚本的结果对比一次；
- 轨迹钩子需要在 testaw.py 中加入一个默认关闭的函数，部署前请确认平台允许 testaw.py 包含这类代码；
- "每个 BUG 对应一个注入错误的程序版本"是设计时的工作假设，请向平台方或用历史反馈确认；
- 旧版中的 JAR "BUG 标记/加密字符串"处理：新版选择不解码、不用于定位用例（与"不探测隐藏判题实现"的红线保持一致）；如果你认为这属于授权范围，需要单独决定。
