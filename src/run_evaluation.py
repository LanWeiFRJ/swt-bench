import hashlib
from functools import partial
from time import time

import docker
from docker.models.containers import Container
import json
import traceback
import logging
import os

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
from typing import List, Tuple, Optional

from src.auxillary_src.extract_patches import remove_binary_diffs
from src.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS
)
from src.docker_utils import (
    remove_image,
    copy_to_container,
    exec_run_with_timeout,
    cleanup_container,
    list_images,
    should_remove,
    checked_exec_run,
)
from src.docker_build import start_container, BuildMode
from src.test_spec import make_test_spec, TestSpec
from src.utils import get_log_dir, get_test_directives, log_git_diff, setup_logging, link_image_build_dir, close_logger
from src.exec_spec import ExecSpec, make_exec_spec, ExecMode
from src.grading import report_results

# 获取模块级别的 logger
logger = logging.getLogger(__name__)


class EvaluationError(Exception):
    def __init__(self, instance_id, message, logger: logging.Logger):
        super().__init__(message)
        self.instance_id = instance_id
        self.log_file = logger.log_file
        self.logger = logger

    def __str__(self):
        log_msg = traceback.format_exc()
        self.logger.info(log_msg)
        return (
            f"{self.instance_id}: {super().__str__()}\n"
            f"Check ({self.log_file}) for more information."
        )


def extract_model_patch(exec_spec: ExecSpec, raw_model_patch: str, patch_types: List[str], build_mode: BuildMode = "api") -> str:
    log_dir = get_log_dir(exec_spec.run_id, exec_spec.patch_id, exec_spec.instance_id)
    instance_id = exec_spec.instance_id

    if os.path.exists(log_dir / "extracted_patch.diff"):
        logger.info(f"  发现已存在的提取patch文件，直接使用: {log_dir / 'extracted_patch.diff'}")
        with open(log_dir / "extracted_patch.diff", "r") as f:
            extracted_patch = f.read()
        logger.info(f"  已加载提取的patch，长度: {len(extracted_patch)} 字符")
    else:
        logger.info(f"  开始提取patch（实例: {instance_id}）")
        logger.info(f"    原始patch长度: {len(raw_model_patch)} 字符")
        logger.info(f"    提取类型: {patch_types}")
        logger.info(f"    参考commit: {exec_spec.base_commit}")
        logger.info(f"    日志目录: {log_dir}")
        
        logger, _ = setup_logging(log_dir, exec_spec.instance_id)

        container = None
        client = docker.from_env()
        try:
            logger.info(f"    启动容器用于patch提取...")
            container = start_container(exec_spec, client, logger, build_mode=build_mode)
            logger.info(f"    容器启动成功: {container.name}")

            logger.info(f"    保存原始patch到文件...")
            with open(log_dir / "raw_model_patch.txt", "w") as f:
                f.write(raw_model_patch)
            copy_to_container(container, log_dir / "raw_model_patch.txt", Path("/root/raw_model_patch.txt"), build_mode=build_mode)
            logger.info(f"    原始patch已复制到容器")

            requirements_file = Path(os.path.join(os.path.dirname(__file__), "auxillary_src", "requirements_extraction.txt"))
            copy_to_container(container, requirements_file, Path("/root/requirements_extraction.txt"), build_mode=build_mode)
            logger.info(f"    提取工具依赖文件已复制到容器")

            extraction_file = Path(os.path.join(os.path.dirname(__file__), "auxillary_src", "extract_patches.py"))
            copy_to_container(container, extraction_file, Path("/root/extract_patches.py"), build_mode=build_mode)
            logger.info(f"    提取脚本已复制到容器")

            logger.info(f"    安装提取工具依赖...")
            checked_exec_run(container, "pip3 install -r /root/requirements_extraction.txt")
            logger.info(f"    依赖安装完成")

            logger.info(f"    执行patch提取命令...")
            extract_cmd = f"python3 /root/extract_patches.py --patch_type {' '.join(patch_types)} --reference_commit {exec_spec.base_commit}"
            logger.info(f"    命令: {extract_cmd}")
            res = container.exec_run(extract_cmd)
            logger.info(f"    提取命令执行完成，退出码: {res.exit_code}")
            
            if res.exit_code == 0:
                logger.info(f"    提取成功，读取提取结果...")
                res = container.exec_run("cat /root/extracted_patch.diff")
                if res.exit_code == 0:
                    extracted_patch = res.output.decode("utf-8")
                    with open(log_dir / "extracted_patch.diff", "w") as f:
                        f.write(extracted_patch)
                    logger.info(f"    Patch提取成功，长度: {len(extracted_patch)} 字符，已保存到 {log_dir / 'extracted_patch.diff'}")
                else:
                    logger.warning(f"    无法读取提取的patch文件")
                    logger.info(f"    错误输出:\n {res.output.decode()}")
                    extracted_patch = ""
            else:
                logger.warning(f"    Patch提取失败，退出码: {res.exit_code}")
                error_output = res.output.decode()
                logger.info(f"    错误输出:\n {error_output}")
                extracted_patch = ""
        except Exception as e:
            logger.error(f"    Patch提取过程中发生异常: {e}")
            logger.info(traceback.format_exc())
            raise e
        finally:
            logger.info(f"    清理容器资源...")
            cleanup_container(client, container, logger)
            logger.info(f"    容器清理完成")
    return extracted_patch


