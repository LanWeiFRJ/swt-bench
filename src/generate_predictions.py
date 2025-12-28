import datetime
import json
import logging
import os
import re
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

# 配置日志 - 同时输出到终端和文件
def setup_logging() -> Path:
    """
    配置日志系统，将日志同时输出到终端和文件。
    返回日志文件路径。
    """
    # 创建 logs 目录（如果不存在）
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成带时间戳的日志文件名
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    log_file = logs_dir / f"generate_predictions_{timestamp}.log"
    
    # 配置日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 创建处理器：终端输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # 创建处理器：文件输出
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # 配置根日志记录器
logging.basicConfig(
    level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[console_handler, file_handler],
        force=True,  # 如果已经配置过日志，强制重新配置
    )
    
    return log_file

# 设置日志并获取日志文件路径
log_file_path = setup_logging()
logger = logging.getLogger(__name__)
logger.info(f"日志将同时写入终端和文件: {log_file_path}")


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
    logger.info(f"Loading configuration from: {config_path}")
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

    logger.info(f"Configuration loaded: dataset={gen_cfg['dataset_name']}, model={gen_cfg['model_name']}, "
                f"split={gen_cfg['split']}, instances={len(gen_cfg.get('instance_ids', [])) if gen_cfg.get('instance_ids') else 'all'}")

    return gen_cfg


def load_api_key() -> str:
    """
    Load OpenRouter API key from environment (.env or shell).
    """
    logger.info("Loading OpenRouter API key...")
    # Load from .env if present (优先从项目根目录加载)
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        logger.debug(f"Loading .env from: {env_path}")
        load_dotenv(dotenv_path=env_path)
    else:
        # 如果项目根目录没有 .env，尝试从当前工作目录向上查找
        logger.debug("No .env file found in project root, trying current directory")
        load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not found. Please set it in your environment or .env file."
        )
    logger.info("API key loaded successfully")
    return api_key


def load_prompt_template(prompt_path: Path) -> str:
    """
    从外部文件加载 prompt 模板。

    模板中可以使用以下占位符:
      {instance_id}, {repo}, {base_commit}, {problem_statement}
    """
    logger.info(f"Loading prompt template from: {prompt_path}")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    template = prompt_path.read_text(encoding="utf-8")
    logger.info(f"Prompt template loaded, size: {len(template)} characters")
    return template


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


def clean_markdown_code_blocks(text: str) -> str:
    """
    去除文本中的markdown代码块标记（```标记）。
    
    处理以下格式：
    - ```diff\n...\n```
    - ```git\n...\n```
    - ```python\n...\n```
    - ```\n...\n```（没有语言标识）
    - 可能只有开头或只有结尾的标记
    
    返回清理后的文本。
    """
    # 去除首尾空白，便于处理
    text = text.strip()
    
    # 去除开头的markdown代码块标记（可能带语言标识如diff、git、python等）
    # 匹配行首的```后跟可选的单词（语言标识）和可选的换行
    text = re.sub(r'^```[\w]*\s*\n?', '', text, flags=re.MULTILINE)
    
    # 去除结尾的markdown代码块标记
    # 匹配可选的换行后跟```和可选的空白
    text = re.sub(r'\n?```\s*$', '', text, flags=re.MULTILINE)
    
    # 再次去除首尾空白
    text = text.strip()
    
    return text


