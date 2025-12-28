"""
全局日志配置模块
将所有日志按照级别分别存储到 logs/{timestamp}/ 目录下
"""
import datetime
import logging
import sys
from pathlib import Path
from typing import Optional

# 全局日志目录
_GLOBAL_LOG_DIR: Optional[Path] = None


def setup_global_logging(project_root: Path, run_id: Optional[str] = None) -> Path:
    """
    设置全局日志系统，将日志按照级别分别存储到文件中
    
    Args:
        project_root: 项目根目录
        run_id: 运行ID（可选），如果提供，会在日志目录名中包含run_id
        
    Returns:
        日志目录路径
    """
    global _GLOBAL_LOG_DIR
    
    # 创建 logs 目录
    logs_base_dir = project_root / "logs"
    logs_base_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成时间戳
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    
    # 创建日志子目录
    if run_id:
        log_dir = logs_base_dir / f"{timestamp}_{run_id}"
    else:
        log_dir = logs_base_dir / timestamp
    
    log_dir.mkdir(parents=True, exist_ok=True)
    _GLOBAL_LOG_DIR = log_dir
    
    # 配置日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # 清除现有的处理器（避免重复添加）
    root_logger.handlers.clear()
    
    # 1. ALL 日志文件处理器 - 记录所有级别的日志
    all_handler = logging.FileHandler(log_dir / "all.log", encoding='utf-8')
    all_handler.setLevel(logging.DEBUG)
    all_handler.setFormatter(formatter)
    root_logger.addHandler(all_handler)
    
    # 2. INFO 日志文件处理器 - 记录 INFO 及以上级别
    info_handler = logging.FileHandler(log_dir / "info.log", encoding='utf-8')
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)
    root_logger.addHandler(info_handler)
    
    # 3. WARNING 日志文件处理器 - 记录 WARNING 及以上级别
    warning_handler = logging.FileHandler(log_dir / "warning.log", encoding='utf-8')
    warning_handler.setLevel(logging.WARNING)
    warning_handler.setFormatter(formatter)
    root_logger.addHandler(warning_handler)
    
    # 4. ERROR 日志文件处理器 - 只记录 ERROR 及以上级别
    error_handler = logging.FileHandler(log_dir / "error.log", encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)
    
    # 5. 控制台处理器 - 输出到终端（INFO级别）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 记录日志系统初始化信息
    root_logger.info("=" * 80)
    root_logger.info("Global logging system initialized")
    root_logger.info(f"Log directory: {log_dir}")
    root_logger.info(f"Log files: all.log, info.log, warning.log, error.log")
    root_logger.info("=" * 80)
    
    return log_dir


def get_global_log_dir() -> Optional[Path]:
    """
    获取全局日志目录路径
    
    Returns:
        全局日志目录路径，如果未初始化则返回 None
    """
    return _GLOBAL_LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器
    
    Args:
        name: 日志记录器名称（通常是模块名）
        
    Returns:
        Logger 实例
    """
    return logging.getLogger(name)