def eval_in_container(log_dir: str, container: Container, logger: logging.Logger, eval_script: str, timeout: int, instance_id: str, compute_coverage: bool, build_mode: str) -> str:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    
    logger.info(f"    准备评估脚本...")
    eval_file = Path(log_dir / "eval.sh")
    eval_file.write_text(eval_script)
    script_lines = len(eval_script.split('\n'))
    logger.info(f"    评估脚本已写入: {eval_file} ({script_lines} 行, {len(eval_script)} 字符)")
    
    logger.info(f"    复制评估脚本到容器...")
    copy_to_container(container, eval_file, Path("/eval.sh"), build_mode=build_mode)
    logger.info(f"    评估脚本已复制到容器: /eval.sh")
    
    if compute_coverage:
        logger.info(f"    计算覆盖率模式已启用，复制trace.py...")
        trace_file = Path(os.path.join(os.path.dirname(__file__), "auxillary_src", "trace.py"))
        copy_to_container(container, trace_file, Path("/root/trace.py"), build_mode=build_mode)
        logger.info(f"    trace.py已复制到容器")
    else:
        logger.info(f"    未启用覆盖率计算")

    logger.info(f"    执行评估脚本 (超时: {timeout}秒)...")
    logger.info(f"    命令: /bin/bash /eval.sh")
    result = exec_run_with_timeout(container, "/bin/bash /eval.sh", timeout=timeout)
    test_output = result.decode("utf-8")
    output_lines = len(test_output.split('\n'))
    logger.info(f"    评估脚本执行完成，输出: {len(test_output)} 字符, {output_lines} 行")
    
    test_output_path = log_dir / "test_output.txt"
    logger.info(f"    保存测试输出到文件: {test_output_path}")
    with open(test_output_path, "w") as f:
        f.write(test_output)
    logger.info(f"    测试输出已保存")
    
    # Log a preview of the output
    output_preview_lines = test_output.split('\n')[:20]
    logger.info(f"    输出预览 (前20行):")
    for line in output_preview_lines:
        logger.info(f"      {line}")
    if len(test_output.split('\n')) > 20:
        logger.info(f"      ... (还有 {len(test_output.split('\n')) - 20} 行)")
    
    return test_output_path


def eval_in_container_with_diff(log_dir: Path, container: Container, logger: logging.Logger, eval_script: str, timeout: int, instance_id: str, compute_coverage: bool, build_mode: str) -> str:
    logger.info(f"    获取执行前的git diff状态...")
    git_diff_output_before = log_git_diff(logger, container, "Git diff before:")
    logger.info(f"    执行前的git diff长度: {len(git_diff_output_before)} 字符")

    test_output_path = eval_in_container(log_dir, container, logger, eval_script, timeout, instance_id, compute_coverage, build_mode=build_mode)

    logger.info(f"    获取执行后的git diff状态...")
    git_diff_output_after = log_git_diff(logger, container, "Git diff after:")
    logger.info(f"    执行后的git diff长度: {len(git_diff_output_after)} 字符")

    if git_diff_output_after != git_diff_output_before:
        logger.info(f"    ⚠️  Git diff在执行前后发生了变化")
        diff_changes = len(git_diff_output_after) - len(git_diff_output_before)
        logger.info(f"    Diff变化: {diff_changes:+d} 字符")
    else:
        logger.info(f"    ✓ Git diff在执行前后保持一致")
    
    return test_output_path


