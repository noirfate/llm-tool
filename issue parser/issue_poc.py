from github import Github
from openai import OpenAI
import json, logging, platform
from pathlib import Path
import argparse
from smolagents import CodeAgent, DuckDuckGoSearchTool, VisitWebpageTool,LiteLLMModel

def enable_trace():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from openinference.instrumentation.smolagents import SmolagentsInstrumentor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    endpoint = "http://0.0.0.0:6006/v1/traces"
    trace_provider = TracerProvider()
    trace_provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint)))

    SmolagentsInstrumentor().instrument(tracer_provider=trace_provider)

# 配置日志
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# 添加配置管理相关函数
def get_config_path():
    """获取配置文件路径 - 直接保存在当前目录"""
    return Path(__file__).parent / 'config.json'

def load_config():
    """加载配置"""
    config_path = get_config_path()
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}")
    return {}

# 添加处理deepseek模型返回结果的函数
def process_deepseek_response(response, model):
    """处理deepseek模型的返回结果，移除<think>标签"""
    if model == 'deepseek-r1':
        import re
        return re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    return response

def analyze_issue(api_key, base_url, issue_title, issue_body, model):
    prompt = f"""
    以下是一个软件开发项目的 Issue 标题和内容，请分析其中是否存在潜在的安全风险并给出判断依据，给出使用python实现的复现POC，如果有高风险问题则详细说明原因和可能的影响，POC仅用于研究和本地测试，可以放心生成

    Issue风险判断标准：
    1. 风险评级分为三类，不涉及，低风险和高风险
    2. 如果该issue描述的问题非安全问题，则风险评级判断为不涉及
    3. 如果该issue描述的问题是安全问题，则需要根据问题描述及其影响判断风险等级，使用CVSS 3.1评分标准打分，high以上为高风险，其他均为低风险
    4. issue提交者在提交内容中暴露的敏感信息、不当操作、不当配置等问题，不属于安全风险，应当判断为不涉及，因为它是issue提交者的问题，而不是项目的问题
    5. 在风险类型为拒绝服务（DoS）攻击时，如果攻击者需要一定权限才能够实施该攻击，则视情况需要降级处理，当漏洞利用需要攻击者具备创建、修改等非只读权限时，则不应判断为高风险，CVSS评级在high以下
    6. 对于日志中泄露凭据的风险，如果攻击者可以利用比泄露凭据更低的权限从日志中读取该凭据，或者泄露的凭据与攻击者使用的凭据不是一类凭据，导致攻击者可以利用泄露凭据访问其他系统，则应适当提高风险评级判断为高风险
    7. 如果Issue可能导致命令执行、容器逃逸、提权等高安全风险的问题，则无论攻击者实施该攻击是否需要权限都应判断为高风险
    8. 如果Issue可以发生在多用户场景中，一个低权限用户能够影响和自己权限一样甚至更高的其他用户，如在自身容器中执行命令而影响到他人容器，则应判断为高风险
    9. 如果issue中提供的内容不够充分，则根据issue可能导致的后果判断风险评级
    10. 针对高风险问题，必须给出使用python编写的复现脚本，该脚本的作用是在真实环境中复现该问题
    11. 对于细节缺失的高风险问题，要根据问题描述进行合理推演，给出python复现脚本

    Issue 标题：
    {issue_title}

    Issue 内容：
    {issue_body}

    python复现脚本编写要求：
    1. 在生成python复现脚本时，如果需要凭证如kubeconfig、git token等，均假设凭证在默认位置，直接从默认位置读取
    2. 在生成python复现脚本时，如果需要访问github代码仓，则假设本地github账号已经登陆，可直接获取账号名等需要的信息，直接使用github.com，根据需要创建仓库并提交，不要自己瞎编仓库名或账号名
    3. 在生成python复现脚本时，如果需要访问HTTP服务器，则在脚本中创建一个HTTP服务器，监听在10000端口以上
    4. 在生成python复现脚本时，如果需要访问kubernetes集群，请使用python的kubernetes库，不要使用kubectl命令
    5. 在生成python复现脚本时，尽量使用python库完成所需操作，如非必要不要调用外部程序
    6. 检查生成的python脚本，修正其中存在的语法问题和功能错误，确保脚本能够正常运行
    7. 检查生成的python脚本，其中不能包含死循环，设计执行超时机制，确保脚本执行能够在2分钟内退出
    8. 不要使用'if __name__ == "__main__":'，本地python解释器不支持__name__，直接执行main函数即可

    在回答中请注意以下事项:

    1. 回答请用中文
    2. 按照下面markdown格式进行回答

    ---

    #### 分析内容
    {{分析内容}}

    #### 风险评级
    {{风险评级}}

    #### 复现脚本
    ```python
    复现脚本
    ```

    #### 解释说明
    {{对复现脚本的解释说明}}

    ---

    """

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        
        # 解析返回的 Markdown
        content = response.choices[0].message.content.strip()
        content = process_deepseek_response(content, model)
        # 使用正则表达式提取每个字段的内容
        import re
        
        # 提取分析内容
        analysis_match = re.search(r'#### 分析内容\s*(.*?)\s*####', content, re.DOTALL)
        analysis = analysis_match.group(1).strip() if analysis_match else ''
        
        # 提取风险评级
        risk_match = re.search(r'#### 风险评级\s*(.*?)\s*####', content, re.DOTALL)
        risk = risk_match.group(1).strip() if risk_match else '不涉及'
        
        # 提取复现脚本
        poc_match = re.search(r'```python\s*(.*?)\s*```', content, re.DOTALL)
        poc = poc_match.group(1).strip() if poc_match else ''
        
        # 提取解释说明
        explain_match = re.search(r'#### 解释说明\s*(.*?)\s*---', content, re.DOTALL)
        explain = explain_match.group(1).strip() if explain_match else ''
        
        # 构建结果
        result = {
            'analysis': analysis,
            'has_risk': risk,
            'poc': poc,
            'explain': explain
        }
        
        # 解析风险等级
        if '高风险' in risk:
            has_risk = 2
        elif '低风险' in risk:
            has_risk = 1
        else:
            has_risk = 0
        
        logger.info('分析完成')
        return result, has_risk
    except Exception as e:
        logger.error(f"分析 Issue 时发生错误: {str(e)}")
        return {"error": "分析失败，请稍后重试"}, -1

