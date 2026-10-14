# ilearning Python 框架约定与真实验证

只讲框架契约和验证方法，不放业务设计内容。**一切以当前题目的实际模板为准**；下面的骨架是常见形态，与模板冲突时以模板为准。

## 1. 接题先读模板

依次读：`认证考试Python自动化测试框架指导.docx`（能读则读）、`udpunittest.py`、`testaw.py`、`testtool.py`、`timeout.py`。确认并写入 `design.json.contract.framework`：

- 运行入口与工作目录（常见：cwd=`python_framework`，`python udpunittest.py`）；
- DUT 启停方式（常见：`setUpClass` 调用 `tool.start_dut()`）以及 JAR 路径如何构造；
- 打点约定：`case_start` / `case_finished` / `case_failed` 的调用位置；
- 断言约定：模板通常重写 `assertTrue/assertFalse` 以调用 `case_failed`，这时**只能用这两个断言**；
- 超时装饰器与默认超时；
- 复位方式（常见：tearDown 中调用 init）；
- 日志位置与 runner 的 verbosity（逐条核对 ID 需要 verbosity=2）。

只读文件：`testtool.py`、`timeout.py`、`tp000.jar`、规格、指导文档。不修改、不替换通信层、不自建 socket/UDP 客户端。

## 2. 常见骨架（示例，以实际模板为准）

```python
class MyTestCase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tool.start_dut()

    @classmethod
    def tearDownClass(cls):
        tool.stop_dut()

    def setUp(self):
        tool.case_start(self.id())
        testaw.trace_case(self.id())      # 可选轨迹钩子，见 §4

    def tearDown(self):
        try:
            tool.case_finished(self.id())
        finally:
            testaw.init()

    def assertTrue(self, expr, msg=None):
        if not expr:
            tool.case_failed(self.id())
        super(MyTestCase1, self).assertTrue(expr, msg)

    def assertFalse(self, expr, msg=None):
        if expr:
            tool.case_failed(self.id())
        super(MyTestCase1, self).assertFalse(expr, msg)

    @timeout(global_default_timeout)
    def test_01_xxx(self):
        ...
```

- setUp 失败时 unittest 不会调用 tearDown；出现时要定位前置/环境问题，不能当作普通失败放过；
- 只管理本框架启动的 DUT 进程；端口冲突先确认占用者，不随意结束其他 Java 进程。

## 3. AW 规则（testaw.py）

- 一个公开命令对应一个薄封装：按规格拼命令，**原样返回响应**；
- AW 内不断言、不偷偷 init/login、不为了让用例成功去修改目标状态；
- 查询类 AW 不截断、不模糊化返回内容；解析辅助函数（如把查询结果拆成字段）可以有，但原始响应必须仍可取得；
- 所有命令都经过同一个内部发送函数，便于接入轨迹钩子。

## 4. 可选轨迹钩子

判别审计（playbook §9 A/D）需要知道每条用例实际发出了哪些命令、收到哪些响应。在 testaw.py 的内部发送函数中加入一个**默认关闭**的钩子：环境变量 `CTB_TRACE_FILE` 不存在时完全不生效，因此不影响平台运行。

```python
import json, os
_TRACE_FILE = os.environ.get("CTB_TRACE_FILE")
_CASE = [None]

def trace_case(case_id):
    _CASE[0] = case_id

def _send(cmd):
    resp = <模板原有的发送调用>(cmd)
    if _TRACE_FILE:
        with open(_TRACE_FILE, "a") as f:
            f.write(json.dumps({"case": _CASE[0], "cmd": cmd, "resp": resp}) + "\n")
    return resp
```

如果模板或题目规定不允许在 testaw.py 中加入这类代码，就不加钩子，审计 A/D 改为人工逐条核对日志，并在 `audit` 中注明。

## 5. 用例编写约定

- 命名：`test_序号_功能描述`，序号连续；同一类中不得重名（后定义的会覆盖前者，收集数会变少）；不同类之间也尽量不同名，因为平台若只给方法名，映射会有歧义；
- 每个 AW 返回值都要断言，或者用于后续断言（`lint` 会检查）；
- 不用 `assertEqual` 等其他断言、不用原生 `assert`、不吞异常、不 skip/expectedFailure；
- 循环只在同一测试意图内使用，失败时要能定位到具体输入（msg 里带上参数）；
- 每条用例都带超时装饰器。

## 6. 真实验证（证据工具）

工具：`.opencode/skills/e2e-test/scripts/e2e_evidence.py`（仅标准库，兼容 Python 3.8）。`<fw>` 为题目的 `python_framework` 绝对路径，`<root>` 为题目根目录。

```text
python e2e_evidence.py collect      --framework <fw>
python e2e_evidence.py run          --framework <fw> --out-dir <root>/.ctb/evidence/<标签> [--trace] [--entry udpunittest.py]
python e2e_evidence.py lint         --framework <fw>
python e2e_evidence.py trace-report --trace <out-dir>/trace.jsonl --ok-codes E000 --expected-codes <规格中的全部响应码>
python e2e_evidence.py check-design --design <root>/.ctb/design.json --framework <fw>
```

- `collect`：在 `<fw>` 下用 unittest loader 真实收集 testcase ID（不是数 `def test_`），同时报告 loader 错误、跨类同名方法、import 时是否有输出、`__main__` 是否自定义了 suite；
- `run`：先记录两个正式文件的 SHA-256，再收集，然后以实际入口运行，只采信本次新生成的日志，解析逐条结果和汇总；把 `__main__.` 前缀规范化为模块名后，核对执行 ID 与收集 ID；运行后再算一次摘要，确认运行期间文件没有变化。结果写入 `<out-dir>/validation.json`。只有 `status=passed` 才算验证通过，`blockers` 会列出所有不通过的原因（失败、错误、跳过、数量不一致、ID 不一致、缺少逐条记录、超时、文件被改动）；
- 正式验证不加 `--trace` 也可以；加了 `--trace` 时同一次运行同时产出轨迹。若 testaw 没有接入钩子，会报 `trace_requested_but_not_written`；
- 运行入口或模块名与默认值不同时，用 `--entry` / `--module` 指定，并在 `contract.framework` 中记录。

**口径**：`collected_count` 是 loader 实际收集数；`run_count` 是本次日志中的 `Ran N`；两者都不等于平台映射条目数。

**失效规则**：验证之后只要修改过任一正式文件，之前的验证就失效（摘要会不一致，loop 的门禁会拦下）。修改共享 AW、setUp/tearDown 或断言方法后必须完整重跑。

## 7. 失败定位顺序

公开规格 → 前置状态（轨迹中是否早于目标步骤就被拒绝）→ AW 拼接的命令与原始响应 → 断言 → 复位/环境。修正期望必须引用规格依据或 `oracle_policy` 允许的实测依据；不得通过删断言、放宽匹配或吞异常来"修复"。

同一失败重复出现且没有新证据时，停下来定位，不要重复执行同一个命令。

## 8. 临时文件

接口探测和诊断脚本只放 `<root>/.ctb/tmp/`。探测时把 `<fw>` 加入 `sys.path` 并切换到该目录后导入真实的 testaw/testtool，不复制框架。同一个问题只保留一份探测脚本；结论写入 `contract`，不再需要的探测文件自行清理。