def run_instance(
        test_spec: TestSpec,
        pred: dict,
        rm_image: bool,
        force_rebuild: bool,
        compute_coverage: bool,
        run_id: str,
        patch_types: List[str],
        timeout: int = None,
        build_mode: BuildMode = "api",
    ):
    """
    Run a single instance with the given prediction.

    Args:
        test_spec (TestSpec): TestSpec instance
        pred (dict): Prediction w/ model_name_or_path, model_patch, instance_id
        rm_image (bool): Whether to remove the image after running
        force_rebuild (bool): Whether to force rebuild the image
        compute_coverage (bool): Whether to compute coverage
        run_id (str): Run ID
        patch_types (List[str]): Patch types to extract
        timeout (int): Timeout for running tests
    """

    exec_spec = test_spec.exec_spec
    exec_spec.timeout = timeout
    exec_spec.rm_image = rm_image
    exec_spec.force_rebuild = force_rebuild
    exec_spec.run_id = run_id
    exec_spec.compute_coverage = compute_coverage
    instance_id = test_spec.instance_id

    logger.info(f"=" * 80)
    logger.info(f"开始处理实例: {instance_id}")
    logger.info(f"  仓库: {exec_spec.repo}")
    logger.info(f"  版本: {exec_spec.version}")
    logger.info(f"  执行模式: {exec_spec.exec_mode}")
    logger.info(f"  超时设置: {timeout}秒")
    logger.info(f"  强制重建镜像: {force_rebuild}")
    logger.info(f"  运行后移除镜像: {rm_image}")
    logger.info(f"  计算覆盖率: {compute_coverage}")

    patch_id_base = pred.get("model_name_or_path", "None").replace("/", "__")
    exec_spec.patch_id = patch_id_base
    logger.info(f"  模型/路径: {pred.get('model_name_or_path', 'None')}")
    logger.info(f"  Patch ID base: {patch_id_base}")

    # Extract or process model patch
    if len(patch_types) == 1 and patch_types[0] == "vanilla":
        logger.info(f"  使用 vanilla 模式处理 patch（移除二进制差异）")
        model_patch = remove_binary_diffs(pred["model_patch"])
        logger.info(f"  处理后的 patch 长度: {len(model_patch)} 字符")
    else:
        logger.info(f"  提取 patch，类型: {patch_types}")
        model_patch = extract_model_patch(exec_spec, pred["model_patch"], patch_types, build_mode=build_mode)
        logger.info(f"  提取后的 patch 长度: {len(model_patch) if model_patch else 0} 字符")

    if model_patch:
        logger.info(f"  发现有效的 model_patch，开始执行评估流程")
        caching_log_dir = [False, False, True, True, True, True]
        patch_ids = ["pred_pre__" + patch_id_base, "pred_post__" + patch_id_base, "gold_pre", "gold_post", "base_pre", "base_post"]
        test_patches = [model_patch, model_patch, test_spec.golden_test_patch, test_spec.golden_test_patch, None, None]
        code_patches = [None, test_spec.golden_code_patch, None, test_spec.golden_code_patch, None, test_spec.golden_code_patch]
        patch_names = ["模型patch(测试前)", "模型patch(测试后)", "黄金测试patch(测试前)", "黄金测试patch(测试后)", "基础(测试前)", "基础(测试后)"]

        output_paths = []
        for idx, (cld, test_patch, code_patch, patch_id, patch_name) in enumerate(zip(caching_log_dir, test_patches, code_patches, patch_ids, patch_names), 1):
            logger.info(f"  [{idx}/6] 执行评估: {patch_name} (patch_id: {patch_id})")
            exec_spec.test_directives = get_test_directives(model_patch if test_patch is None else test_patch, exec_spec.repo)
            exec_spec.patch_list = [] if code_patch is None else [code_patch]
            exec_spec.patch_list += [test_patch] if test_patch else []
            exec_spec.patch_id = patch_id
            
            logger.info(f"    测试指令数量: {len(exec_spec.test_directives)}")
            logger.info(f"    代码patch: {'有' if code_patch else '无'} ({len(code_patch) if code_patch else 0} 字符)")
            logger.info(f"    测试patch: {'有' if test_patch else '无'} ({len(test_patch) if test_patch else 0} 字符)")
            
            if cld:
                log_dir = get_log_dir(patch_id, instance_id, test_directive_id(exec_spec.test_directives))
                logger.info(f"    日志目录: {log_dir}")
            else:
                log_dir = None
                logger.info(f"    日志目录: 未指定（将使用默认位置）")
            
            _, test_output_path = run_eval_exec_spec(exec_spec, model_patch, log_dir, build_mode)
            output_paths.append(test_output_path)
            logger.info(f"    评估完成，输出文件: {test_output_path}")
        logger.info(f"  所有评估步骤完成，共生成 {len(output_paths)} 个输出文件")
    else:
        logger.warning(f"  model_patch 为空，跳过评估")
    
    logger.info(f"实例 {instance_id} 处理完成")
    logger.info(f"=" * 80)
    return instance_id