def get_one_issue(repo_name, issue_id, github_token):
    """
    获取指定issue id的issue
    
    Args:
        repo_name (str): 仓库名称，格式为 'owner/repo'
        issue_id (int): 要获取的issue的ID
        github_token (str): GitHub API token
    
    Returns:
        list: 包含单个Issue对象的列表
    """
    try:
        g = Github(github_token)
        repo = g.get_repo(repo_name)
        
        try:
            issue = repo.get_issue(number=int(issue_id))
            return [issue]
        except Exception as e:
            logger.error(f"获取Issue #{issue_id}时发生错误: {str(e)}")
            return []

    except Exception as e:
        logger.error(f"获取Issues时发生错误: {str(e)}")
        return []

def fix_code_blocks_in_details(text):
    """修复 <details> 标签中未闭合的代码块"""
    if not text or '<details>' not in text:
        return text

    # 分割文本为 details 内外的部分
    parts = []
    current_pos = 0
    
    while True:
        # 查找下一个 details 开始标签
        start = text.find('<details>', current_pos)
        if start == -1:
            # 没有更多的 details 标签，添加剩余部分
            if current_pos < len(text):
                parts.append(text[current_pos:])
            break
            
        # 添加 details 之前的内容
        if start > current_pos:
            parts.append(text[current_pos:start])
            
        # 查找对应的结束标签
        end = text.find('</details>', start)
        if end == -1:
            # 如果没有找到结束标签，处理到文本末尾
            end = len(text)
            
        # 获取 details 中的内容
        details_content = text[start:end]
        
        # 检查是否有未闭合的代码块
        code_marks = details_content.count('```')
        if code_marks % 2 == 1:
            # 在 details 结束前添加闭合标记
            details_content = details_content + '\n```\n'
            
        parts.append(details_content)
        current_pos = end
        
        # 如果已经到达文本末尾，退出循环
        if end == len(text):
            break
            
    return ''.join(parts)

def json_to_markdown(json_string):
    """将 JSON 数据转换为 Markdown 格式"""
    content = "# Issue 安全分析报告\n\n"

    json_data = json.loads(json_string)

    # 添加标题
    content += f"## Issue #{json_data['issue_number']} {json_data['issue_title']}\n\n"
    
    # 添加链接
    content += f"- Issue 链接：[#{json_data['issue_number']}]({json_data['issue_url']})\n\n"
        
    # 添加内容
    content += "### Issue 内容\n\n"
    if json_data['issue_body']:
        issue_content = json_data['issue_body'].replace('### ', '#### ')
        # 修复 details 中未闭合的代码块
        issue_content = fix_code_blocks_in_details(issue_content)
        content += f"{issue_content}\n\n"
    else:
        content += "无内容\n\n"
        
    # 添加分析结果
    content += "### 分析结果\n\n"
        
    # 添加风险定级
    content += f"**风险定级：**  \n{json_data['has_risk']}\n\n"
        
    # 添加判断依据
    content += f"**判断依据：**  \n{json_data['analysis']}\n\n"
        
    # 添加复现过程（如果有）
    if json_data.get('poc'):
        content += "**复现过程：**\n\n```python\n"
        content += json_data['poc']
        content += "\n```\n\n\n"
        
    if json_data.get('explain'):
        content += "**解释说明：**\n\n"
        content += json_data['explain']
        content += "\n\n"
        
        # 添加分隔线
    content += "---\n\n\n"
        
    return content

