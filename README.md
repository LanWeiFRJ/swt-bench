<div align="center">
    <h1><img height="150px" src="./docs/static/images/color_circle.png" alt="SWT-Bench 🔍🦙"><br>SWT-Bench</h1>

[![arXiv](https://img.shields.io/badge/arXiv-2406.12952-b31b1b.svg)](https://arxiv.org/abs/2406.12952)
[![Build & Test](https://github.com/logic-star-ai/swt-bench/actions/workflows/build.yml/badge.svg)](https://github.com/logic-star-ai/swt-bench/actions/workflows/build.yml)
   <a href="https://www.python.org/">
        <img alt="Build" src="https://img.shields.io/badge/Python-3.9+-1f425f.svg?color=blue">
    </a>
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>


## 👋 概述

SWT-Bench 是一个用于评估大语言模型在测试生成方面表现的基准测试，测试用例来自 GitHub 上的真实软件问题。
给定一个*代码库*和一个*问题*，语言模型的任务是生成一个*复现测试*，该测试在代码库的原始状态下失败，在应用解决该问题的补丁后通过。

> 查看我们的论文了解更多详情：[SWT-Bench: Testing and Validating Real-World Bug-Fixes with Code Agents](https://arxiv.org/abs/2406.12952)

## 🚀 安装设置

SWT-Bench 使用 Docker 进行可复现的评估。
请按照 [Docker 安装指南](https://docs.docker.com/engine/install/) 在您的机器上安装 Docker。
如果您在 Linux 上设置，我们建议同时查看 [安装后步骤](https://docs.docker.com/engine/install/linux-postinstall/)。

最后，要构建 SWT-Bench，请按照以下步骤操作：
```bash
git clone git@github.com:eth-sri/swt-bench.git
cd swt-bench
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

通过运行以下命令测试您的安装：
```bash
python -m src.main \
    --predictions_path gold \
    --max_workers 1 \
    --instance_ids sympy__sympy-20590 \
    --run_id validate-gold
```

## 💽 使用说明

## 生成预测

在运行评估框架之前，您需要为基准测试实例生成模型预测（复现测试补丁）。本指南说明了如何使用内置的预测生成脚本。

### 前置条件

1. **OpenRouter API Key**：生成脚本使用 OpenRouter 访问语言模型。您需要设置 API 密钥：

   ```bash
   # 选项 1：在项目根目录创建 .env 文件
   echo "OPENROUTER_API_KEY=your_api_key_here" > .env
   
   # 选项 2：导出为环境变量
   export OPENROUTER_API_KEY="your_api_key_here"
   ```

2. **配置文件**：在项目根目录创建或编辑 `config.yaml`，结构如下：

   ```yaml
   generation:
     # 数据集配置
     dataset_name: "princeton-nlp/SWE-bench_Lite"  # HuggingFace 数据集名称或本地路径
     split: "test"                                # 要使用的数据集分割
     is_swt: false                                # 是否为 SWT 扩展数据
     filter_swt: false                            # 是否过滤到官方 SWT 子集

     # 模型和 Prompt 配置
     model_name: "anthropic/claude-sonnet-4.5"    # OpenRouter 模型名称
     prompt_path: "prompts/generate_patch_prompt.txt"  # Prompt 模板路径

     # 输出配置
     output_dir: "predictions"                    # 预测文件的输出目录

     # 可选：指定要为其生成预测的实例 ID
     # 留空或省略则为该分割中的所有实例生成预测
     # instance_ids:
     #   - "astropy__astropy-12907"
     #   - "sympy__sympy-20590"
   ```

### 运行生成脚本

使用以下命令启动预测生成：

```bash
python -m src.generate_predictions
```

脚本将执行以下操作：
1. 从 `config.yaml` 加载配置
2. 从 HuggingFace 加载数据集
3. 对每个实例，调用 OpenRouter API 生成复现测试补丁
4. 将结果保存到 `predictions/<model_name>.<dataset>.<split>.<timestamp>.jsonl`
5. 将日志同时写入终端和 `logs/generate_predictions_YYYYMMDD-HHMMSS.log`

### 高级选项

- **自定义配置路径**：使用 `SWT_CONFIG_PATH` 环境变量指定不同的配置文件：
  ```bash
  SWT_CONFIG_PATH=/path/to/your/config.yaml python -m src.generate_predictions
  ```

- **输出格式**：生成的预测以 JSONL 格式保存，每行包含：
  ```json
  {
    "instance_id": "<instance_id>",
    "model_name_or_path": "<model_name>",
    "model_patch": "<git_patch_content>",
    "full_output": "<optional_complete_model_output>"
  }
  ```

### 后续步骤

生成预测后，您可以使用输出的 JSONL 文件运行评估框架（请参阅下面的"运行评估"部分）。脚本会自动清理 LLM 输出中的 markdown 代码块标记，以确保生成有效的 git 补丁。

## 运行评估

> [!WARNING]
> 在 SWT-Bench 上运行快速评估可能消耗大量资源
> 我们建议在具有至少 120GB 可用存储空间、16GB RAM 和 8 个 CPU 核心的 `x86_64` 机器上运行评估框架。
> 您可能需要尝试 `--max_workers` 参数来找到适合您机器的最佳工作线程数，但我们建议使用少于 `min(0.75 * os.cpu_count(), 24)` 的值。
>
> 如果使用 Docker Desktop 运行，请确保增加虚拟磁盘空间至约 120GB 可用空间，并根据 Docker 可用的 CPU 数量设置 max_workers 以与上述建议保持一致。
>
> 对 `arm64` 机器的支持是实验性的。

使用评估框架在 SWT-Bench Lite 上评估模型预测，运行以下命令：
```bash
python -m src.main \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path <path_to_predictions> \
    --filter_swt \
    --max_workers <num_workers> \
    --run_id <run_id>
    # 使用 --predictions_path 'gold' 来验证 gold 补丁
    # 使用 --run_id 来命名评估运行
    # 使用 --exec_mode reproduction_script --reproduction_script_name <script_name> 以在复现脚本模式下运行（见下文）
```

此命令将在当前目录中生成 docker 构建日志（`image_build_logs`）和评估日志（`run_instance_swt_logs`）。
最终评估结果将存储在 `evaluation_results` 目录中。

### 单元测试模式 vs. 复现脚本模式

默认情况下，SWT-Bench 在单元测试模式下运行，其中模型预测被视为要集成到现有测试套件中的单元测试。评估框架运行测试套件的修改部分并报告更改以计算成功率。成功的补丁添加一个从通过到失败的测试，而不会导致现有测试失败。

在更简单的复现脚本模式下，模型预测被视为复现问题的独立脚本。评估框架在代码库上运行脚本，并根据脚本的退出代码确定成功：0 表示通过，1 表示失败。在此模式下不执行测试套件。


## 报告结果

为了评估单次运行的结果，我们提供了一个简单的脚本来评估单次评估运行。
传入您的评估路径，包括 run_id 和模型，以获得简单的表格概览。
例如，要复现论文表 2 和表 3 中 SWE-Agent 的结果，运行以下命令：

```bash
python -m src.report run_instance_swt_logs/swea__gpt-4-1106-preview/gpt4__SWE-bench_Lite__default_test_demo3__t-0.00__p-0.95__c-3.00__install-1 --dataset lite
# |------------------------------------|--------------------------|
# | Method                             | swea__gpt-4-1106-preview |
# | Applicability (W)                  | 87.31884057971014        |
# | Success Rate (S)                   | 15.942028985507246       |
# | F->X                               | 48.18840579710145        |
# | F->P                               | 16.666666666666668       |
# | P->P                               | 9.782608695652174        |
# | Coverage Delta (Δᵃˡˡ)              | 26.488815129800212       |
# | Coverage Delta Resolved (Δᔆ)       | 64.69774543638181        |
# | Coverage Delta Unresolved (Δⁿᵒᵗ ᔆ) | 19.14736127176707        |
```

为了查看覆盖率增量报告，您需要在同一评估路径中包含 gold 评估，即从下面的下载部分将 golden 结果下载到 `run_instance_swt_logs` 中。

### 提交结果到排行榜

我们在[排行榜](https://swtbench.com)上列出了 SWT-Bench Lite 和 Verified 的顶级方法。如果您希望将结果包含在内，请[发送电子邮件至 submit@swtbench.com](mailto:submit@swtbench.com?subject=SWT-Bench%20Submission&body=Hi%20there%2C%0A%0ASWT-Bench%20is%20great%21%20We%20want%20to%20submit%20our%20agent%20evaluation%20to%20the%20leaderboard.%0A%0APlease%20find%20attached%201%29%20the%20predictions%20of%20our%20cool%20agent%20as%20jsonl%20zip%2C%202%29%20the%20resulting%20evaluation%20report%2C%20and%203%29%20a%20link%20to%20the%20project%20and%20inference%20traces%3A)，包含以下内容：

- 您的方法名称
- 您方法的推理结果，作为用于运行评估的 JSONL。JSONL 应该为 SWT-Bench Lite 或 Verified 中的每个实例每行包含一个预测，每个预测包含以下字段
  - `instance_id` SWT-Bench Lite 或 Verified 中实例的名称
  - `model_name_or_path` 您的模型/方法的名称
  - `model_patch` 要应用于仓库的 git 补丁
  - `full_output` _（可选）_ 您的模型针对给定任务的完整输出
- 您本地确定的性能
- 您项目主页的链接和您方法的跟踪记录（以验证预测的合法性）
- 如果您希望通过我们独立验证结果，请描述如何复现您的运行

我们将在我们的 Docker 化环境中独立运行您的预测以验证您的分数，联系您确认您的结果并协调发布。为确保跟踪记录的可访问性，我们保留在我们的服务器上托管您的预测的权利。

> 排行榜的包含将在尽力而为的基础上进行，但我们不能保证包含或及时处理您的请求。



## ⬇️ 下载

### 数据集

SWT-Bench、SWT-Bench-Lite 和 SWT-Bench Verified 数据集已在 huggingface 上公开发布，可以使用以下链接访问。它们已经包含通过 BM25 检索的 27k token 限制上下文。

| Prompt 格式 | SWT-Bench                                                                     | SWT-Bench Lite                                                                     | SWT-Bench Verified                                                                     |
|---------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| ZeroShotBase  | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_bm25_27k_zsb/) | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_Lite_bm25_27k_zsb/) | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_Verified_bm25_27k_zsb/) |             
| ZeroShotPlus  | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_bm25_27k_zsp/) | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_Lite_bm25_27k_zsp/) | [下载](https://huggingface.co/datasets/nmuendler/SWT-Bench_Verified_bm25_27k_zsp/) |             

### 评估结果

我们提供运行代码代理的完整跟踪、每种方法和设置的预测补丁以及评估框架的日志。

| 工件          | 单个文件                                                            | ZIP                                                                                |
|-------------------|---------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| Agent 跟踪      | [下载](https://files.sri.inf.ethz.ch/swt-bench/agent_traces//) | [下载](https://files.sri.inf.ethz.ch/swt-bench/agent_traces.zip) |             
| 预测补丁 | [下载](https://files.sri.inf.ethz.ch/swt-bench/inference_output/) | [下载 (Lite)](https://files.sri.inf.ethz.ch/swt-bench/inference_output-lite.zip) [下载 (Verified)](https://files.sri.inf.ethz.ch/swt-bench/inference_output-verified.zip) |
| 评估框架日志 | [下载](https://files.sri.inf.ethz.ch/swt-bench/run_instance_swt_logs) | [下载 (Lite)](https://files.sri.inf.ethz.ch/swt-bench/run_instance_swt_logs-lite.zip) [下载 (Verified)](https://files.sri.inf.ethz.ch/swt-bench/run_instance_swt_logs-verified.zip)  |

每种方法解决的实例的完整列表可以在[这里](https://files.sri.inf.ethz.ch/swt-bench/resolved_per_approach.json)找到。

对于 OpenHands 的评估，我们自动丢弃所有顶级文件以删除代理生成的过时复现脚本。
此外，为了在正确的环境中评估代理，我们丢弃对 `setup.py`、`pyproject.toml` 和 `requirements.txt` 文件的更改，因为它们由测试设置更改并与重复评估冲突。
要找到 OpenHands 使用的确切设置，请查看 [`feat/CI`](https://github.com/logic-star-ai/swt-bench/tree/feat/CI) 分支。
AEGIS 在复现脚本模式下进行评估。

作为参考，我们的 gold 验证运行结果如下（适用性、成功率、F->X 和 F->P 率均为 100%）。

| 指标                             | Lite  | Verified | Full  |  
|------------------------------------|-------|----------|-------|
| # 实例                        | 276   | 433      | 2294  |
| P->P (Gold)                        | 10.86 | 15.01    | 17.65 |
| Coverage Delta (Δᵃˡˡ)  (Gold)      | 71.84 | 69.12    | 65.13 |



## 🏗 构建 SWT-Bench 和零样本推理

要重新创建 SWT-Bench 数据集或创建您自己的版本，并在该数据集上运行论文中的零样本方法，请按照以下步骤操作。
为避免重复，我们重用了部分 SWE-Bench 工具。
首先，创建 SWE-Bench 风格的数据集，例如使用 [SWE-Bench 数据集收集脚本](https://github.com/princeton-nlp/SWE-bench/tree/main/swebench/collect)。
如果您想添加 BM-25 检索的文档，可以使用 [SWE-Bench BM-25 检索脚本 `bm25_retrieval.py`](https://github.com/princeton-nlp/SWE-bench/tree/main/swebench/inference/make_datasets) - 请确保将 [`include_tests` 设置为 `True`](https://github.com/princeton-nlp/SWE-bench/blob/d99c1c45880375bdca90b2ffd2627576c886a1b2/swebench/inference/make_datasets/bm25_retrieval.py#L188C42-L188C55) 以确保测试文件包含在结果中。

最后，运行 [dataset/swt_bench.py](dataset/swt_bench.py) 将 SWE-Bench 风格的数据集转换为 SWT-Bench 数据集。
例如，如果您的 SWE-Bench 数据集在 `datasets/swe_bench` 中，运行以下命令。

```bash
python3 dataset/swt_bench.py --dataset_path datasets/swe_bench --output_path dataset/swt_bench_zsb --mode base
python3 dataset/swt_bench.py --dataset_path datasets/swe_bench --output_path dataset/swt_bench_zsp --mode plus
```

这些命令将创建论文中 Zero-Shot Base 和 Zero-Shot Plus 方法的数据集。
然后您可以使用 [SWE-Bench 推理工具](https://github.com/princeton-nlp/SWE-bench/tree/main/swebench/inference) 生成模型推理文件。

## 💫 贡献

我们非常希望听到更广泛的 NLP、机器学习和软件工程研究社区的意见，并欢迎任何贡献、拉取请求或问题！
为此，请提交新的拉取请求或问题。我们一定会尽快跟进！

联系人：[Niels Mündler](https://www.sri.inf.ethz.ch/people/niels) 和 [Mark Niklas Müller](https://www.sri.inf.ethz.ch/people/mark) (Email: {niels.muendler, mark.mueller}@inf.ethz.ch)。

此仓库基于 [SWE-Bench 评估框架](https://github.com/princeton-nlp/SWE-bench)，我们要感谢所有贡献者。

## ✍️ 引用

如果您觉得我们的工作有帮助，请使用以下引用。
```bib
@inproceedings{
  mundler2024swtbench,
  title={{SWT}-Bench: Testing and Validating Real-World Bug-Fixes with Code Agents},
  author={Niels M{\"u}ndler and Mark Niklas Mueller and Jingxuan He and Martin Vechev},
  booktitle={The Thirty-eighth Annual Conference on Neural Information Processing Systems},
  year={2024},
  url={https://openreview.net/forum?id=9Y8zUO11EQ}
}
```

还请考虑引用 SWE-bench，它启发了我们的工作并构成了此代码库的基础。
```bib
@inproceedings{
    jimenez2024swebench,
    title={{SWE}-bench: Can Language Models Resolve Real-world Github Issues?},
    author={Carlos E Jimenez and John Yang and Alexander Wettig and Shunyu Yao and Kexin Pei and Ofir Press and Karthik R Narasimhan},
    booktitle={The Twelfth International Conference on Learning Representations},
    year={2024},
    url={https://openreview.net/forum?id=VTF8yNQM66}
}
```

## 🪪 许可证
MIT。请查看 `LICENSE.md`。