def run_eval_exec_spec(exec_spec: ExecSpec, model_patch: str, log_dir: Optional[Path]=None, build_mode: BuildMode = "api") -> Tuple[str, Path]:
    client = docker.from_env()
    instance_id = exec_spec.instance_id

    if log_dir is None:
        log_dir = get_log_dir(exec_spec.run_id, exec_spec.patch_id, exec_spec.instance_id)

    logger, _ = setup_logging(log_dir, instance_id)
    start_time = time()
    logger.info(f"=" * 80)
    logger.info(f"开始评估执行 (实例: {instance_id})")
    logger.info(f"  开始时间: {start_time}")
    logger.info(f"  Patch ID: {exec_spec.patch_id}")
    logger.info(f"  Run ID: {exec_spec.run_id}")
    logger.info(f"  日志目录: {log_dir}")
    logger.info(f"  实例镜像: {exec_spec.instance_image_key}")
    logger.info(f"  超时设置: {exec_spec.timeout}秒")
    logger.info(f"  计算覆盖率: {exec_spec.compute_coverage}")
    logger.info(f"  Patch数量: {len(exec_spec.patch_list)}")
    for idx, patch in enumerate(exec_spec.patch_list, 1):
        patch_preview = patch[:100].replace('\n', '\\n') if patch else "None"
        logger.info(f"    Patch {idx}: {patch_preview}... ({len(patch) if patch else 0} 字符)")

    logger.info(f"  保存执行规格到 exec_spec.json...")
    with open(log_dir / "exec_spec.json", "w") as f:
        json.dump(exec_spec.as_dict(), f, indent=2)
    logger.info(f"  执行规格已保存")

    logger.info(f"  保存model_patch到 model_patch.diff...")
    with open(log_dir / "model_patch.diff", "w") as f:
        f.write(model_patch)
    logger.info(f"  Model patch已保存 ({len(model_patch)} 字符)")

    if (log_dir / "test_output.txt").exists():
        logger.info(f"  发现已存在的测试输出文件，跳过执行: {log_dir / 'test_output.txt'}")
        return instance_id, (log_dir / "test_output.txt")

    logger.info(f"  链接镜像构建目录...")
    link_image_build_dir(log_dir, exec_spec.instance_image_key)
    logger.info(f"  镜像构建目录已链接")

    # Run the instance
    container = None
    try:
        logger.info(f"  启动容器...")
        container = start_container(exec_spec, client, logger, build_mode=build_mode)
        logger.info(f"  容器启动成功: {container.name if container else 'N/A'}")

        logger.info(f"  在容器中执行评估脚本...")
        test_output_path = eval_in_container_with_diff(log_dir, container, logger, exec_spec.eval_script, exec_spec.timeout, instance_id, exec_spec.compute_coverage, build_mode=build_mode)
        logger.info(f"  评估脚本执行完成，输出文件: {test_output_path}")

        end_time = time()
        elapsed_time = end_time - start_time
        logger.info(f"  评估完成，耗时: {elapsed_time:.2f}秒")
        logger.info(f"=" * 80)
        return instance_id, test_output_path
    except EvaluationError as e:
        end_time = time()
        elapsed_time = end_time - start_time
        logger.error(f"  评估失败 (耗时: {elapsed_time:.2f}秒): {e}")
        logger.info(f"=" * 80)
        raise EvaluationError(instance_id, str(e), logger) from e
    except Exception as e:
        end_time = time()
        elapsed_time = end_time - start_time
        logger.error(f"  评估过程中发生异常 (耗时: {elapsed_time:.2f}秒): {e}")
        logger.info(traceback.format_exc())
        logger.info(f"=" * 80)
        raise EvaluationError(instance_id, str(e), logger) from e
    finally:
        logger.info(f"  清理资源...")
        cleanup_container(client, container, logger)
        if exec_spec.rm_image:
            logger.info(f"  移除实例镜像: {exec_spec.instance_image_key}")
            remove_image(client, exec_spec.instance_image_key, logger)
        end_time = time()
        elapsed_time = end_time - start_time
        logger.info(f"  评估执行结束，总耗时: {elapsed_time:.2f}秒")
        close_logger(logger)


