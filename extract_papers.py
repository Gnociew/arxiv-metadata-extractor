from openai import OpenAI
import json
import re
import os
import tarfile
import tempfile
import requests
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# API配置
API_KEY  = "****************"
API_BASE = "****************"
MODEL_ID = "****************"

client = OpenAI(api_key=API_KEY, base_url=API_BASE)

# Schema定义 - 只提取作者信息
SCHEMA_EXAMPLE = {
    "authors": [
        {
            "name": "string",
            "email": "string (可为空字符串)",
            "affiliations": ["string", "..."]
        }
    ]
}

SYSTEM_PROMPT =  f"""你是一个信息抽取助手。现在给你一段 LaTeX 论文片段，
请从可能出现的 \\author、\\affil、\\thanks、脚注、邮箱域名模式中提取作者列表，
并严格按照如下 JSON Schema 输出（不要多字段，不要解释）：
{json.dumps(SCHEMA_EXAMPLE, ensure_ascii=False, indent=2)}

规则：
- 尽可能匹配 'author', 'affiliation', 'email' 等常见 LaTeX 写法（如 \\author{{}}, \\affil{{}}, \\thanks{{... email ...}}）。
- 也要考虑 `\\IEEEauthorblockN`, `\\IEEEauthorblockA` 等 IEEE 模板写法。
- 提取邮箱时，建议先用正则表达式搜索文本中所有包含'@'符号的位置，这能帮助你快速定位邮箱信息。
- 邮箱可能出现在 \\email{{}} 命令、\\thanks{{}} 脚注、或直接写在文本中。
- 一个作者可能对应多个机构；将其放入 'affiliations' 数组。
- 只输出 JSON 对象，不要额外文本。

邮箱特殊格式处理：
1. 花括号简写格式：\texttt{{\{{user1, user2\}}@domain.com}}
   需拆分为：user1@domain.com, user2@domain.com
   
2. LaTeX共享邮箱：\\email{{\{{paulohdscoelho,raularaju,luisfeliperamos\}} @dcc.ufmg.br}}
   说明：花括号内多个用户名共用一个域名，注意可能有空格
   需展开为：paulohdscoelho@dcc.ufmg.br, raularaju@dcc.ufmg.br, luisfeliperamos@dcc.ufmg.br
   
3. 占位符模板：作者名为 Rithesh Kumar，邮箱写 first.last@domain.com
   说明："first"和"last"是占位符，需用作者的名和姓替换（转小写，用点号连接）
   结果为：rithesh.kumar@domain.com

"""

# 邮箱特例：
# 1. 输入：假设作者名为 Rithesh Kumar，文中只写了邮箱模板 first.last@domain.com
#    说明：这种情况下 "first" 和 "last" 是占位符，需要用作者的名和姓替换
#    处理方式：将作者名字转为小写，用点号连接
#    最终邮箱：rithesh.kumar@domain.com (根据实际作者与域名填写)
# 2. 输入：\\email{{\{{user1,user2,user3\}} @domain.com}} 或 \\email{{\{{user1, user2, user3\}}@domain.com}}
#    说明：这是LaTeX中的共享邮箱格式，花括号内的多个用户名共用一个域名，注意可能有空格
#    处理方式：展开为每个用户的独立邮箱
#    例如：\\email{{\{{paulohdscoelho,raularaju,luisfeliperamos\}} @dcc.ufmg.br}}
#    应拆分为：paulohdscoelho@dcc.ufmg.br, raularaju@dcc.ufmg.br, luisfeliperamos@dcc.ufmg.br



