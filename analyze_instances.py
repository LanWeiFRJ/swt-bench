#!/usr/bin/env python3
"""
分析run_instance_swt_logs目录下的实例执行状态
提取每个实例在6个步骤中的成功/失败状态，以及patch应用和解决状态
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime


def parse_run_instance_log(log_path: Path) -> Dict[str, any]:
    """
    解析run_instance.log文件，提取6个步骤的状态信息
    
    返回:
        {
            'steps': {
                'pred_pre': {'patch_applied': bool, 'status': str},
                'pred_post': {'patch_applied': bool, 'status': str},
                'gold_pre': {'patch_applied': bool, 'status': str},
                'gold_post': {'patch_applied': bool, 'status': str},
                'base_pre': {'patch_applied': bool, 'status': str},
                'base_post': {'patch_applied': bool, 'status': str},
            },
            'final_result': {
                'patch_exists': Optional[bool],
                'patch_successfully_applied': Optional[bool],
                'resolved': Optional[bool],
            }
        }
    """
    result = {
        'steps': {},
        'final_result': {
            'patch_exists': None,
            'patch_successfully_applied': None,
            'resolved': None,
        }
    }
    
    if not log_path.exists():
        return result
    
    content = log_path.read_text(encoding='utf-8')
    
    # 解析6个步骤的状态
    step_patterns = [
        (r'\[1/6\] pred_pre:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'pred_pre'),
        (r'\[2/6\] pred_post:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'pred_post'),
        (r'\[3/6\] gold_pre:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'gold_pre'),
        (r'\[4/6\] gold_post:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'gold_post'),
        (r'\[5/6\] base_pre:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'base_pre'),
        (r'\[6/6\] base_post:.*?\n.*?Patch应用状态: ([✓✗]) ([成功失败]+)', 'base_post'),
    ]
    
    for pattern, step_name in step_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            symbol = match.group(1)
            status_text = match.group(2)
            patch_applied = (symbol == '✓' and '成功' in status_text)
            result['steps'][step_name] = {
                'patch_applied': patch_applied,
                'status': '成功' if patch_applied else '失败'
            }
        else:
            result['steps'][step_name] = {
                'patch_applied': None,
                'status': '未找到'
            }
    
    # 解析最终结果
    final_patterns = {
        'patch_exists': r'Patch存在: (True|False)',
        'patch_successfully_applied': r'Patch成功应用: (True|False)',
        'resolved': r'已解决 \(resolved\): (True|False)',
    }
    
    for key, pattern in final_patterns.items():
        match = re.search(pattern, content)
        if match:
            result['final_result'][key] = match.group(1) == 'True'
    
    # 也尝试从"最终结果"行提取
    final_result_match = re.search(r'最终结果: ([✓✗]) (解决|未解决)', content)
    if final_result_match:
        result['final_result']['resolved'] = (final_result_match.group(1) == '✓')
    
    return result


def parse_report_json(report_path: Path) -> Dict[str, any]:
    """
    解析report.json文件，获取最终评估结果
    """
    if not report_path.exists():
        return {}
    
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # report.json的格式是 {instance_id: {...}}
            # 取第一个实例的数据
            if data:
                instance_id = list(data.keys())[0]
                return data[instance_id]
    except Exception as e:
        print(f"警告: 无法解析 {report_path}: {e}")
    
    return {}


def analyze_instance(instance_dir: Path) -> Dict[str, any]:
    """
    分析单个实例目录
    """
    instance_id = instance_dir.name
    
    log_path = instance_dir / 'run_instance.log'
    report_path = instance_dir / 'report.json'
    
    log_data = parse_run_instance_log(log_path)
    report_data = parse_report_json(report_path)
    
    # 合并数据，report.json优先
    result = {
        'instance_id': instance_id,
        'steps': log_data['steps'],
        'patch_exists': report_data.get('patch_exists', log_data['final_result']['patch_exists']),
        'patch_successfully_applied': report_data.get('patch_successfully_applied', log_data['final_result']['patch_successfully_applied']),
        'resolved': report_data.get('resolved', log_data['final_result']['resolved']),
        'log_exists': log_path.exists(),
        'report_exists': report_path.exists(),
    }
    
    return result


def generate_summary_report(results: List[Dict[str, any]], output_path: Path):
    """
    生成汇总报告
    """
    total = len(results)
    
    # 统计各步骤的成功/失败数
    step_stats = {}
    for step in ['pred_pre', 'pred_post', 'gold_pre', 'gold_post', 'base_pre', 'base_post']:
        step_stats[step] = {
            'success': sum(1 for r in results if r['steps'].get(step, {}).get('patch_applied') is True),
            'failed': sum(1 for r in results if r['steps'].get(step, {}).get('patch_applied') is False),
            'not_found': sum(1 for r in results if r['steps'].get(step, {}).get('patch_applied') is None),
        }
    
    # 统计patch应用和解决状态
    patch_applied = sum(1 for r in results if r.get('patch_successfully_applied') is True)
    patch_not_applied = sum(1 for r in results if r.get('patch_successfully_applied') is False)
    patch_unknown = sum(1 for r in results if r.get('patch_successfully_applied') is None)
    
    resolved = sum(1 for r in results if r.get('resolved') is True)
    not_resolved = sum(1 for r in results if r.get('resolved') is False)
    resolved_unknown = sum(1 for r in results if r.get('resolved') is None)
    
    # 分类实例列表
    patch_applied_list = [r['instance_id'] for r in results if r.get('patch_successfully_applied') is True]
    patch_not_applied_list = [r['instance_id'] for r in results if r.get('patch_successfully_applied') is False]
    resolved_list = [r['instance_id'] for r in results if r.get('resolved') is True]
    not_resolved_list = [r['instance_id'] for r in results if r.get('resolved') is False]
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write("实例执行状态分析报告\n")
        f.write("=" * 100 + "\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总实例数: {total}\n")
        f.write("\n")
        
        # 步骤统计
        f.write("=" * 100 + "\n")
        f.write("6个步骤的Patch应用状态统计\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'步骤':<15} {'成功':<10} {'失败':<10} {'未找到':<10} {'总计':<10}\n")
        f.write("-" * 100 + "\n")
        for step, stats in step_stats.items():
            total_step = stats['success'] + stats['failed'] + stats['not_found']
            f.write(f"{step:<15} {stats['success']:<10} {stats['failed']:<10} {stats['not_found']:<10} {total_step:<10}\n")
        
        f.write("\n")
        f.write("=" * 100 + "\n")
        f.write("Patch应用状态统计\n")
        f.write("=" * 100 + "\n")
        f.write(f"Patch成功应用: {patch_applied}\n")
        f.write(f"Patch应用失败: {patch_not_applied}\n")
        f.write(f"Patch状态未知: {patch_unknown}\n")
        f.write("\n")
        
        f.write("=" * 100 + "\n")
        f.write("解决状态统计\n")
        f.write("=" * 100 + "\n")
        f.write(f"已解决: {resolved}\n")
        f.write(f"未解决: {not_resolved}\n")
        f.write(f"解决状态未知: {resolved_unknown}\n")
        f.write("\n")
        
        # 分类实例列表
        f.write("=" * 100 + "\n")
        f.write("分类实例列表\n")
        f.write("=" * 100 + "\n")
        f.write("\n")
        
        f.write(f"Patch成功应用的实例 ({len(patch_applied_list)} 个):\n")
        for instance_id in sorted(patch_applied_list):
            f.write(f"  - {instance_id}\n")
        f.write("\n")
        
        f.write(f"Patch应用失败的实例 ({len(patch_not_applied_list)} 个):\n")
        for instance_id in sorted(patch_not_applied_list):
            f.write(f"  - {instance_id}\n")
        f.write("\n")
        
        f.write(f"已解决的实例 ({len(resolved_list)} 个):\n")
        for instance_id in sorted(resolved_list):
            f.write(f"  - {instance_id}\n")
        f.write("\n")
        
        f.write(f"未解决的实例 ({len(not_resolved_list)} 个):\n")
        for instance_id in sorted(not_resolved_list):
            f.write(f"  - {instance_id}\n")
        f.write("\n")
        
        # 详细列表
        f.write("=" * 100 + "\n")
        f.write("详细实例列表\n")
        f.write("=" * 100 + "\n")
        f.write("\n")
        
        # 按实例ID排序
        sorted_results = sorted(results, key=lambda x: x['instance_id'])
        
        for result in sorted_results:
            f.write(f"\n实例: {result['instance_id']}\n")
            f.write("-" * 100 + "\n")
            
            # 6个步骤的状态
            f.write("6个步骤的Patch应用状态:\n")
            for step in ['pred_pre', 'pred_post', 'gold_pre', 'gold_post', 'base_pre', 'base_post']:
                step_info = result['steps'].get(step, {})
                status = step_info.get('status', '未知')
                patch_applied = step_info.get('patch_applied')
                if patch_applied is True:
                    status_symbol = "✓"
                elif patch_applied is False:
                    status_symbol = "✗"
                else:
                    status_symbol = "?"
                f.write(f"  {step:<15}: {status_symbol} {status}\n")
            
            # 最终状态
            f.write("\n最终状态:\n")
            f.write(f"  Patch存在: {result.get('patch_exists', '未知')}\n")
            f.write(f"  Patch成功应用: {result.get('patch_successfully_applied', '未知')}\n")
            f.write(f"  已解决: {result.get('resolved', '未知')}\n")
            f.write(f"  日志文件存在: {result.get('log_exists', False)}\n")
            f.write(f"  报告文件存在: {result.get('report_exists', False)}\n")
            f.write("\n")


def main():
    base_dir = Path('/Users/lanweifrj/project/swt-bench/run_instance_swt_logs')
    target_dir = base_dir / 'debug_run_251229_0035' / 'anthropic__claude-sonnet-4.5'
    output_file = 'Users/lanweifrj/project/swt-bench/instance_analysis_report_debug_run_251229_0035.txt'
    
    if not target_dir.exists():
        print(f"错误: 目标目录不存在: {target_dir}")
        return
    
    print(f"开始分析实例目录: {target_dir}")
    print(f"输出文件: {output_file}")
    
    results = []
    instance_dirs = sorted([d for d in target_dir.iterdir() if d.is_dir()])
    
    print(f"找到 {len(instance_dirs)} 个实例目录")
    
    for instance_dir in instance_dirs:
        print(f"处理: {instance_dir.name}")
        try:
            result = analyze_instance(instance_dir)
            results.append(result)
        except Exception as e:
            print(f"  错误: 处理 {instance_dir.name} 时出错: {e}")
            results.append({
                'instance_id': instance_dir.name,
                'steps': {},
                'error': str(e)
            })
    
    print(f"\n分析完成，生成报告...")
    generate_summary_report(results, output_file)
    print(f"报告已保存到: {output_file}")


if __name__ == '__main__':
    main()