def run_instances(
        predictions: dict,
        instances: list,
        compute_coverage: bool,
        cache_level: str,
        clean: bool,
        force_rebuild: bool,
        max_workers: int,
        run_id: str,
        patch_types: List[str],
        timeout: int,
        client: docker.DockerClient,
        build_mode: BuildMode,
        exec_mode: ExecMode,
        reproduction_script_name: Optional[str] = None,
    ):
    """
    Run all instances for the given predictions in parallel.

    Args:
        predictions (dict): Predictions dict generated by the model
        instances (list): List of instances
        cache_level (str): Cache level
        clean (bool): Clean images above cache level
        force_rebuild (bool): Force rebuild images
        max_workers (int): Maximum number of workers
        run_id (str): Run ID
        timeout (int): Timeout for running tests
        client (docker.DockerClient): Docker client
        build_mode (BuildMode): Build mode
        exec_mode (ExecMode): Execution mode
        reproduction_script_name (Optional[str]): Name of the reproduction script
    """
    test_specs = list(map(partial(make_test_spec, exec_mode=exec_mode, reproduction_script_name=reproduction_script_name), instances))

    # print number of existing instance images
    logger.info(f"=" * 80)
    logger.info(f"开始批量执行实例评估")
    logger.info(f"  总实例数: {len(instances)}")
    logger.info(f"  最大并发数: {max_workers}")
    logger.info(f"  执行模式: {exec_mode}")
    logger.info(f"  超时设置: {timeout}秒")
    logger.info(f"  强制重建: {force_rebuild}")
    logger.info(f"  缓存级别: {cache_level}")
    logger.info(f"  清理模式: {clean}")
    logger.info(f"  计算覆盖率: {compute_coverage}")
    
    instance_image_ids = {x.exec_spec.instance_image_key for x in test_specs}
    existing_images = {
        tag for i in client.images.list(all=True)
        for tag in i.tags if tag in instance_image_ids
    }
    logger.info(f"  需要使用的实例镜像数: {len(instance_image_ids)}")
    if not force_rebuild and len(existing_images):
        logger.info(f"  ✓ 发现 {len(existing_images)} 个已存在的实例镜像，将重用它们")
        logger.info(f"  可重用的镜像: {sorted(existing_images)}")
    else:
        if force_rebuild:
            logger.info(f"  强制重建模式，将重新构建所有镜像")
        else:
            logger.info(f"  未发现可重用的镜像，将构建新镜像")

    # Log instance IDs
    instance_ids = [spec.instance_id for spec in test_specs]
    logger.info(f"  实例列表:")
    for idx, instance_id in enumerate(instance_ids, 1):
        logger.info(f"    {idx}. {instance_id}")

    # run instances in parallel
    logger.info(f"  开始并行执行 {len(instances)} 个实例...")
    logger.info(f"=" * 80)
    with tqdm(total=len(instances), smoothing=0) as pbar:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create a future for running each instance
            futures = [
                executor.submit(
                    run_instance,
                    test_spec,
                    predictions[test_spec.instance_id],
                    should_remove(
                        test_spec.exec_spec.instance_image_key,
                        cache_level,
                        clean,
                        existing_images,
                    ),
                    force_rebuild,
                    compute_coverage,
                    run_id,
                    patch_types,
                    timeout,
                    build_mode,
                )
                for test_spec in test_specs
            ]
            # Wait for each future to complete
            try:
                for future in as_completed(futures):
                    # Update progress bar
                    pbar.update(1)
                    # check if instance ran successfully
                    e = future.exception()
                    if e is None:
                        continue
                    try:
                        if isinstance(e, EvaluationError):
                            logger.error(f"✗ 实例评估失败: {e.instance_id}")
                            logger.error(f"  错误详情: {e}")
                        elif isinstance(e, Exception):
                            logger.error(f"✗ 执行异常", exc_info=True)
                        elif isinstance(e, TimeoutError):
                            logger.error(f"✗ 超时错误: {e}")
                    except Exception as e:
                        logger.error(f"✗ 格式化错误信息时发生异常: {e}", exc_info=True)
            except TimeoutError as e:
                logger.error(f"✗ 批量执行超时: {e}")
    logger.info(f"=" * 80)
    logger.info(f"所有实例执行完成 (共 {len(instances)} 个)")
    logger.info(f"=" * 80)

