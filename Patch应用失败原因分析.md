# Patch 应用失败原因分析

## 概述

根据代码分析，系统通过检查 `test_output.txt` 文件来判断 patch 是否成功应用。如果 patch 没有成功应用，`patch_successfully_applied` 会被设置为 `False`。

---

## Patch 应用成功的判断标准

**代码位置：** `src/grading.py` - `get_logs_eval()`

Patch 被认为**成功应用**需要同时满足以下条件：

1. ✅ **test_output.txt 文件存在**
2. ✅ **输出中不包含任何错误标记**（见下方错误标记列表）
3. ✅ **输出中包含 "applied patch" 字符串**（不区分大小写）

```python
# 成功条件
return log_parser(content), "applied patch" in raw_content.lower()
```

---

## Patch 应用失败的判断标准

Patch 被认为**失败**如果满足以下任一条件：

### 1. 文件不存在

```python
if not Path(log_fp).exists():
    # likely due to a timeout
    return {}, False
```

**可能原因：**
- 执行超时（timeout）
- 脚本执行过程中崩溃
- 磁盘空间不足
- 权限问题导致无法写入文件

---

### 2. 输出中包含错误标记

**代码位置：** `src/grading.py` - `get_logs_eval()`

如果 `test_output.txt` 中包含以下任何错误标记，patch 被认为失败：

```python
if any([
    APPLY_PATCH_FAIL,      # ">>>>> Patch Apply Failed"
    RESET_FAILED,          # ">>>>> Reset Failed"
    TESTS_ERROR,           # ">>>>> Tests Errored"
    TESTS_TIMEOUT,         # ">>>>> Tests Timed Out"
    "Failed to reset task environment",
]):
    return {}, False
```

---

## 详细的失败原因分析

### 原因 1: `>>>>> Patch Apply Failed` (APPLY_PATCH_FAIL)

**含义：** `git apply` 命令执行失败

**可能的具体原因：**

#### 1.1 Patch 格式错误
- Patch 不是有效的 git diff 格式
- Patch 缺少必要的头部信息（`--- a/` 或 `+++ b/`）
- Patch 的上下文行不匹配

#### 1.2 文件路径问题
- Patch 中指定的文件不存在于目标仓库中
- 文件路径不正确（相对路径问题）
- 文件被删除或重命名

#### 1.3 上下文不匹配（Context Mismatch）
- Patch 生成的代码与当前代码不一致
- 代码已经发生变化（虽然理论上应该在 base_commit）
- 空白字符不匹配（空格 vs Tab）
- 行尾符不匹配（Windows CRLF vs Unix LF）

#### 1.4 Git 状态问题
- 仓库不在干净状态（有未提交的更改）
- Git 配置问题
- 工作目录不在预期的 commit

**代码位置：** `src/exec_spec.py` - `eval_script_list`

```python
apply_patch_commands = [
    f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{patch}\n{HEREDOC_DELIMITER}"
    for patch in patch_list
]
```

**git apply 常见错误：**
- `error: patch failed: file/path:line_number`
- `error: file/path: patch does not apply`
- `error: unrecognized input`
- `error: corrupt patch at line X`

---

### 原因 2: `>>>>> Reset Failed` (RESET_FAILED)

**含义：** 测试后无法重置仓库状态到原始状态

**可能的具体原因：**

#### 2.1 Git 重置失败
- `git checkout {base_commit}` 失败
- `git apply /root/pre_state.patch` 失败
- Git 仓库损坏
- 权限问题

**代码位置：** `src/exec_spec.py` - `eval_script_list`

```python
reset_commands = [
    f"git checkout {base_commit}",
    f"git apply /root/pre_state.patch"
]
```

#### 2.2 预状态 Patch 问题
- `/root/pre_state.patch` 文件不存在或损坏
- 预状态 patch 本身无法应用
- 磁盘空间不足

**代码位置：** `src/exec_spec.py` - `eval_script_list`

```python
patch_base_command = f"git diff HEAD {base_commit} >> /root/pre_state.patch"
```

---

### 原因 3: `>>>>> Tests Errored` (TESTS_ERROR)

**含义：** 测试执行过程中发生错误（不是测试失败，而是执行错误）

**可能的具体原因：**

#### 3.1 测试框架错误
- 测试框架本身崩溃
- 测试导入失败
- 依赖包缺失或版本不匹配

#### 3.2 环境问题
- Python 环境配置错误
- 环境变量缺失
- 路径问题

#### 3.3 代码错误
- 应用 patch 后代码有语法错误
- 导入错误
- 运行时错误（在测试执行前）

#### 3.4 资源问题
- 内存不足
- 文件句柄耗尽
- 网络问题（如果测试需要网络）

---

### 原因 4: `>>>>> Tests Timed Out` (TESTS_TIMEOUT)

**含义：** 测试执行超时

**可能的具体原因：**

#### 4.1 测试执行时间过长
- 测试套件太大
- 测试包含性能测试
- 测试有死循环或无限等待

#### 4.2 超时设置不合理
- 默认超时为 1800 秒（30分钟）
- 某些测试可能需要更长时间

**代码位置：** `src/main.py`

