import datetime
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests
import yaml
from dotenv import load_dotenv
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# 获取项目根目录（脚本所在目录的父目录）
PROJECT_ROOT = Path(__file__).parent.parent

# 将项目根目录添加到 Python 路径，以便正确导入 src 模块
# 项目中的模块使用绝对导入 (from src.xxx import ...)，需要从项目根目录导入
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入 dataset 模块
from src.dataset import load_swebench_dataset  # type: ignore[import-untyped]


def load_config(config_path: Path) -> Dict[str, Any]:
    """
    从 YAML 配置文件中加载生成参数。

    期望结构示例:
    generation:
      dataset_name: princeton-nlp/SWE-bench_Lite
      split: test
      is_swt: false
      filter_swt: false
      model_name: openrouter/auto
      output_dir: predictions
      instance_ids: []   # 或 null
      prompt_path: prompts/generate_patch_prompt.txt
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 允许两种写法：顶层直接放字段，或嵌在 generation 下
    if "generation" in cfg:
        gen_cfg = cfg["generation"] or {}
    else:
        gen_cfg = cfg

    required_keys = ["dataset_name", "split", "model_name", "output_dir", "prompt_path"]
    missing = [k for k in required_keys if k not in gen_cfg]
    if missing:
        raise KeyError(f"Missing keys in config under 'generation': {missing}")

    # 一些可选字段给默认值
    gen_cfg.setdefault("is_swt", False)
    gen_cfg.setdefault("filter_swt", False)
    gen_cfg.setdefault("instance_ids", None)

    return gen_cfg


def load_api_key() -> str:
    """
    Load OpenRouter API key from environment (.env or shell).
    """
    # Load from .env if present (优先从项目根目录加载)
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        # 如果项目根目录没有 .env，尝试从当前工作目录向上查找
        load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not found. Please set it in your environment or .env file."
        )
    return api_key


def load_prompt_template(prompt_path: Path) -> str:
    """
    从外部文件加载 prompt 模板。

    模板中可以使用以下占位符:
      {instance_id}, {repo}, {base_commit}, {problem_statement}
    """
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_prompt(instance: Dict[str, Any], template: str) -> str:
    """
    使用外部模板和实例字段构造 prompt。
    """
    return template.format(
        instance_id=instance["instance_id"],
        repo=instance["repo"],
        base_commit=instance["base_commit"],
        problem_statement=instance.get("problem_statement", ""),
    ).strip()


def call_openrouter(
    api_key: str,
    prompt: str,
    model: str,
    base_url: str = "https://openrouter.ai/api/v1/chat/completions",
) -> str:
    """
    Call OpenRouter chat completions API and return the model's response text.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a code generation model that returns ONLY raw unified git diff patches. "
                    "Do not include markdown fences or explanations."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    # 创建带重试机制的 session
    session = requests.Session()
    
    # 配置重试策略
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    # 从环境变量获取代理设置（如果存在）
    # requests 会自动读取环境变量中的代理设置，但我们可以显式配置以更好地处理 SOCKS5
    proxies = {}
    
    # 优先使用 http_proxy 和 https_proxy
    https_proxy = os.getenv("https_proxy")
    http_proxy = os.getenv("http_proxy")
    all_proxy = os.getenv("all_proxy")
    
    if https_proxy:
        proxies["https"] = https_proxy
    if http_proxy:
        proxies["http"] = http_proxy
    
    # 如果设置了 all_proxy 且没有设置具体的 http/https_proxy，则使用 all_proxy
    if all_proxy and not proxies:
        proxies["https"] = all_proxy
        proxies["http"] = all_proxy
    
    # 如果没有设置代理，使用 None（让 requests 自动从环境变量读取）
    if not proxies:
        proxies = None

    try:
        resp = session.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=600,
            proxies=proxies,
            verify=True,  # 保持 SSL 验证
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected OpenRouter response format: {data}") from e
    except requests.exceptions.SSLError as e:
        # 如果 SSL 错误持续，提供更详细的错误信息
        raise RuntimeError(
            f"SSL connection error when calling OpenRouter API. "
            f"This may be caused by proxy configuration or network issues. "
            f"Original error: {e}"
        ) from e


def generate_predictions_for_dataset(
    dataset_name: str,
    split: str,
    model_name: str,
    output_dir: Path,
    prompt_template: str,
    is_swt: bool = False,
    filter_swt: bool = False,
    instance_ids: Optional[List[str]] = None,
) -> Path:
    """
    Generate predictions for all (or selected) instances in a dataset using an OpenRouter model.

    Each prediction has the form:
      {"instance_id": str, "model_patch": str, "model_name_or_path": str}

    The predictions are written to a timestamped .jsonl file in `output_dir`.
    """
    api_key = load_api_key()

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    dataset = load_swebench_dataset(
        name=dataset_name,
        split=split,
        is_swt=is_swt,
        filter_swt=filter_swt,
    )

    if instance_ids:
        wanted = set(instance_ids)
        dataset = [d for d in dataset if d["instance_id"] in wanted]

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    outfile = output_dir / f"{model_name.replace('/', '__')}.{dataset_name.split('/')[-1]}.{split}.{timestamp}.jsonl"

    with outfile.open("w", encoding="utf-8") as f:
        for instance in dataset:
            prompt = build_prompt(instance, prompt_template)
            patch = call_openrouter(api_key, prompt, model=model_name)
            record = {
                "instance_id": instance["instance_id"],
                "model_patch": patch,
                "model_name_or_path": model_name,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return outfile


def main() -> None:
    """
    主入口：从 config.yaml 读取配置，构造 prompt 并批量生成 predictions.jsonl。

    默认在项目根目录查找 config.yaml，可通过环境变量 SWT_CONFIG_PATH 覆盖。
    """
    # 解析配置文件路径
    config_env = os.getenv("SWT_CONFIG_PATH")
    if config_env:
        config_path = Path(config_env)
    else:
        # 基于脚本位置，在项目根目录查找 config.yaml
        config_path = PROJECT_ROOT / "config.yaml"

    gen_cfg = load_config(config_path)

    # prompt_path 和 output_dir 都基于项目根目录解析
    prompt_path = PROJECT_ROOT / gen_cfg["prompt_path"]
    prompt_template = load_prompt_template(prompt_path)

    output_path = generate_predictions_for_dataset(
        dataset_name=gen_cfg["dataset_name"],
        split=gen_cfg["split"],
        model_name=gen_cfg["model_name"],
        output_dir=PROJECT_ROOT / gen_cfg["output_dir"],
        prompt_template=prompt_template,
        is_swt=gen_cfg.get("is_swt", False),
        filter_swt=gen_cfg.get("filter_swt", False),
        instance_ids=gen_cfg.get("instance_ids"),
    )
    print(f"Predictions written to: {output_path}")


if __name__ == "__main__":
    main()


