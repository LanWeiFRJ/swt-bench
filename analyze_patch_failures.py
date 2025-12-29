#!/usr/bin/env python3
"""
分析patch应用失败的实例，提取失败原因
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime


def analyze_patch_failure(instance_dir: Path) -> Dict[str, any]:
    """
    分析单个实例的patch应用失败原因
    """
    instance_id = instance_dir.name
    test_output_path = instance_dir / 'test_output.txt'
    model_patch_path = instance_dir / 'model_patch.diff'
    
    result = {
        'instance_id': instance_id,
        'failure_reason': '未知',
        'failure_category': '其他',
        'error_details': [],
        'patch_file_exists': model_patch_path.exists(),
        'test_output_exists': test_output_path.exists(),
    }
    
    if not test_output_path.exists():
        result['failure_reason'] = '测试输出文件不存在'
        result['failure_category'] = '文件缺失'
        return result
    
    content = test_output_path.read_text(encoding='utf-8', errors='ignore')
    
    # 分析失败原因
    error_details = []
    
    # 1. Patch应用失败相关错误
    patch_apply_patterns = [
        (r'patch does not apply', 'patch无法应用'),
        (r'patch failed:', 'patch应用失败'),
        (r'error: while searching for:', 'patch上下文不匹配'),
        (r'hunk.*failed', 'hunk应用失败'),
        (r'error:.*\.py: patch does not apply', '文件patch无法应用'),
    ]
    
    for pattern, description in patch_apply_patterns:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            # 提取上下文
            start = max(0, match.start() - 200)
            end = min(len(content), match.end() + 200)
            context = content[start:end]
            error_details.append({
                'type': 'patch_apply',
                'description': description,
                'context': context.replace('\n', ' ')[:300]
            })
            if result['failure_category'] == '其他':
                result['failure_category'] = 'Patch应用失败'
                result['failure_reason'] = description
    
    # 2. Git相关错误
    git_error_patterns = [
        (r'error: unrecognized input', 'git patch格式错误'),
        (r'fatal:.*patch', 'git致命错误'),
        (r'error:.*git', 'git错误'),
    ]
    
    for pattern, description in git_error_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            error_details.append({
                'type': 'git_error',
                'description': description,
                'context': ''
            })
            if result['failure_category'] == '其他':
                result['failure_category'] = 'Git错误'
                result['failure_reason'] = description
    
    # 3. 测试执行错误
    test_error_patterns = [
        (r'unrecognized arguments:', 'pytest参数错误'),
        (r'pytest.*error:', 'pytest执行错误'),
        (r'>>>>> Tests Errored', '测试执行错误'),
        (r'>>>>> Tests Timed Out', '测试超时'),
        (r'>>>>> Reset Failed', '环境重置失败'),
    ]
    
    for pattern, description in test_error_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            error_details.append({
                'type': 'test_error',
                'description': description,
                'context': ''
            })
            if result['failure_category'] == '其他':
                result['failure_category'] = '测试执行错误'
                result['failure_reason'] = description
    
    # 4. 文件不存在错误
    if re.search(r'No such file or directory|FileNotFoundError|文件不存在', content, re.IGNORECASE):
        error_details.append({
            'type': 'file_error',
            'description': '文件不存在',
            'context': ''
        })
        if result['failure_category'] == '其他':
            result['failure_category'] = '文件错误'
            result['failure_reason'] = '文件不存在'
    
    # 5. 语法错误
    if re.search(r'SyntaxError|IndentationError|语法错误', content, re.IGNORECASE):
        error_details.append({
            'type': 'syntax_error',
            'description': '语法错误',
            'context': ''
        })
        if result['failure_category'] == '其他':
            result['failure_category'] = '语法错误'
            result['failure_reason'] = '语法错误'
    
    # 6. 导入错误
    if re.search(r'ImportError|ModuleNotFoundError|导入错误', content, re.IGNORECASE):
        error_details.append({
            'type': 'import_error',
            'description': '导入错误',
            'context': ''
        })
        if result['failure_category'] == '其他':
            result['failure_category'] = '导入错误'
            result['failure_reason'] = '导入错误'
    
    # 提取具体的patch失败文件信息
    patch_failed_files = re.findall(r'error: (.*?): patch (?:does not apply|failed)', content)
    if patch_failed_files:
        result['failed_files'] = list(set(patch_failed_files))
    
    # 提取patch应用命令的输出
    if 'git apply' in content:
        apply_section = content.split('git apply')[1].split('\n+ ')[0] if 'git apply' in content else ''
        if 'error:' in apply_section:
            result['git_apply_output'] = apply_section[:500]
    
    result['error_details'] = error_details
    
    # 如果还没有确定失败原因，尝试从内容中提取
    if result['failure_category'] == '其他':
        # 检查是否有"applied patch"字样
        if 'applied patch' not in content.lower():
            result['failure_reason'] = '未找到patch应用成功的标志'
        else:
            result['failure_reason'] = '未知错误（需要进一步分析）'
    
    return result


def generate_failure_analysis_report(results: List[Dict[str, any]], output_path: Path):
    """
    生成失败原因分析报告
    """
    total = len(results)
    
    # 按失败类别统计
    category_stats = defaultdict(int)
    reason_stats = defaultdict(int)
    
    for result in results:
        category_stats[result['failure_category']] += 1
        reason_stats[result['failure_reason']] += 1
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write("Patch应用失败原因分析报告\n")
        f.write("=" * 100 + "\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总失败实例数: {total}\n")
        f.write("\n")
        
        # 失败类别统计
        f.write("=" * 100 + "\n")
        f.write("失败类别统计\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'失败类别':<30} {'数量':<10} {'百分比':<10}\n")
        f.write("-" * 100 + "\n")
        for category, count in sorted(category_stats.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total * 100) if total > 0 else 0
            f.write(f"{category:<30} {count:<10} {percentage:.2f}%\n")
        f.write("\n")
        
        # 失败原因统计（前20个）
        f.write("=" * 100 + "\n")
        f.write("失败原因统计（前20个）\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'失败原因':<40} {'数量':<10} {'百分比':<10}\n")
        f.write("-" * 100 + "\n")
        for reason, count in sorted(reason_stats.items(), key=lambda x: x[1], reverse=True)[:20]:
            percentage = (count / total * 100) if total > 0 else 0
            f.write(f"{reason:<40} {count:<10} {percentage:.2f}%\n")
        f.write("\n")
        
        # 按类别分组的实例列表
        f.write("=" * 100 + "\n")
        f.write("按失败类别分组的实例列表\n")
        f.write("=" * 100 + "\n")
        f.write("\n")
        
        categories = sorted(set(r['failure_category'] for r in results), 
                          key=lambda x: category_stats[x], reverse=True)
        
        for category in categories:
            category_instances = [r for r in results if r['failure_category'] == category]
            f.write(f"\n{category} ({len(category_instances)} 个实例):\n")
            f.write("-" * 100 + "\n")
            for instance in sorted(category_instances, key=lambda x: x['instance_id']):
                f.write(f"  - {instance['instance_id']}: {instance['failure_reason']}\n")
                if 'failed_files' in instance and instance['failed_files']:
                    f.write(f"    失败文件: {', '.join(instance['failed_files'][:3])}\n")
            f.write("\n")
        
        # 详细实例分析
        f.write("=" * 100 + "\n")
        f.write("详细实例分析\n")
        f.write("=" * 100 + "\n")
        f.write("\n")
        
        sorted_results = sorted(results, key=lambda x: (x['failure_category'], x['instance_id']))
        
        for result in sorted_results:
            f.write(f"\n实例: {result['instance_id']}\n")
            f.write("-" * 100 + "\n")
            f.write(f"失败类别: {result['failure_category']}\n")
            f.write(f"失败原因: {result['failure_reason']}\n")
            f.write(f"Patch文件存在: {result['patch_file_exists']}\n")
            f.write(f"测试输出存在: {result['test_output_exists']}\n")
            
            if 'failed_files' in result and result['failed_files']:
                f.write(f"失败文件: {', '.join(result['failed_files'])}\n")
            
            if result['error_details']:
                f.write(f"错误详情 ({len(result['error_details'])} 个):\n")
                for i, detail in enumerate(result['error_details'][:3], 1):
                    f.write(f"  {i}. {detail['description']}\n")
                    if detail.get('context'):
                        f.write(f"     上下文: {detail['context'][:200]}...\n")
            
            if 'git_apply_output' in result:
                f.write(f"Git apply输出:\n{result['git_apply_output'][:300]}...\n")
            
            f.write("\n")


def main():
    # 读取失败实例列表
    report_file = Path('/Users/lanweifrj/project/swt-bench/run_instance_swt_logs/debug_run_251229_0035/instance_analysis_report.txt')
    
    if not report_file.exists():
        print(f"错误: 报告文件不存在: {report_file}")
        return
    
    # 提取失败实例列表
    failed_instances = []
    with open(report_file, 'r', encoding='utf-8') as f:
        content = f.read()
        # 找到"Patch应用失败的实例"部分
        start_marker = "Patch应用失败的实例"
        end_marker = "已解决的实例"
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx != -1 and end_idx != -1:
            section = content[start_idx:end_idx]
            # 提取实例ID
            for line in section.split('\n'):
                if line.strip().startswith('- '):
                    instance_id = line.strip()[2:].strip()
                    failed_instances.append(instance_id)
    
    print(f"找到 {len(failed_instances)} 个失败实例")
    
    # 分析每个失败实例
    pred_pre_dir = Path('/Users/lanweifrj/project/swt-bench/run_instance_swt_logs/debug_run_251229_0035/pred_pre__anthropic__claude-sonnet-4.5')
    
    results = []
    for instance_id in failed_instances:
        instance_dir = pred_pre_dir / instance_id
        if instance_dir.exists():
            print(f"分析: {instance_id}")
            try:
                result = analyze_patch_failure(instance_dir)
                results.append(result)
            except Exception as e:
                print(f"  错误: 处理 {instance_id} 时出错: {e}")
                results.append({
                    'instance_id': instance_id,
                    'failure_reason': f'分析错误: {str(e)}',
                    'failure_category': '分析错误',
                    'error_details': [],
                })
        else:
            print(f"  警告: 目录不存在: {instance_dir}")
            results.append({
                'instance_id': instance_id,
                'failure_reason': '实例目录不存在',
                'failure_category': '文件缺失',
                'error_details': [],
            })
    
    # 生成报告
    output_file = Path('/Users/lanweifrj/project/swt-bench/patch_failure_analysis_report.txt')
    print(f"\n生成分析报告...")
    generate_failure_analysis_report(results, output_file)
    print(f"报告已保存到: {output_file}")


if __name__ == '__main__':
    main()