def download_and_extract_tar(url: str, extract_dir: str, max_retries: int = 3) -> Tuple[bool, str]:
    """下载并解压tar.gz文件，支持重试
    
    Returns:
        (success, error_message): 成功返回(True, ""), 失败返回(False, 错误信息)
    """
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"  重试 ({attempt}/{max_retries})...")
                time.sleep(1)  # 等待1秒后重试
            
            print(f"  下载: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            tar_path = os.path.join(extract_dir, "paper.tar.gz")
            with open(tar_path, 'wb') as f:
                f.write(response.content)
            
            print(f"  解压到: {extract_dir}")
            with tarfile.open(tar_path, 'r:gz') as tar:
                tar.extractall(extract_dir)
            
            os.remove(tar_path)
            return True, ""
            
        except requests.RequestException as e:
            error_msg = f"下载失败: {str(e)}"
            print(f"  ❌ {error_msg}")
            if attempt == max_retries:
                return False, error_msg
        except tarfile.TarError as e:
            error_msg = f"解压失败: {str(e)}"
            print(f"  ❌ {error_msg}")
            if attempt == max_retries:
                return False, error_msg
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            print(f"  ❌ {error_msg}")
            if attempt == max_retries:
                return False, error_msg
    
    return False, "下载失败（超过最大重试次数）"


def find_main_tex_file(extract_dir: str) -> Optional[str]:
    """找到主要的.tex文件"""
    tex_files = list(Path(extract_dir).rglob("*.tex"))
    
    if not tex_files:
        return None
    
    # 优先级：包含documentclass的文件，或者最大的文件
    main_candidates = []
    for tex_file in tex_files:
        try:
            content = tex_file.read_text(encoding='utf-8', errors='ignore')
            if r'\documentclass' in content:
                main_candidates.append((tex_file, len(content)))
        except:
            continue
    
    if main_candidates:
        # 返回包含documentclass的最大文件
        return str(sorted(main_candidates, key=lambda x: x[1], reverse=True)[0][0])
    
    # 如果没有找到documentclass，返回最大的tex文件
    return str(max(tex_files, key=lambda f: f.stat().st_size))


def remove_latex_comments(text: str) -> str:
    """移除LaTeX中的注释内容
    
    包括：
    1. % 行注释
    2. \iffalse...\fi 块注释
    3. \begin{comment}...\end{comment} 注释环境
    """
    # 移除 % 行注释（但不移除 \% 转义的百分号）
    text = re.sub(r'(?<!\\)%.*', '', text)
    
    # 移除 \iffalse...\fi 块（条件编译块）
    text = re.sub(r'\\iffalse.*?\\fi', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 \begin{comment}...\end{comment} 注释环境
    text = re.sub(r'\\begin\{comment\}.*?\\end\{comment\}', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    return text


def clean_latex_text(text: str) -> str:
    """清理LaTeX文本中的命令"""
    # 移除常见的LaTeX命令
    text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def extract_nested_braces(content: str, start_pos: int) -> tuple:
    """从指定位置提取嵌套的大括号内容"""
    depth = 0
    result = []
    i = start_pos
    
    while i < len(content):
        char = content[i]
        if char == '{':
            depth += 1
            if depth > 1:
                result.append(char)
        elif char == '}':
            depth -= 1
            if depth == 0:
                return ''.join(result), i
            result.append(char)
        else:
            if depth > 0:
                result.append(char)
        i += 1
    
    return ''.join(result), i


def extract_abstract(content: str, tex_dir: str = None) -> Optional[str]:
    """从 TeX 内容中提取摘要，支持多种格式"""
    # 移除所有注释（行注释和块注释）
    content = remove_latex_comments(content)
    
    # 方式1: \begin{abstract}...\end{abstract}
    abstract_pattern = r'\\begin\{abstract\}(.*?)\\end\{abstract\}'
    match = re.search(abstract_pattern, content, re.IGNORECASE | re.DOTALL)
    
    if match:
        abstract = match.group(1)
        abstract = clean_latex_text(abstract)
        return abstract.strip()
    
    # 方式2: \abstract{...}
    abstract_cmd_match = re.search(r'\\abstract\s*\{', content, re.IGNORECASE)
    if abstract_cmd_match:
        abstract_content, _ = extract_nested_braces(content, abstract_cmd_match.end() - 1)
        if abstract_content:
            abstract = clean_latex_text(abstract_content)
            return abstract.strip()
    
    # 方式3: 摘要在单独的文件中，通过 \input 或 \include 引入
    if tex_dir:
        # 首先查找明确包含 abstract 的文件
        input_patterns = [
            r'\\input\{([^}]*abstract[^}]*)\}',
            r'\\include\{([^}]*abstract[^}]*)\}'
        ]
        
        for pattern in input_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for input_file in matches:
                # 尝试添加 .tex 后缀
                possible_paths = [
                    os.path.join(tex_dir, input_file),
                    os.path.join(tex_dir, f"{input_file}.tex")
                ]
                
                for file_path in possible_paths:
                    if os.path.exists(file_path):
                        try:
                            print(f"    检查引入文件: {os.path.basename(file_path)}")
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                input_content = f.read()
                            # 递归提取摘要（不再传递tex_dir避免无限递归）
                            abstract = extract_abstract(input_content, tex_dir=None)
                            if abstract:
                                print(f"    ✓ 在 {os.path.basename(file_path)} 中找到摘要")
                                return abstract
                        except Exception as e:
                            print(f"    读取输入文件失败 {file_path}: {e}")
                            continue
        
        # 方式4: 如果没有找到，尝试搜索所有引入的文件（如 introduction, intro 等）
        # 这些文件可能在开头包含摘要
        general_patterns = [
            r'\\input\{([^}]*)\}',
            r'\\include\{([^}]*)\}'
        ]
        
        candidate_files = []
        for pattern in general_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            candidate_files.extend(matches)
        
        # 优先级：introduction, intro 等可能包含摘要的文件
        priority_keywords = ['introduction', 'intro', 'main', '00_', '01_']
        
        # 按优先级排序
        def get_priority(filename):
            filename_lower = filename.lower()
            for i, keyword in enumerate(priority_keywords):
                if keyword in filename_lower:
                    return i
            return len(priority_keywords)
        
        candidate_files.sort(key=get_priority)
        
        for input_file in candidate_files:
            # 跳过已经检查过的 abstract 文件
            if 'abstract' in input_file.lower():
                continue
            
            possible_paths = [
                os.path.join(tex_dir, input_file),
                os.path.join(tex_dir, f"{input_file}.tex")
            ]
            
            for file_path in possible_paths:
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            input_content = f.read()
                        # 只在文件开头查找摘要（前3000字符）
                        abstract = extract_abstract(input_content[:3000], tex_dir=None)
                        if abstract:
                            print(f"    ✓ 在 {os.path.basename(file_path)} 中找到摘要")
                            return abstract
                    except Exception as e:
                        continue
    
    return None


def clean_preamble_for_extraction(text: str) -> str:
    """清理前文，移除无用的LaTeX配置，只保留标题、作者等核心信息
    
    采用逐行清理策略，避免跨行的贪婪匹配删除太多内容
    """
    lines = []
    skip_until_brace = 0  # 跟踪需要跳过的花括号嵌套
    
    for line in text.split('\n'):
        stripped = line.strip()
        
        # 如果正在跳过多行命令的内容
        if skip_until_brace > 0:
            skip_until_brace += line.count('{') - line.count('}')
            if skip_until_brace <= 0:
                skip_until_brace = 0
            continue
        
        # 检查是否是需要完全跳过的配置行
        if any(stripped.startswith(cmd) for cmd in [
            '\\documentclass',
            '\\usepackage',
            '\\pdfpagewidth',
            '\\pdfpageheight',
            '\\typeout',
            '\\urlstyle',
            '\\setcopyright',
            '\\copyrightyear',
            '\\acmYear',
            '\\acmDOI',
            '\\acmISBN',
        ]):
            # 检查是否有未闭合的花括号（多行命令）
            open_braces = line.count('{')
            close_braces = line.count('}')
            if open_braces > close_braces:
                skip_until_brace = open_braces - close_braces
            continue
        
        # 检查是否是单行的配置命令（整行删除）
        if any(re.match(pattern, stripped) for pattern in [
            r'\\newcommand\{.*?\}.*',
            r'\\renewcommand\{.*?\}.*',
            r'\\newtheorem\{.*?\}.*',
            r'\\definecolor\{.*?\}.*',
            r'\\def\\.*',
            r'\\lstset\{.*',
            r'\\tcbuselibrary\{.*',
        ]):
            # 检查是否有未闭合的花括号（多行命令）
            open_braces = line.count('{')
            close_braces = line.count('}')
            if open_braces > close_braces:
                skip_until_brace = open_braces - close_braces
            continue
        
        # 特殊处理 \pdfinfo 和 \acmConference（可能跨多行）
        if '\\pdfinfo{' in line or '\\acmConference' in line:
            open_braces = line.count('{')
            close_braces = line.count('}')
            if open_braces > close_braces:
                skip_until_brace = open_braces - close_braces
            continue
        
        # 保留这一行
        lines.append(line)
    
    result = '\n'.join(lines)
    
    # 移除多余的空白行
    result = re.sub(r'\n\s*\n+', '\n\n', result)
    
    return result.strip()


def extract_preamble(tex_content: str, max_chars: int = 2000) -> str:
    """提取LaTeX文件的前文部分（标题、作者等信息通常在这里）
    
    优化版本：只提取核心的标题、作者、机构、邮箱信息，移除无用的配置
    
    策略：
    1. 优先查找到 \begin{abstract} 或 \maketitle 之前的内容（通常包含作者信息）
    2. 如果没找到，查找到 \begin{document} 后的一定范围（有些论文把作者信息放在 \begin{document} 之后）
    3. 最后才查找到 \section 之前
    """
    # 统一移除所有注释
    cleaned = remove_latex_comments(tex_content)
    
    # 尝试找到合适的结束标记
    preamble = None
    
    # 策略1: 查找到 \begin{abstract} 或 \maketitle（最可靠）
    for pattern in [r'(.*?\\begin\{abstract\})', r'(.*?\\maketitle)']:
        match = re.search(pattern, cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            preamble = match.group(1)
            break
    
    # 策略2: 如果没找到，查找 \begin{document} 及其后的内容
    if not preamble:
        match = re.search(r'\\begin\{document\}', cleaned, re.IGNORECASE)
        if match:
            # 从文档开始取 max_chars*3 字符（包含 \begin{document} 前后）
            start_pos = max(0, match.start() - 500)  # 前面留500字符
            end_pos = match.end() + max_chars * 2  # 后面取更多字符
            preamble = cleaned[start_pos:end_pos]
        else:
            # 策略3: 查找到第一个 \section
            match = re.search(r'(.*?\\section)', cleaned, re.DOTALL | re.IGNORECASE)
            if match:
                preamble = match.group(1)
            else:
                # 最后的兜底：使用前面的内容
                preamble = cleaned[:max_chars * 3]
    
    # 清理无用信息
    preamble = clean_preamble_for_extraction(preamble)
    
    # 限制最大长度
    return preamble[:max_chars]


def extract_authors_by_regex(content: str) -> List[Dict]:
    """使用正则表达式从API返回的文本中提取作者信息
    
    当JSON解析失败时使用,直接匹配字段名称
    """
    authors = []
    
    # 尝试找到所有的 "name": "..." 模式
    # 使用更宽松的匹配,处理换行和空格
    name_pattern = r'"name"\s*:\s*"([^"]*)"'
    email_pattern = r'"email"\s*:\s*"([^"]*)"'
    affiliation_pattern = r'"affiliations"\s*:\s*\[(.*?)\]'
    
    # 首先找到所有的name
    names = re.findall(name_pattern, content, re.DOTALL)
    
    if not names:
        return []
    
    # 尝试分段处理,为每个作者构建完整信息
    # 按 "name" 字段分割内容
    parts = re.split(r'"name"\s*:\s*"', content)
    
    for i, part in enumerate(parts[1:], 1):  # 跳过第一个空部分
        author = {}
        
        # 提取name (已经分割了,part的开头就是name值)
        name_match = re.match(r'([^"]*)"', part)
        if name_match:
            author['name'] = name_match.group(1).strip()
        else:
            continue
        
        # 在这个part中查找email
        email_match = re.search(email_pattern, part)
        if email_match:
            email_value = email_match.group(1).strip()
            author['email'] = email_value if email_value else None
        else:
            author['email'] = None
        
        # 在这个part中查找affiliations
        affil_match = re.search(affiliation_pattern, part, re.DOTALL)
        if affil_match:
            affil_content = affil_match.group(1)
            # 提取所有引号中的字符串
            affiliations = re.findall(r'"([^"]*)"', affil_content)
            author['affiliations'] = [a.strip() for a in affiliations if a.strip()]
        else:
            author['affiliations'] = []
        
        authors.append(author)
    
    return authors


def normalize_author_fields(authors: List[Dict]) -> List[Dict]:
    """规范化作者字段，确保每个作者都有 name, email, affiliations 字段
    
    Args:
        authors: 从API获取的作者列表
        
    Returns:
        规范化后的作者列表，每个作者都有完整的字段
    """
    normalized = []
    for author in authors:
        normalized_author = {
            "name": author.get("name", ""),
            "email": author.get("email") if author.get("email") else None,  # 没有邮箱时设为 None
            "affiliations": author.get("affiliations", [])
        }
        # 确保 affiliations 是列表
        if not isinstance(normalized_author["affiliations"], list):
            normalized_author["affiliations"] = [normalized_author["affiliations"]] if normalized_author["affiliations"] else []
        
        normalized.append(normalized_author)
    
    return normalized


def extract_authors_with_api(tex_preamble: str) -> List[Dict]:
    """使用API提取作者信息"""
    cleaned = re.sub(r"\s+", " ", tex_preamble).strip()
    
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": cleaned}
            ],
            temperature=0,
            max_tokens=4096  # 增加到4096,避免截断
        )
        
        # 检查是否被截断
        choice = resp.choices[0]
        content = choice.message.content.strip()
        
        # 检查finish_reason,如果是length说明被截断了
        if choice.finish_reason == 'length':
            print(f"  ⚠ 警告: API响应被截断(达到max_tokens限制)")
            print(f"  响应长度: {len(content)} 字符")
            # 尝试使用更大的max_tokens重试一次
            print(f"  正在重试,使用更大的token限制...")
            resp = client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": cleaned}
                ],
                temperature=0,
                max_tokens=8192  # 重试时使用8192
            )
            content = resp.choices[0].message.content.strip()
            print(f"  重试后响应长度: {len(content)} 字符")
        
        # 额外检查: JSON完整性检查
        # 检查是否以}结尾(完整的JSON对象应该以}结尾)
        if content and not content.rstrip().endswith('}'):
            print(f"  ⚠ 检测到响应不完整(不以}}结尾)")
            print(f"  响应最后50字符: ...{content[-50:]}")
            print(f"  正在重试...")
            # 重试一次
            resp = client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": cleaned}
                ],
                temperature=0,
                max_tokens=8192
            )
            content = resp.choices[0].message.content.strip()
            print(f"  重试后响应长度: {len(content)} 字符,以{content[-10:]}结尾")
        
        # 调试：打印API返回的原始内容（仅在出错时）
        # print(f"  API返回: {content[:200]}...")
        
        # 策略1: 尝试移除markdown代码块标记
        if '```json' in content or '```' in content:
            # 提取代码块中的内容
            code_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
            if code_match:
                content = code_match.group(1).strip()
        
        # 策略2: 尝试提取第一个完整的JSON对象（使用更精确的模式）
        # 找到第一个 { 的位置
        start_idx = content.find('{')
        if start_idx != -1:
            # 从这个位置开始,找到匹配的 }
            brace_count = 0
            in_string = False
            escape_next = False
            
            for i in range(start_idx, len(content)):
                char = content[i]
                
                # 处理转义字符
                if escape_next:
                    escape_next = False
                    continue
                
                if char == '\\':
                    escape_next = True
                    continue
                
                # 处理字符串边界
                if char == '"':
                    in_string = not in_string
                    continue
                
                # 只在字符串外计数花括号
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # 找到完整的JSON对象
                            json_str = content[start_idx:i+1]
                            
                            # 尝试解析JSON
                            try:
                                result = json.loads(json_str)
                            except json.JSONDecodeError as je:
                                # 解析失败,可能是其他问题,继续到策略3
                                print(f"  策略2 JSON解析失败: {je}")
                                break
                            
                            # 检查是否是单个作者对象(没有authors字段)
                            if 'name' in result and 'authors' not in result:
                                print(f"  - 检测到单个作者对象,转换为authors数组")
                                authors = [result]
                            else:
                                authors = result.get('authors', [])
                            
                            print(f"  - 解析JSON成功,原始authors数量: {len(authors)}")
                            if not authors:
                                print(f"  ⚠ 警告: API返回的authors字段为空列表或不存在")
                                print(f"  完整JSON长度: {len(json_str)} 字符")
                                print(f"  JSON的keys: {list(result.keys())}")
                            
                            normalized = normalize_author_fields(authors)
                            if normalized:
                                print(f"  ✓ API成功提取 {len(normalized)} 位作者")
                            else:
                                print(f"  ⚠ normalize后结果为空")
                            return normalized
        
        # 策略3: 直接解析完整内容（如果策略2失败）
        try:
            result = json.loads(content)
        except json.JSONDecodeError as je:
            print(f"  策略3 JSON解析失败: {je}")
            print(f"  内容长度: {len(content)} 字符")
            
            # 策略4: 尝试修复不完整的JSON
            # 检查是否是因为截断导致的不完整
            if 'Expecting' in str(je):
                print(f"  尝试修复不完整的JSON...")
                fixed_content = content.rstrip()
                
                # 检查字符串引号是否配对
                # 计算引号数量(跳过转义的引号)
                quote_count = 0
                i = 0
                while i < len(fixed_content):
                    if fixed_content[i] == '\\' and i + 1 < len(fixed_content):
                        i += 2  # 跳过转义字符
                        continue
                    if fixed_content[i] == '"':
                        quote_count += 1
                    i += 1
                
                # 如果引号数量是奇数,说明有未闭合的字符串
                if quote_count % 2 == 1:
                    print(f"  检测到未闭合的字符串(引号数量为奇数: {quote_count})")
                    fixed_content += '"'  # 补充闭合引号
                    quote_count += 1
                
                # 计算需要补充的括号
                open_braces = fixed_content.count('{')
                close_braces = fixed_content.count('}')
                open_brackets = fixed_content.count('[')
                close_brackets = fixed_content.count(']')
                
                # 补充缺失的括号
                if open_brackets > close_brackets:
                    fixed_content += '\n' + '      ]' * (open_brackets - close_brackets)
                if open_braces > close_braces:
                    fixed_content += '\n' + '  }' * (open_braces - close_braces)
                
                print(f"  补充了 {1 if quote_count % 2 == 0 and quote_count > 0 else 0} 个引号, {open_brackets - close_brackets} 个], {open_braces - close_braces} 个}}")
                
                try:
                    result = json.loads(fixed_content)
                    print(f"  ✓ 修复成功!")
                except json.JSONDecodeError as je2:
                    print(f"  修复失败: {je2}")
                    print(f"  尝试策略5: 正则表达式逐行匹配...")
                    # 策略5: 使用正则表达式直接提取字段
                    authors = extract_authors_by_regex(content)
                    if authors:
                        print(f"  ✓ 正则表达式提取成功,找到 {len(authors)} 位作者")
                        return normalize_author_fields(authors)
                    else:
                        print(f"  正则表达式提取也失败")
                        print(f"  内容前300字符: {content[:300]}")
                        print(f"  内容后100字符: ...{content[-100:]}")
                        return []
            else:
                print(f"  尝试策略5: 正则表达式逐行匹配...")
                # 策略5: 使用正则表达式直接提取字段
                authors = extract_authors_by_regex(content)
                if authors:
                    print(f"  ✓ 正则表达式提取成功,找到 {len(authors)} 位作者")
                    return normalize_author_fields(authors)
                else:
                    print(f"  内容前300字符: {content[:300]}")
                    print(f"  内容后100字符: ...{content[-100:]}")
                    return []
        
        # 检查是否是单个作者对象
        if 'name' in result and 'authors' not in result:
            print(f"  - 策略3检测到单个作者对象,转换为authors数组")
            authors = [result]
        else:
            authors = result.get('authors', [])
        
        print(f"  - 策略3解析JSON成功,原始authors数量: {len(authors)}")
        if not authors:
            print(f"  ⚠ 警告: API返回的authors字段为空列表或不存在")
            print(f"  JSON的keys: {list(result.keys())}")
        
        normalized = normalize_author_fields(authors)
        if normalized:
            print(f"  ✓ API成功提取 {len(normalized)} 位作者")
        else:
            print(f"  ⚠ normalize后authors为空,但原始有 {len(authors)} 个")
        return normalized
            
    except Exception as e:
        print(f"  API调用失败: {e}")
        return []