```python
parser.add_argument(
    "--timeout", type=int, default=1_800, 
    help="Timeout (in seconds) for running tests for each instance"
)
```

---

### 原因 5: `Failed to reset task environment`

**含义：** 重置任务环境失败

**可能的具体原因：**
- Git 操作失败
- 文件系统问题
- 权限问题

---

### 原因 6: 输出中缺少 "applied patch" 标记

**代码位置：** `src/grading.py` - `get_logs_eval()`

```python
return log_parser(content), "applied patch" in raw_content.lower()
```

**即使没有错误标记，如果输出中不包含 "applied patch"，patch 也被认为失败。**

**可能的具体原因：**

#### 6.1 Eval 脚本执行不完整
- 脚本在 patch 应用前就失败了
- 脚本执行被中断

#### 6.2 输出格式问题
- 输出被重定向到其他地方
- 输出缓冲区未刷新

---

## 其他可能的问题

### 问题 1: Patch 格式问题（vanilla/fuzzy/custom）

**代码位置：** `src/run_evaluation.py` - `extract_model_patch()`

根据 `patch_types` 参数，patch 可能经过不同的处理：

1. **vanilla**: 移除二进制差异
2. **fuzzy**: 模糊匹配应用
3. **custom**: 自定义 AST 变换

如果 patch 格式转换失败，可能导致应用失败。

---

### 问题 2: 多 Patch 顺序问题

**代码位置：** `src/exec_spec.py` - `eval_script_list`

```python
eval_commands += apply_patch_commands + [test_command] + ...
```

如果 `patch_list` 中包含多个 patch，它们按顺序应用。如果第一个 patch 失败，后续 patch 也会失败。

---

### 问题 3: Base Commit 状态问题

**代码位置：** `src/exec_spec.py` - `eval_script_list`

```python
patch_base_command = f"git diff HEAD {base_commit} >> /root/pre_state.patch"
```

如果仓库当前状态与 `base_commit` 不匹配，可能导致预状态 patch 生成错误。

---

## 调试建议

### 1. 检查 test_output.txt

查看 `run_instance_swt_logs/{run_id}/{patch_id}/{instance_id}/test_output.txt` 或相应的日志目录：

```bash
# 查看完整的测试输出
cat run_instance_swt_logs/.../test_output.txt

# 查找错误标记
grep -E ">>>>>|Failed|error" run_instance_swt_logs/.../test_output.txt

# 查找 git apply 错误
grep -i "git apply\|patch failed\|does not apply" run_instance_swt_logs/.../test_output.txt
```

---

### 2. 检查 eval.sh 脚本

查看实际执行的脚本：

```bash
cat run_instance_swt_logs/.../eval.sh
```

检查：
- Patch 内容是否正确
- Git 命令是否正确
- 路径是否正确

---

### 3. 检查 model_patch.diff

查看实际应用的 patch：

```bash
cat run_instance_swt_logs/.../model_patch.diff
```

检查：
- Patch 格式是否正确
- 文件路径是否存在
- 代码上下文是否匹配

---

### 4. 检查 exec_spec.json

查看执行规格配置：

```bash
cat run_instance_swt_logs/.../exec_spec.json
```

检查：
- base_commit 是否正确
- test_directives 是否正确
- patch_list 是否包含正确的 patch

---

### 5. 手动测试 Patch 应用

可以在 Docker 容器中手动测试：

```bash
# 进入容器
docker exec -it <container_name> bash

# 检查 git 状态
cd /testbed/<repo>
git status
git log --oneline -5

# 尝试手动应用 patch
git apply -v - < /path/to/patch.diff

# 检查应用结果
git status
git diff
```

---

## 常见错误模式总结

| 错误标记 | 主要原因 | 解决方案 |
|---------|---------|---------|
| `>>>>> Patch Apply Failed` | git apply 失败 | 检查 patch 格式、文件路径、上下文匹配 |
| `>>>>> Reset Failed` | 无法重置仓库状态 | 检查 git 状态、预状态 patch |
| `>>>>> Tests Errored` | 测试执行错误 | 检查代码语法、依赖、环境配置 |
| `>>>>> Tests Timed Out` | 测试超时 | 增加 timeout 或优化测试 |
| `Failed to reset task environment` | 环境重置失败 | 检查 git 操作、权限 |
| 文件不存在 | 超时或崩溃 | 检查日志、资源限制 |
| 缺少 "applied patch" | 脚本执行不完整 | 检查 eval.sh、脚本执行流程 |

---

## 预防措施

### 1. Patch 生成时

- 确保 patch 格式正确（标准的 git diff 格式）
- 确保文件路径正确（相对于仓库根目录）
- 确保上下文足够（git diff 默认包含 3 行上下文）

### 2. Patch 提取时

- 正确使用 extract_patches 脚本
- 验证提取的 patch 格式
- 检查二进制文件是否被正确处理

### 3. 执行环境

- 确保仓库在正确的 base_commit
- 确保环境配置正确
- 确保有足够的资源（内存、磁盘、时间）

### 4. 错误处理

- 增加详细的日志记录
- 在关键步骤添加错误检查
- 提供清晰的错误消息