def call_openrouter(
    api_key: str,
    prompt: str,
    model: str,
    base_url: str = "https://openrouter.ai/api/v1/chat/completions",
) -> str:
    """
    Call OpenRouter chat completions API and return the model's response text.
    """
    logger.debug(f"Calling OpenRouter API with model: {model}, prompt length: {len(prompt)}")
    
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
        logger.debug(f"Using HTTPS proxy: {https_proxy}")
    if http_proxy:
        proxies["http"] = http_proxy
        logger.debug(f"Using HTTP proxy: {http_proxy}")
    
    # 如果设置了 all_proxy 且没有设置具体的 http/https_proxy，则使用 all_proxy
    if all_proxy and not proxies:
        proxies["https"] = all_proxy
        proxies["http"] = all_proxy
        logger.debug(f"Using all_proxy: {all_proxy}")
    
    # 如果没有设置代理，使用 None（让 requests 自动从环境变量读取）
    if not proxies:
        proxies = None
        logger.debug("No proxy configured")

    try:
        logger.info(f"Sending request to OpenRouter API (model: {model})...")
        start_time = datetime.datetime.now(datetime.UTC)
        
        resp = session.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=600,
            proxies=proxies,
            verify=True,  # 保持 SSL 验证
        )
        
        elapsed = (datetime.datetime.now(datetime.UTC) - start_time).total_seconds()
        logger.info(f"Received response from OpenRouter API (status: {resp.status_code}, elapsed: {elapsed:.2f}s)")
        
        resp.raise_for_status()
        data = resp.json()
        try:
            response_content = data["choices"][0]["message"]["content"]
            logger.debug(f"Response content length: {len(response_content)} characters")
            return response_content
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected response format: {data}")
            raise RuntimeError(f"Unexpected OpenRouter response format: {data}") from e
    except requests.exceptions.Timeout as e:
        logger.error(f"Request timeout after 600 seconds")
        raise RuntimeError(f"Request to OpenRouter API timed out: {e}") from e
    except requests.exceptions.SSLError as e:
        # 如果 SSL 错误持续，提供更详细的错误信息
        logger.error(f"SSL connection error: {e}")
        raise RuntimeError(
            f"SSL connection error when calling OpenRouter API. "
            f"This may be caused by proxy configuration or network issues. "
            f"Original error: {e}"
        ) from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {e}")
        raise RuntimeError(f"Error calling OpenRouter API: {e}") from e


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
    logger.info("=" * 80)
    logger.info("Starting prediction generation")
    logger.info(f"Dataset: {dataset_name}, Split: {split}, Model: {model_name}")
    logger.info(f"Output directory: {output_dir}")
    logger.info("=" * 80)
    
    api_key = load_api_key()

    # Ensure output directory exists
    logger.info(f"Creating output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    logger.info(f"Loading dataset: {dataset_name} (split: {split}, is_swt: {is_swt}, filter_swt: {filter_swt})...")
    dataset = load_swebench_dataset(
        name=dataset_name,
        split=split,
        is_swt=is_swt,
        filter_swt=filter_swt,
    )
    logger.info(f"Dataset loaded: {len(dataset)} instances")

    if instance_ids:
        wanted = set(instance_ids)
        dataset = [d for d in dataset if d["instance_id"] in wanted]
        logger.info(f"Filtered to {len(dataset)} instances based on instance_ids")

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    outfile = output_dir / f"{model_name.replace('/', '__')}.{dataset_name.split('/')[-1]}.{split}.{timestamp}.jsonl"
    logger.info(f"Output file: {outfile}")

    total_instances = len(dataset)
    logger.info(f"Processing {total_instances} instances...")
    
    start_time = datetime.datetime.now(datetime.UTC)
    successful = 0
    failed = 0

    with outfile.open("w", encoding="utf-8") as f:
        for idx, instance in enumerate(dataset, 1):
            instance_id = instance["instance_id"]
            logger.info(f"[{idx}/{total_instances}] Processing instance: {instance_id}")
            
            try:
                logger.debug(f"Building prompt for {instance_id}...")
                prompt = build_prompt(instance, prompt_template)
                logger.debug(f"Prompt built, length: {len(prompt)} characters")
                
                logger.info(f"Calling OpenRouter API for {instance_id}...")
                patch = call_openrouter(api_key, prompt, model=model_name)
                logger.info(f"Successfully generated patch for {instance_id} (length: {len(patch)} characters)")
                
                # 清理markdown代码块标记
                patch = clean_markdown_code_blocks(patch)
                logger.debug(f"Cleaned patch length: {len(patch)} characters")
                
                record = {
                    "instance_id": instance_id,
                    "model_patch": patch,
                    "model_name_or_path": model_name,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()  # 确保立即写入文件
                
                successful += 1
                
                # 计算进度和预计剩余时间
                if idx > 0:
                    elapsed = (datetime.datetime.now(datetime.UTC) - start_time).total_seconds()
                    avg_time_per_instance = elapsed / idx
                    remaining_instances = total_instances - idx
                    estimated_remaining = avg_time_per_instance * remaining_instances
                    
                    logger.info(f"Progress: {idx}/{total_instances} ({idx/total_instances*100:.1f}%) | "
                              f"Successful: {successful}, Failed: {failed} | "
                              f"Elapsed: {elapsed/60:.1f}min | "
                              f"Estimated remaining: {estimated_remaining/60:.1f}min")
                
            except Exception as e:
                failed += 1
                logger.error(f"Failed to process instance {instance_id}: {e}", exc_info=True)
                # 继续处理下一个实例，不中断整个流程
                logger.warning(f"Skipping instance {instance_id} and continuing...")

    elapsed_total = (datetime.datetime.now(datetime.UTC) - start_time).total_seconds()
    logger.info("=" * 80)
    logger.info("Prediction generation completed")
    logger.info(f"Total instances: {total_instances}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Total time: {elapsed_total/60:.1f} minutes ({elapsed_total:.1f} seconds)")
    logger.info(f"Output file: {outfile}")
    logger.info("=" * 80)

    return outfile


def main() -> None:
    """
    主入口：从 config.yaml 读取配置，构造 prompt 并批量生成 predictions.jsonl。

    默认在项目根目录查找 config.yaml，可通过环境变量 SWT_CONFIG_PATH 覆盖。
    """
    logger.info("Starting generate_predictions script")
    logger.info(f"Project root: {PROJECT_ROOT}")
    
    # 解析配置文件路径
    config_env = os.getenv("SWT_CONFIG_PATH")
    if config_env:
        config_path = Path(config_env)
        logger.info(f"Using config path from environment: {config_path}")
    else:
        # 基于脚本位置，在项目根目录查找 config.yaml
        config_path = PROJECT_ROOT / "config.yaml"
        logger.info(f"Using default config path: {config_path}")

    try:
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
        logger.info(f"✓ Predictions written to: {output_path}")
        print(f"Predictions written to: {output_path}")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()