def process_paper(paper_info: Dict, temp_dir: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """处理单篇论文
    
    Returns:
        (success_result, failed_result): 
        - 成功返回 (结果字典, None)
        - 失败返回 (None, 失败记录字典)
    """
    print(f"\n处理: {paper_info['title'][:50]}...")
    
    try:
        # 创建临时目录
        paper_dir = os.path.join(temp_dir, paper_info['url'].split('/')[-1])
        os.makedirs(paper_dir, exist_ok=True)
        
        # 下载并解压（带重试）
        success, error_msg = download_and_extract_tar(paper_info['tex_source'], paper_dir)
        if not success:
            failed_record = {
                "title": paper_info["title"],
                "url": paper_info["url"],
                "tex_source": paper_info["tex_source"],
                "category": paper_info["category"],
                "failure_reason": f"下载失败: {error_msg}"
            }
            return None, failed_record
        
        # 找到主tex文件
        main_tex = find_main_tex_file(paper_dir)
        if not main_tex:
            print("  ❌ 未找到.tex文件")
            failed_record = {
                "title": paper_info["title"],
                "url": paper_info["url"],
                "tex_source": paper_info["tex_source"],
                "category": paper_info["category"],
                "failure_reason": "未找到主.tex文件"
            }
            return None, failed_record
        
        print(f"  主文件: {os.path.basename(main_tex)}")
        
        # 读取tex内容
        with open(main_tex, 'r', encoding='utf-8', errors='ignore') as f:
            tex_content = f.read()
        
        # 提取摘要（正则），传递目录以便查找引入的文件
        tex_dir = os.path.dirname(main_tex)
        abstract = extract_abstract(tex_content, tex_dir)
        
        # 提取前文部分
        preamble = extract_preamble(tex_content)
        
        # 使用API提取作者
        print("  调用API提取作者...")
        authors = extract_authors_with_api(preamble)
        
        # 检查提取结果
        failure_reasons = []
        
        if not abstract:
            print("  ⚠ 未找到摘要")
            failure_reasons.append("摘要为空")
        else:
            print(f"  ✓ 摘要提取: {len(abstract)} 字符")
        
        if not authors or len(authors) == 0:
            print("  ⚠ 未提取到作者")
            failure_reasons.append("作者为空")
        else:
            print(f"  ✓ 作者数: {len(authors)}")
        
        # 如果有失败项，记录到失败文件
        if failure_reasons:
            failed_record = {
                "title": paper_info["title"],
                "url": paper_info["url"],
                "tex_source": paper_info["tex_source"],
                "category": paper_info["category"],
                "failure_reason": "; ".join(failure_reasons)
            }
            return None, failed_record
        
        # 成功提取，构建结果
        result = {
            "title": paper_info["title"],
            "authors": authors,
            "abstract": abstract,
            "category": paper_info["category"],
            "pdf_url": paper_info["url"],
            "tex_source": paper_info["tex_source"]
        }
        
        return result, None
        
    except Exception as e:
        print(f"  ❌ 处理失败: {e}")
        failed_record = {
            "title": paper_info["title"],
            "url": paper_info["url"],
            "tex_source": paper_info["tex_source"],
            "category": paper_info["category"],
            "failure_reason": f"处理异常: {str(e)}"
        }
        return None, failed_record


def load_processed_ids(success_file: str, failed_file: str) -> set:
    """加载已处理的论文ID（用于断点续传）"""
    processed = set()
    
    # 从成功文件加载
    if os.path.exists(success_file):
        try:
            with open(success_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        processed.add(data.get('tex_source', ''))
        except Exception as e:
            print(f"警告: 读取成功记录失败: {e}")
    
    # 从失败文件加载
    if os.path.exists(failed_file):
        try:
            with open(failed_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        processed.add(data.get('tex_source', ''))
        except Exception as e:
            print(f"警告: 读取失败记录失败: {e}")
    
    return processed


def append_to_file(filename: str, data: dict):
    """追加一条记录到文件（JSONL格式：每行一个JSON）"""
    with open(filename, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def main():
    """主函数 - 支持断点续传和增量写入"""
    # 询问用户选择输入文件
    print("请选择输入文件:")
    print("1. data/test.json (20篇测试)")
    print("2. data/arxiv_deduped_AI.json (AI分类)")
    print("3. data/arxiv_deduped_CL.json (CL分类)")
    print("4. data/arxiv_deduped_CV.json (CV分类)")
    print("5. data/arxiv_deduped_LG.json (LG分类)")
    choice = input("请输入选择 (1-5，默认1): ").strip() or '1'
    
    file_map = {
        '1': ('data/test.json', 'test'),
        '2': ('data/arxiv_deduped_AI.json', 'AI'),
        '3': ('data/arxiv_deduped_CL.json', 'CL'),
        '4': ('data/arxiv_deduped_CV.json', 'CV'),
        '5': ('data/arxiv_deduped_LG.json', 'LG')
    }
    
    if choice in file_map:
        input_file, suffix = file_map[choice]
    else:
        print("无效选择，使用默认: test.json")
        input_file, suffix = 'test.json', 'test'
    
    # 根据输入文件生成对应的输出文件（带后缀）
    output_success = f'extracted_papers_{suffix}.jsonl'
    output_failed = f'failed_papers_{suffix}.jsonl'
    
    print(f"输出文件:")
    print(f"  - 成功: {output_success}")
    print(f"  - 失败: {output_failed}")
    
    # 读取数据
    print(f"\n正在读取 {input_file}...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 根据文件类型提取论文列表
    if 'test.json' in input_file:
        papers = data.get('cs.AI', [])
    elif 'AI.json' in input_file:
        papers = data.get('cs.AI', [])
    elif 'CL.json' in input_file:
        papers = data.get('cs.CL', [])
    elif 'CV.json' in input_file:
        papers = data.get('cs.CV', [])
    elif 'LG.json' in input_file:
        papers = data.get('cs.LG', [])
    else:
        papers = []
    print(f"共有 {len(papers)} 篇论文待处理")
    
    # 加载已处理的论文ID（断点续传）
    processed_ids = load_processed_ids(output_success, output_failed)
    if processed_ids:
        print(f"检测到已处理 {len(processed_ids)} 篇，将跳过这些论文")
    
    # 统计信息
    skipped_count = 0
    new_success = 0
    new_failed = 0
    start_time = time.time()
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        for i, paper in enumerate(papers, 1):
            tex_source = paper.get('tex_source', '')
            title = paper.get('title', 'unknown')
            
            # 跳过已处理的
            if tex_source in processed_ids:
                skipped_count += 1
                if i % 100 == 0:  # 每100条显示一次进度
                    elapsed = time.time() - start_time
                    rate = i / elapsed if elapsed > 0 else 0
                    print(f"[{i}/{len(papers)}] 跳过已处理, 速度: {rate:.1f}篇/秒")
                continue
            
            # 显示当前处理的论文
            print(f"\n{'='*60}")
            print(f"[{i}/{len(papers)}] {title[:50]}...")
            
            success_result, failed_result = process_paper(paper, temp_dir)
            
            # 立即写入结果（增量写入）
            if success_result:
                append_to_file(output_success, success_result)
                new_success += 1
                processed_ids.add(tex_source)
                print("  ✅ 成功")
            
            if failed_result:
                append_to_file(output_failed, failed_result)
                new_failed += 1
                processed_ids.add(tex_source)
                print(f"  ❌ 失败: {failed_result['failure_reason']}")
            
            # 定期显示进度报告
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                remaining = (len(papers) - i) / rate if rate > 0 else 0
                print(f"\n--- 进度报告 ---")
                print(f"已处理: {i}/{len(papers)} ({i*100//len(papers)}%)")
                print(f"跳过: {skipped_count}, 新增成功: {new_success}, 新增失败: {new_failed}")
                print(f"速度: {rate:.1f}篇/秒, 预计剩余: {remaining/60:.1f}分钟")
    
    # 最终统计
    elapsed_total = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"处理完成！")
    print(f"{'='*60}")
    print(f"总论文数: {len(papers)}")
    print(f"跳过（已处理）: {skipped_count}")
    print(f"新增成功: {new_success}")
    print(f"新增失败: {new_failed}")
    print(f"总耗时: {elapsed_total/60:.1f}分钟")
    print(f"平均速度: {len(papers)/elapsed_total:.1f}篇/秒")
    print(f"\n结果文件 (JSONL格式):")
    print(f"  成功: {output_success}")
    print(f"  失败: {output_failed}")
    
    # 统计失败原因
    if os.path.exists(output_failed):
        print(f"\n失败原因统计:")
        failure_stats = {}
        with open(output_failed, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    reason = record.get('failure_reason', 'unknown')
                    failure_stats[reason] = failure_stats.get(reason, 0) + 1
        
        for reason, count in sorted(failure_stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {reason}: {count} 篇")


if __name__ == "__main__":
    main()