def find_all_test_output_paths(dir: Path):
    for file in dir.rglob("test_output.txt"):
        yield file

def test_directive_id(test_directives: list[str]):
    return hashlib.sha256("__".join(test_directives).encode()).hexdigest()


def make_run_report(
        predictions: dict,
        dataset: list,
        client: docker.DockerClient,
        run_id: str,
        exec_mode: ExecMode,
    ):
    """
    Make a final evaluation and run report of the instances that have been run.
    Also reports on images and containers that may still running!

    Args:
        predictions (dict): Predictions dict generated by the model
        dataset (list): List of instances
        client (docker.DockerClient): Docker client
        run_id (str): Run ID
    """
    # instantiate sets to store IDs of different outcomes
    completed_ids = set()
    resolved_ids = set()
    error_ids = set()
    unstopped_containers = set()
    unremoved_images = set()
    unresolved_ids = set()
    coverages = []
    coverage_deltas = []


    logger.info(f"  遍历数据集，处理每个实例...")
    # iterate through dataset and check if the instance has been run
    for instance in tqdm(dataset):
        instance_id = instance["instance_id"]
        prediction = predictions.get(instance_id)
        if not prediction:
            logger.warning(f"    实例 {instance_id}: 未找到对应的预测，跳过")
            error_ids.add(instance_id)
            continue
        
        patch_id_base = prediction["model_name_or_path"].replace("/", "__")
        model_patch_file = get_log_dir(
                run_id,
                "pred_pre__" + patch_id_base,
                instance_id,
        ) / "model_patch.diff"
        test_output_file = model_patch_file.parent / "test_output.txt"
        if not model_patch_file.exists() or not test_output_file.exists():
            # The instance was not run successfully
            logger.warning(f"    实例 {instance_id}: 缺少必要文件，标记为错误")
            if not model_patch_file.exists():
                logger.warning(f"      缺少文件: {model_patch_file}")
            if not test_output_file.exists():
                logger.warning(f"      缺少文件: {test_output_file}")
            error_ids.add(instance_id)
            continue

        report_file = get_log_dir(
            run_id,
            patch_id_base,
            instance_id,
        ) / "report.json"
        if report_file.exists():
            # If report file exists, then the instance has been run and reported before
            logger.info(f"    实例 {instance_id}: 发现已存在的报告文件，直接使用")
            completed_ids.add(instance_id)
            report = json.loads(report_file.read_text())
        else:
            logger.info(f"    实例 {instance_id}: 生成新报告")
            with model_patch_file.open() as f:
                model_patch = f.read()

            patch_ids = ["pred_pre__" + patch_id_base, "pred_post__" + patch_id_base, "gold_pre", "gold_post",
                         "base_pre", "base_post"]
            model_test_directive_path = test_directive_id(get_test_directives(model_patch, instance["repo"]))
            gold_test_directive_path = test_directive_id(
                get_test_directives(instance["golden_test_patch"], instance["repo"]))
            directive_paths = [gold_test_directive_path, gold_test_directive_path, model_test_directive_path,
                               model_test_directive_path]
            output_paths = (
                    [
                        get_log_dir(run_id, patch_id, instance_id) / "test_output.txt" for patch_id in patch_ids[:2]
                    ] + [
                        get_log_dir(patch_id, instance_id, directive_path) / "test_output.txt" for
                        patch_id, directive_path in zip(patch_ids[2:], directive_paths)
                    ]
            )
            report = report_results(
                patch_id_base,
                run_id,
                instance["golden_code_patch"],
                output_paths,
                instance_id,
                instance["repo"],
                exec_mode,
            )
        
        # Record results
        report_data = report.get(instance_id, {})
        if report_data.get("resolved", False):
            logger.info(f"    实例 {instance_id}: ✓ 已解决")
            resolved_ids.add(instance_id)
        else:
            logger.info(f"    实例 {instance_id}: ✗ 未解决")
            unresolved_ids.add(instance_id)
        
        if report_data.get("coverage_pred") is not None:
            coverage_deltas.append(report_data.get("coverage_delta_pred", 0))
            coverages.append(report_data.get("coverage_pred", 0))


    if len(coverage_deltas) > 0:
        coverage_delta = sum(coverage_deltas)/len(coverage_deltas)
        coverage = sum(coverages) / len(coverages)
    else:
        coverage_delta = 0
        coverage = 0

    # get remaining images and containers
    images = list_images(client)
    exec_specs = list(map(make_exec_spec, dataset))
    for spec in exec_specs:
        image_name = spec.instance_image_key
        if image_name in images:
            unremoved_images.add(image_name)
    containers = client.containers.list(all=True)
    for container in containers:
        if run_id in container.name:
            unstopped_containers.add(container.name)

    # log final report
    logger.info("=" * 80)
    logger.info("最终评估报告")
    logger.info(f"=" * 80)
    logger.info(f"总体统计:")
    logger.info(f"  总实例数: {len(dataset)}")
    logger.info(f"  已完成实例数: {len(completed_ids)} ({len(completed_ids)/len(dataset)*100:.1f}%)")
    logger.info(f"")
    logger.info(f"结果统计:")
    logger.info(f"  ✓ 已解决 (resolved): {len(resolved_ids)} ({len(resolved_ids)/len(dataset)*100:.1f}%)")
    logger.info(f"  ✗ 未解决 (unresolved): {len(unresolved_ids)} ({len(unresolved_ids)/len(dataset)*100:.1f}%)")
    logger.info(f"  ⚠️  错误 (errors): {len(error_ids)} ({len(error_ids)/len(dataset)*100:.1f}%)")
    logger.info(f"")
    logger.info(f"覆盖率统计:")
    logger.info(f"  平均覆盖率: {coverage:.4f}")
    logger.info(f"  平均覆盖率变化: {coverage_delta:+.4f}")
    logger.info(f"")
    logger.info(f"资源统计:")
    logger.info(f"  仍在运行的容器: {len(unstopped_containers)}")
    if unstopped_containers:
        logger.info(f"    容器列表: {sorted(unstopped_containers)}")
    logger.info(f"  仍存在的镜像: {len(unremoved_images)}")
    if unremoved_images and len(unremoved_images) <= 20:
        logger.info(f"    镜像列表: {sorted(unremoved_images)}")
    elif unremoved_images:
        logger.info(f"    镜像列表 (前20个): {sorted(list(unremoved_images))[:20]}")
        logger.info(f"    ... (还有 {len(unremoved_images) - 20} 个)")
    logger.info(f"=" * 80)

    # write report to file
    report = {
        "total_instances": len(dataset),
        "completed_instances": len(completed_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "error_instances": len(error_ids),
        "Mean coverage": coverage,
        "Mean coverage delta": coverage_delta,
        "unstopped_instances": len(unstopped_containers),
        "completed_ids": list(sorted(completed_ids)),
        "resolved_ids": list(sorted(resolved_ids)),
        "unresolved_ids": list(sorted(unresolved_ids)),
        "error_ids": list(sorted(error_ids)),
        "unstopped_containers": list(sorted(unstopped_containers)),
        "unremoved_images": list(sorted(unremoved_images)),
    }
    report_file = Path(os.path.join("evaluation_results",
        list(predictions.values())[0]["model_name_or_path"].replace("/", "__")
        + f".{run_id}"
        + ".json")
    )
    Path("evaluation_results").mkdir(parents=True, exist_ok=True)
    with open(report_file, "w") as f:
        print(json.dumps(report, indent=4), file=f)
    logger.info(f"Report written to {report_file}")