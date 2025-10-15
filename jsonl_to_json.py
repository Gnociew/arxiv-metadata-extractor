#!/usr/bin/env python3
"""
将JSONL格式转换为标准JSON格式的工具脚本

JSONL格式：每行一个JSON对象（适合增量写入和大文件处理）
JSON格式：标准的JSON数组格式（更易读）
"""

import json
import sys


def jsonl_to_json(jsonl_file: str, output_file: str):
    """将JSONL文件转换为JSON数组格式"""
    data = []
    
    print(f"读取 {jsonl_file}...")
    with open(jsonl_file, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"警告: 第{i}行解析失败: {e}")
    
    print(f"共读取 {len(data)} 条记录")
    print(f"写入 {output_file}...")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print("✓ 转换完成！")


def main():
    if len(sys.argv) < 2:
        print("用法: python jsonl_to_json.py <input.jsonl> [output.json]")
        print("\n将处理结果转换为标准JSON格式:")
        print("  python jsonl_to_json.py extracted_papers.jsonl extracted_papers.json")
        print("  python jsonl_to_json.py failed_papers.jsonl failed_papers.json")
        return
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace('.jsonl', '.json')
    
    jsonl_to_json(input_file, output_file)


if __name__ == '__main__':
    main()
