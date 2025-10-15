# arXiv 论文信息提取工具

从 arXiv LaTeX 源代码中自动提取论文元数据（标题、作者、机构、邮箱、摘要）。

## 功能特性

- 自动下载和解压 arXiv LaTeX 源代码
- 混合提取策略：API 提取作者信息 + 正则表达式提取摘要
- 断点续传：支持中断后继续
- 多数据集支持：AI、CL、CV、LG 独立处理
- 5层 JSON 解析策略：处理 API 响应异常

## 系统要求

- Python 3.10+
- 依赖：`openai>=1.0.0`, `requests>=2.31.0`
- API 端点：`http://maas-api.cn-huabei-1.xf-yun.com/v1`
- 模型：`xop3qwen1b7`

## 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行提取
```bash
python extract_papers.py
# 选择数据集: 1=test, 2=AI, 3=CL, 4=CV, 5=LG
```

### 清理失败记录（可选）
```bash
python clean_failed_papers.py
# 移除可重试的失败记录（作者为空、下载失败等）
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `extract_papers.py` | 主提取脚本（943行），5层JSON解析策略 |
| `clean_failed_papers.py` | 清理失败记录，移除可重试的条目 |
| `jsonl_to_json.py` | JSONL转JSON格式 |
| `extracted_papers_{suffix}.jsonl` | 成功提取的论文 |
| `failed_papers_{suffix}.jsonl` | 失败的论文及原因 |
| `data/arxiv_deduped_*.json` | 输入数据（AI/CL/CV/LG） |

## 使用指南

### 基本流程

1. 运行 `python extract_papers.py`，选择数据集
2. 查看结果：`wc -l extracted_papers_AI.jsonl failed_papers_AI.jsonl`
3. 清理失败记录：`python clean_failed_papers.py`（移除可重试的失败）
4. 重新运行提取（自动跳过已成功和已失败的）

### 常见失败原因

- `作者为空`：API解析失败（可重试）
- `下载失败`：arXiv源代码不可用（不可重试）
- `解压失败`：文件损坏（不可重试）
- `摘要为空`：未找到摘要（不可重试）

### 断点续传

程序支持中断后继续，使用 `Ctrl+C` 中断后重新运行即可。

## 常见问题

- **API连接失败**：检查网络和API Key
- **大量"作者为空"**：运行 `clean_failed_papers.py` 清理后重试
- **下载速度慢**：arXiv有速率限制，可后台运行 `nohup python extract_papers.py > log.txt 2>&1 &`
- **磁盘空间不足**：程序自动清理临时文件，建议至少10GB空间

## 技术细节

### 5层 JSON 解析策略

1. 移除 Markdown 代码块（```json...```）
2. 智能花括号计数（跟踪字符串状态）
3. 直接 JSON 解析
4. JSON 智能修复（补全引号、括号）
5. 正则表达式兜底提取

### API 响应处理

- 截断检测：检查 `finish_reason` 和 JSON 完整性
- Token 管理：默认4096，重试8192
- 输入优化：清理 LaTeX 注释减少噪音

## 数据格式

### 输入（JSON）
```json
{"id": "2410.11141v1", "title": "...", "tex_source": "https://..."}
```

### 输出（JSONL）
成功记录：
```json
{"id": "...", "title": "...", "authors": [{"name": "...", "email": null, "affiliations": []}], "abstract": "..."}
```

失败记录：
```json
{"id": "...", "failure_reason": "下载失败: 404"}
```

## 处理统计

| 数据集 | 论文数 | 
|--------|--------|
| test | 20 |
| AI | 38,720 | 
| CL | 39,000 | 
| CV | 71,000 |
| LG | 63,000 | 

预期成功率：85-95%

---