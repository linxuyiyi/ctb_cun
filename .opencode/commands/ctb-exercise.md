---
description: CTB 单题作答；默认一轮，可指定多轮。本入口只校验参数、调用 loop、展示结果。
agent: build
subtask: false
---

# CTB 单题作答

原始参数：`$ARGUMENTS`

用法：

```text
/ctb-exercise <subjectId> [--max-rounds N] [--profile FAST|BALANCED|THOROUGH|auto] [--mode exercise|contest] [--username <工号>]
```

## 参数校验

完整解析原始参数串，不把它当 shell 代码执行。

- 选项支持 `--key value` 与 `--key=value`，可以出现在题号前后；
- 题号必须恰好一个，为纯数字正整数，**按字符串保留**；如果用空格、逗号或分号分出了多个题号，报错并提示改用 `/ctb-loop`，不要只取第一个；
- `--max-rounds`：正整数，默认 1，表示本次最多**新增**的真实评测次数；
- `--profile`：FAST / BALANCED / THOROUGH / auto，默认 auto；
- `--mode`：exercise / contest，默认 exercise；
- `--username`：可选；不填时由 loop 读取配置；
- 未知选项、重复选项、缺少值、非法取值、题号为空或非法：说明具体错误并给出用法，然后停止，不取题、不提交。

## 执行

校验通过后，在当前会话加载 loop 技能（技能工具不可用时读取 `.opencode/skills/loop/SKILL.md` 并照做），传入：

```json
{"subject_ids": ["<题号>"], "mode": "...", "max_rounds": N, "profile": "...", "username": "...或 null"}
```

不创建子任务，不改动当前模型设置，不自行取题、设计、提交或计算统计。

## 展示

只展示 loop 返回的事实，未知值写"未知"，不补 0：

1. 题号、标题、状态、停止原因；
2. 本次实际评测次数；每轮：快照路径、收集数、新增用例数、命中唯一 BUG 数、新增唯一 BUG 数、NEW/REPEAT/MISS/UNKNOWN 数、评测耗时、answer_origin；
3. 当前版本命中数与累计命中数（分开列）、total_bug、当前命中率（仅当总数已知且为正时显示）；
4. 分类统计（仅平台确认的分类；未分类命中单独列出）：

| 类别 | 总数 | 当前命中 | 累计命中 | 本轮新增 | 当前剩余 |
| --- | --- | --- | --- | --- | --- |

5. 脚本路径、design.json、session.json、最终快照路径；
6. e2e-test 交付摘要与剩余 GAP。

正确环境通过不等于平台命中；用过参考答案的轮次要标明 answer_origin。