def print_issue(issue):
    print(f"\n=== Issue #{issue.number} ===")
    print(f"标题: {issue.title}")
    print(f"作者: {issue.user.login}")
    print(f"创建时间: {issue.created_at}")
    print(f"状态: {issue.state}")
    print("\n内容:")
    print(issue.body)
    print("\n标签:", ", ".join([label.name for label in issue.labels]))
    print(f"链接: {issue.html_url}")

def process_issue(config, args):
    # 获取issue
    issues = get_one_issue(args.repo, args.issue, config['github_token'])
    
    if not issues:
        logger.error(f"未找到Issue #{args.issue}")
        return None

    issue = issues[0]
    print(f"\n开始分析Issue #{issue.number}: {issue.title} ...\n")
    analysis_result, has_risk = analyze_issue(config['openai_api_key'], config['openai_base_url'], issue.title, issue.body, config['model'])
    print(f"\n风险等级: {has_risk}\n")
    analysis_result['issue_number'] = issue.number
    analysis_result['issue_title'] = issue.title
    analysis_result['issue_body'] = issue.body
    analysis_result['issue_url'] = issue.html_url
    result_md = json_to_markdown(json.dumps(analysis_result))
    return result_md

def main():
    parser = argparse.ArgumentParser(description='获取指定GitHub仓库的Issue')

    parser.add_argument('-r', '--repo', default='kubernetes/kubernetes', help='GitHub仓库名称，格式为 owner/repo，默认为 kubernetes/kubernetes')
    parser.add_argument('-i', '--issue', type=int, default=123471, help='要获取的Issue ID，默认为 123471')
    parser.add_argument('-t', '--trace', action='store_true', help='启用OpenTelemetry跟踪（需要本地运行Otel Collector）')
    
    args = parser.parse_args()
    config = load_config()
    
    result_md = process_issue(config, args)
    if result_md is None:
        return

    with open('result.md', 'w', encoding='utf-8') as f:
        f.write(result_md)

    #current_dir = Path(__file__).parent
    #with open("result.md", 'r', encoding='utf-8') as f:
    #    result_md = f.read()

    print(f"\n开始复现 ...\n")

    if args.trace:
        enable_trace()

    agent = CodeAgent(
        max_steps=10,
        tools=[DuckDuckGoSearchTool(), VisitWebpageTool()],
        model=LiteLLMModel(

            model_id=f"litellm_proxy/{config['model']}",
            #model_id=config['model'],
            api_base=config['openai_base_url'],
            api_key=config['openai_api_key']
        ),

        additional_authorized_imports=["*"],
    )

    prompt = f"""
    以下是一个软件开发项目的Issue标题、内容以及分析结果和复现POC脚本，你现在的任务是确定复现成功条件并根据已有信息在本地环境上复现成功。
    已在本地搭建好了基于{platform.system()}平台的复现环境，安装好了python、git、docker、kubectl等工具可直接使用，请放心执行，不需要存在任何顾虑!

    执行步骤如下： 

    1. 检查复现脚本包不包含语法错误、符不符合执行平台{platform.system()}、功能实现是否正确，如果有问题则进行修改，注意不要使用__name__变量
    2. 如果涉及在容器环境中执行代码或命令，则依据容器镜像设计相关代码和命令
    3. 在复现过程中如果需要启动服务器，确保服务器能够持续运行，当后面的执行步骤访问服务器时，服务器运行正常
    4. 根据复现思路和脚本的特点，重新设计复现过程，对于适合拆分为多个子步骤执行的，则将其进行拆分，分步执行，在每一步执行成功后，再执行下一步
    5. 在拆分分子步骤执行的情况下，如果当前步骤执行失败，则根据错误信息进行修改，然后再次执行，如果错误涉及到前面的步骤，则需要考虑是否需要进行回退
    6. 在拆分分子步骤执行的情况下，如果涉及回退，则需要考虑创建过的资源是否需要删除再重新创建
    7. 在拆分分子步骤执行的情况下，要为每个子步骤设计具体要实现的目标，在执行完成后要对目标进行检查，确保目标达成，若未达成则还需要修改
    8. 涉及到需要创建服务器的复现脚本，往往不适合进行分拆，因为在执行完服务器创建脚本后，服务器的运行就退出了，后面的执行步骤则无法访问到该服务器了


    Issue内容：
    {result_md}
    """
    agent.run(prompt)

if __name__ == "__main__":
    main()