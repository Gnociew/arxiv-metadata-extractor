#!/usr/bin/env python3
"""
清理失败记录,只保留解压失败和摘要为空的记录
其他失败原因的记录会被移除,以便重新运行提取
"""

import json
import os

def clean_failed_papers(failed_file):
    """清理失败记录,只保留解压失败和摘要为空的"""
    
    if not os.path.exists(failed_file):
        print(f"文件不存在: {failed_file}")
        return
    
    # 读取所有失败记录
    all_failed = []
    with open(failed_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                    all_failed.append(record)
                except json.JSONDecodeError as e:
                    print(f"警告: 跳过无效JSON行: {e}")
                    continue
    
    print(f"总失败记录数: {len(all_failed)}")
    
    # 统计各种失败原因
    failure_stats = {}
    for record in all_failed:
        reason = record.get('failure_reason', 'unknown')
        failure_stats[reason] = failure_stats.get(reason, 0) + 1
    
    print("\n失败原因统计:")
    for reason, count in sorted(failure_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  {reason}: {count} 篇")
    
    # 只保留解压失败和摘要为空的记录
    kept_records = []
    removed_records = []
    
    for record in all_failed:
        reason = record.get('failure_reason', '')
        
        # 保留条件:
        # 1. 解压失败 (包含 "解压失败" 或 "not a gzip file" 或 "gzip" 或 "tar")
        # 2. 摘要为空 (包含 "摘要为空")
        if ('解压失败' in reason or 
            'not a gzip file' in reason or
            'gzip' in reason.lower() or
            'tar' in reason.lower() or
            '摘要为空' in reason):
            kept_records.append(record)
        else:
            removed_records.append(record)
    
    print(f"\n保留记录: {len(kept_records)} 篇")
    print(f"移除记录: {len(removed_records)} 篇")
    
    # 显示移除的记录原因
    if removed_records:
        print("\n移除的记录原因统计:")
        removed_stats = {}
        for record in removed_records:
            reason = record.get('failure_reason', 'unknown')
            removed_stats[reason] = removed_stats.get(reason, 0) + 1
        
        for reason, count in sorted(removed_stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason}: {count} 篇")
    
    # 备份原文件
    backup_file = failed_file + '.backup'
    if os.path.exists(backup_file):
        print(f"\n警告: 备份文件已存在,将覆盖: {backup_file}")
    
    os.rename(failed_file, backup_file)
    print(f"\n原文件已备份到: {backup_file}")
    
    # 写入清理后的记录
    with open(failed_file, 'w', encoding='utf-8') as f:
        for record in kept_records:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    print(f"清理后的文件已保存: {failed_file}")
    print(f"\n✓ 完成! 现在可以重新运行 extract_papers.py")


def main():
    """主函数"""
    # 处理所有失败文件
    failed_files = [
        'failed_papers_test.jsonl',
        'failed_papers_AI.jsonl',
        'failed_papers_CL.jsonl',
        'failed_papers_CV.jsonl',
        'failed_papers_LG.jsonl'
    ]
    
    print("失败记录清理工具")
    print("=" * 60)
    print("将移除以下失败原因的记录:")
    print("  - 作者为空")
    print("  - 下载失败")
    print("  - 404错误")
    print("  - 网络错误")
    print("  - 其他非解压/摘要问题")
    print("\n将保留以下失败原因的记录:")
    print("  - 解压失败 (not a gzip file 等)")
    print("  - 摘要为空")
    print("=" * 60)
    
    # 询问用户要处理哪个文件
    print("\n请选择要清理的文件:")
    for i, file in enumerate(failed_files, 1):
        exists = "✓" if os.path.exists(file) else "✗"
        print(f"{i}. {file} {exists}")
    print("6. 清理所有存在的文件")
    
    choice = input("\n请输入选择 (1-6): ").strip()
    
    if choice == '6':
        # 清理所有存在的文件
        for file in failed_files:
            if os.path.exists(file):
                print(f"\n{'='*60}")
                print(f"处理: {file}")
                print('='*60)
                clean_failed_papers(file)
    elif choice in ['1', '2', '3', '4', '5']:
        idx = int(choice) - 1
        file = failed_files[idx]
        print(f"\n{'='*60}")
        print(f"处理: {file}")
        print('='*60)
        clean_failed_papers(file)
    else:
        print("无效选择")


if __name__ == "__main__":
    main()
