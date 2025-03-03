from github import Github
from openai import OpenAI
from datetime import datetime
import streamlit as st
import json, sys, math
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="GitHub Issue 安全分析工具",
    page_icon="🛡️",
    layout="wide"
)

st.title("GitHub Issue 安全分析工具 🛡️")

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

def save_config(config):
    """保存配置"""
    config_path = get_config_path()
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存配置文件失败: {str(e)}")
        return False

# 初始化会话状态
def init_session_state():
    """初始化会话状态"""
    if 'current_page' not in st.session_state:
        st.session_state.current_page = 1
    if 'analysis_results' not in st.session_state:
        st.session_state.analysis_results = []
    if 'total_issues' not in st.session_state:
        st.session_state.total_issues = 0
    if 'issues' not in st.session_state:
        st.session_state.issues = []
    if 'analysis_complete' not in st.session_state:
        st.session_state.analysis_complete = False
    if "model_options" not in st.session_state:
        st.session_state.model_options = {'o1-mini': 'o1-mini', 'o3-mini': 'o3-mini', 'deepseek-r1': 'deepseek-r1'}

with st.sidebar:
    st.header("配置参数")
    
    init_session_state()

    # 加载已保存的配置
    saved_config = load_config()
    config_model = saved_config.get('model')
    if config_model:
        if config_model not in st.session_state.model_options:
            st.session_state.model_options[config_model] = config_model
        st.session_state.selected_model = config_model
    else:
        st.session_state.selected_model = list(st.session_state.model_options.keys())[0]
    
    repo_name = st.text_input("代码仓库", saved_config.get('repo_name', "kubernetes/kubernetes"))
    labels = st.text_input("标签（用逗号分隔）", saved_config.get('labels', "kind/bug"))
    since_time = st.date_input("起始时间", datetime(2025, 1, 1))
    until_time = st.date_input("结束时间", datetime.now())
    openai_api_key = st.text_input("OpenAI API Key", value=saved_config.get('openai_api_key', ''), type="password")
    openai_base_url = st.text_input("OpenAI Base URL（可选）", value=saved_config.get('openai_base_url', "https://api.wlai.vip/v1"))
    github_token = st.text_input("GitHub Token", value=saved_config.get('github_token', ''), type="password")
    
    # 添加 "获取模型列表" 按钮
    if st.button("获取模型列表"):
        client = OpenAI(api_key=openai_api_key, base_url=openai_base_url)
        try:
            models = client.models.list()
            st.session_state.model_options = {m.id: m.id for m in models.data}
            st.success("模型列表已更新")
        except Exception as e:
            st.error(f"获取模型列表失败: {str(e)}")

    # 显示所有模型并默认选中当前模型
    selected_models = st.multiselect(
        "选择模型（支持搜索）",
        options=list(st.session_state.model_options.keys()),
        format_func=lambda x: st.session_state.model_options[x],
        default=[st.session_state.selected_model] if st.session_state.selected_model in st.session_state.model_options else [list(st.session_state.model_options.keys())[0]],
        max_selections=1,
        placeholder="请选择一个模型"
    )
    
    # 确保只选择一个模型
    if len(selected_models) > 0:
        st.session_state.selected_model = selected_models[-1]
    elif len(selected_models) == 0:
        st.session_state.selected_model = list(st.session_state.model_options.keys())[0]
    
    st.session_state.model = st.session_state.selected_model

    # 添加保存配置按钮
    if st.button("保存配置"):
        # 先读取现有配置
        current_config = load_config()

        # 更新配置项（只更新非空值）
        if repo_name:
            current_config['repo_name'] = repo_name
        if labels:
            current_config['labels'] = labels
        if openai_api_key:
            current_config['openai_api_key'] = openai_api_key
        if openai_base_url:
            current_config['openai_base_url'] = openai_base_url
        if github_token:
            current_config['github_token'] = github_token
        if st.session_state.selected_model:
            current_config['model'] = st.session_state.selected_model
            
        # 保存更新后的配置
        if save_config(current_config):
            st.success("配置已保存")
        else:
            st.error("配置保存失败")
    
    execute_button = st.button("获取issue")

def analyze_issue(api_key, base_url, issue_title, issue_body):
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
        logger.info('开始分析')
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=st.session_state.model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        
        # 解析返回的 Markdown
        content = response.choices[0].message.content.strip()

        #logger.info(f"返回的内容: {content}")
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
        st.error(f"分析失败: {str(e)}")
        return {"error": "分析失败，请稍后重试"}, -1

def get_issues(repo_name, labels, since_time, until_time, github_token):
    try:
        g = Github(github_token)
        repo = g.get_repo(repo_name)

        # 构建查询参数
        labels_query = ' '.join([f'label:{label.strip()}' for label in labels.split(',')])
        since_str = since_time.strftime('%Y-%m-%d')
        until_str = until_time.strftime('%Y-%m-%d')

        query = f'repo:{repo_name} is:issue {labels_query} created:{since_str}..{until_str}'

        # 搜索 Issue 并转换为列表
        issues = list(g.search_issues(query))
        return issues

    except Exception as e:
        logger.error(f"获取 Issues 时发生错误: {str(e)}")
        st.error(f"获取 Issues 失败: {str(e)}")
        return []

def display_issue(issue, analysis=None):
    """显示单个issue的函数"""
    cols = st.columns([8, 1])  # 创建两列布局：标题占8份，分析按钮占1份
    
    with cols[0]:
        if analysis:
            if analysis.get('has_risk') == 2:
                title_color = "red"
            elif analysis.get('has_risk') == 1:
                title_color = "orange"
            else:
                title_color = "green"
        else:
            title_color = "gray"
            
        # 使用container来包装标题，确保不换行
        with st.container():
            expander = st.expander(f"#### :{title_color}[#{issue.number} {issue.title}]", expanded=False)
            with expander:
                st.markdown(f"**Issue 链接：** [#{issue.number}]({issue.html_url})", unsafe_allow_html=True)
                
                # 处理Issue内容的换行和未闭合的代码块
                issue_content = issue.body if issue.body else '无内容'
                if issue.body:
                    issue_content = fix_code_blocks_in_details(issue_content)
                    issue_content = issue_content.replace('\n', '  \n')
                st.markdown(f"**Issue 内容：**  \n{issue_content}")
                
                # 处理分析结果的换行
                if analysis:
                    analysis_data = analysis['analysis']  # 获取分析结果
                    st.markdown("**分析结果**  \n")
                    st.markdown(f"**风险定级：**  \n{analysis_data['has_risk']}\n")
                    st.markdown(f"**判断依据：**  \n{analysis_data['analysis']}\n")
                    if analysis_data.get('poc'):  # 只有当 poc 不为空时才显示
                        st.markdown("**复现过程：**")
                        st.code(analysis_data['poc'], language="python")
                    if analysis_data.get('explain'):
                        st.markdown(f"**解释说明：**  \n{analysis_data['explain']}\n")
    
    with cols[1]:
        # 始终显示分析按钮，根据是否已分析显示不同文本
        with st.container():
            st.markdown('<div class="analyze-button">', unsafe_allow_html=True)
            button_text = "重新分析" if analysis else "分析"
            st.button(button_text, key=f"analyze_{issue.number}", type="secondary", use_container_width=True,
                     on_click=analyze_single_issue, args=(issue, openai_api_key, openai_base_url))
            st.markdown('</div>', unsafe_allow_html=True)

def analyze_single_issue(issue, api_key, base_url):
    """分析单个issue的辅助函数"""
    try:
        analysis_result, has_risk = analyze_issue(
            api_key,
            base_url,
            issue.title,
            issue.body or ''
        )
        if has_risk == -1:
            st.error(f"分析Issue #{issue.number}失败: {analysis_result}")
            return
            
        result = {
            'issue_number': issue.number,
            'issue_title': issue.title,
            'issue_url': issue.html_url,
            'analysis': analysis_result,
            'has_risk': has_risk,
            'issue_body': issue.body or ''
        }
        
        if 'analysis_results' not in st.session_state:
            st.session_state.analysis_results = []
            
        # 查找是否已存在该 issue 的分析结果
        existing_index = next(
            (i for i, r in enumerate(st.session_state.analysis_results) 
             if r['issue_number'] == issue.number), 
            -1
        )
        
        if existing_index != -1:
            # 如果已存在，替换原有结果
            st.session_state.analysis_results[existing_index] = result
        else:
            # 如果不存在，添加新结果
            st.session_state.analysis_results.append(result)
            
        st.session_state.analysis_complete = True
    except Exception as e:
        st.error(f"分析Issue #{issue.number}失败: {str(e)}")

def change_page(page_number):
    """更新页码的回调函数"""
    st.session_state.current_page = page_number

def display_pagination(current_page, total_pages):
    """显示分页控制"""
    st.markdown("""
        <style>
        /* 分页区域样式 */
        .pagination-container {
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 10px 0;
            gap: 5px;
        }
        /* 分页按钮样式 */
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] button {
            width: 32px !important;
            height: 32px !important;
            padding: 0px !important;
            font-size: 14px !important;
            border-radius: 16px !important;
            box-shadow: none !important;
            border: 1px solid #ddd !important;
            background-color: white !important;
            color: #666 !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] button:hover {
            background-color: #f5f5f5 !important;
            border-color: #666 !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] button[data-testid="baseButton-secondary"] {
            background-color: white !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] button[data-testid="baseButton-primary"] {
            background-color: #ff4b4b !important;
            color: white !important;
            border-color: #ff4b4b !important;
        }
        /* 页码显示样式 */
        .page-info {
            text-align: center;
            color: #666;
            font-size: 14px;
            margin: 10px 0;
        }
        .page-number {
            color: #ff4b4b;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)
    
    with st.container():
        # 显示总页数信息
        page_info = f'<div class="page-info">第 <span class="page-number">{current_page}</span> 页 / 共 <span class="page-number">{total_pages}</span> 页</div>'
        st.markdown(page_info, unsafe_allow_html=True)
        
        cols = st.columns([1, 1, 1, 1, 1, 1, 1])
        
        # 首页按钮
        with cols[0]:
            if current_page > 1:
                st.button("⟪", key="first_page", use_container_width=False,
                         on_click=change_page, args=(1,))
        
        # 上一页按钮
        with cols[1]:
            if current_page > 1:
                st.button("◀", key="prev_page", use_container_width=False,
                         on_click=change_page, args=(current_page - 1,))
        
        # 页码按钮
        start_page = max(1, current_page - 2)
        end_page = min(total_pages, start_page + 2)
        if end_page - start_page < 2:
            start_page = max(1, end_page - 2)
        
        for i, col in zip(range(start_page, end_page + 1), cols[2:5]):
            with col:
                st.button(str(i), 
                         type="primary" if i == current_page else "secondary",
                         key=f"page_{i}", 
                         use_container_width=False,
                         on_click=change_page,
                         args=(i,))
        
        # 下一页按钮
        with cols[5]:
            if current_page < total_pages:
                st.button("▶", key="next_page", use_container_width=False,
                         on_click=change_page, args=(current_page + 1,))
        
        # 末页按钮
        with cols[6]:
            if current_page < total_pages:
                st.button("⟫", key="last_page", use_container_width=False,
                         on_click=change_page, args=(total_pages,))

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
    markdown = "# Issue 安全分析报告\n\n"
    
    # 分离不同风险等级的 issues
    risk_issues = []
    low_risk_issues = []
    no_risk_issues = []
    
    json_data = json.loads(json_string)
    for item in json_data:
        content = ""
        # 添加标题
        content += f"## Issue #{item['issue_number']} {item['issue_title']}\n\n"
        
        # 添加链接
        content += f"- Issue 链接：[#{item['issue_number']}]({item['issue_url']})\n\n"
        
        # 添加内容
        content += "### Issue 内容\n\n"
        if item['issue_body']:
            issue_content = item['issue_body'].replace('### ', '#### ')
            # 修复 details 中未闭合的代码块
            issue_content = fix_code_blocks_in_details(issue_content)
            content += f"{issue_content}\n\n"
        else:
            content += "无内容\n\n"
        
        # 添加分析结果
        content += "### 分析结果\n\n"
        analysis_data = item['analysis']
        
        # 添加风险定级
        content += f"**风险定级：**  \n{analysis_data['has_risk']}\n\n"
        
        # 添加判断依据
        content += f"**判断依据：**  \n{analysis_data['analysis']}\n\n"
        
        # 添加复现过程（如果有）
        if analysis_data.get('poc'):
            content += "**复现过程：**\n\n```python\n"
            content += analysis_data['poc']
            content += "\n```\n\n\n"
        
        if analysis_data.get('explain'):
            content += "**解释说明：**\n\n"
            content += analysis_data['explain']
            content += "\n\n"
        
        # 添加分隔线
        content += "---\n\n\n"
        
        # 根据分析结果分类
        if item['has_risk'] == 2:
            risk_issues.append(content)
        elif item['has_risk'] == 1:
            low_risk_issues.append(content)
        else:
            no_risk_issues.append(content)
    
    # 添加高风险的 issues
    if risk_issues:
        markdown += f"# 🚨 存在高风险的 Issues ({len(risk_issues)} 个)\n\n"
        markdown += "".join(risk_issues)
    
    # 添加低风险的 issues
    if low_risk_issues:
        markdown += f"# ⚠️ 存在低风险的 Issues ({len(low_risk_issues)} 个)\n\n"
        markdown += "".join(low_risk_issues)
    
    # 添加无风险的 issues
    if no_risk_issues:
        markdown += f"# ✅ 不涉及安全风险的 Issues ({len(no_risk_issues)} 个)\n\n"
        markdown += "".join(no_risk_issues)
    
    return markdown

def display_action_buttons():
    """显示操作按钮（导出和清除）和分析进度"""
    st.markdown("""
        <style>
        /* 底部功能区样式 */
        .bottom-area {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background-color: white;
            border-top: 1px solid #eee;
            padding: 15px 0;
            z-index: 1000;
        }
        .bottom-container {
            max-width: 1000px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 20px;
        }
        /* 分析进度样式 */
        .analysis-progress {
            color: #666;
            font-size: 14px;
            font-weight: 500;
            white-space: nowrap;
        }
        .progress-numbers {
            color: #ff4b4b;
            font-weight: bold;
            margin: 0 4px;
        }
        /* 功能按钮容器样式 */
        div[data-testid="column"] > div {
            display: flex;
            justify-content: center;
        }
        /* 功能按钮样式 */
        div.stButton > button,
        div.stDownloadButton > button {
            min-width: 120px !important;
            height: 36px !important;
            font-size: 14px !important;
            font-weight: 500 !important;
            border-radius: 18px !important;
            box-shadow: none !important;
            border: 1px solid #ddd !important;
            background-color: white !important;
            color: #666 !important;
            transition: all 0.3s ease !important;
            padding: 0 20px !important;
            line-height: 34px !important;
            white-space: nowrap !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
        }
        div.stButton > button:hover,
        div.stDownloadButton > button:hover {
            background-color: #ff4b4b !important;
            color: white !important;
            border-color: #ff4b4b !important;
        }
        /* 为底部固定区域预留空间 */
        .content-wrapper {
            margin-bottom: 80px;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # 显示分析进度和功能按钮
    st.markdown('<div class="bottom-area">', unsafe_allow_html=True)
    st.markdown('<div class="bottom-container">', unsafe_allow_html=True)
    
    # 显示分析进度
    total_issues = len(st.session_state.issues) if hasattr(st.session_state, 'issues') else 0
    analyzed_issues = len(st.session_state.analysis_results)
    progress_text = f'<div class="analysis-progress">已分析<span class="progress-numbers">{analyzed_issues}/{total_issues}</span>个issues</div>'
    
    # 创建功能按钮
    results_json = json.dumps(
        st.session_state.analysis_results,
        ensure_ascii=False,
        indent=4
    )

    results_md = json_to_markdown(results_json)
    
    # 使用列布局
    cols = st.columns([2, 1, 1])
    
    # 显示进度
    with cols[0]:
        st.markdown(progress_text, unsafe_allow_html=True)
    
    # 显示导出按钮
    with cols[1]:
        st.download_button(
            '导出结果',
            data=results_md,
            file_name='issue_analysis_results.md',
            mime='text/markdown',
            use_container_width=False
        )
    
    # 显示清除按钮
    with cols[2]:
        st.button('清除结果', on_click=clear_results, use_container_width=False)
    
    st.markdown('</div></div>', unsafe_allow_html=True)

def clear_results():
    """清除分析结果的回调函数"""
    st.session_state.analysis_results = []

def main():
    # 添加全局样式
    st.markdown("""
        <style>
        /* 通用按钮样式重置 */
        div.stButton > button {
            box-sizing: border-box !important;
        }
        /* 分析按钮样式 */
        div[data-testid="column"] div.stButton.analyze-button > button {
            width: 100% !important;
            height: 32px !important;
            font-size: 14px !important;
            border-radius: 16px !important;
            background-color: white !important;
            color: #666 !important;
            border: 1px solid #ddd !important;
            box-shadow: none !important;
        }
        div[data-testid="column"] div.stButton.analyze-button > button:hover {
            background-color: #ff4b4b !important;
            color: white !important;
            border-color: #ff4b4b !important;
        }
        /* 分隔线样式 */
        hr {
            margin: 30px 0 20px 0 !important;
            border-color: #eee !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    if execute_button:
        # 输入验证
        if not all([openai_api_key, github_token, repo_name, labels]):
            st.error("请填写所有必需的字段")
            return

        try:
            with st.spinner('正在获取 Issue 列表...'):
                st.session_state.issues = get_issues(repo_name, labels, since_time, until_time, github_token)
                st.session_state.total_issues = len(st.session_state.issues)

            if not st.session_state.issues:
                st.warning("未找到符合条件的 Issues")
                return
        except Exception as e:
            logger.error(f"获取 Issues 时发生错误: {str(e)}")
            st.error(f"获取 Issues 失败: {str(e)}")
            return

    # 如果已经有issues数据，则显示分页内容
    if hasattr(st.session_state, 'issues') and st.session_state.issues:
        # 分页逻辑
        per_page = 10
        num_pages = math.ceil(st.session_state.total_issues / per_page)
        current_page = st.session_state.current_page
        
        start_idx = (current_page - 1) * per_page
        end_idx = min(start_idx + per_page, st.session_state.total_issues)

        # 添加"分析当前页面所有Issue"按钮
        analyze_button_key = f"analyze_page_{current_page}"
        if st.button("分析当前页面所有Issue", key=analyze_button_key):
            current_issues = st.session_state.issues[start_idx:end_idx]
            progress_text = st.empty()
            progress_bar = st.progress(0)
            
            for idx, issue in enumerate(current_issues):
                if not any(r['issue_number'] == issue.number for r in st.session_state.analysis_results):
                    progress_text.text(f'正在分析 Issue #{issue.number}...')
                    analyze_single_issue(issue, openai_api_key, openai_base_url)
                progress_bar.progress((idx + 1) / len(current_issues))
            
            progress_text.text('分析完成！')
            st.session_state.analysis_complete = True

        # 显示Issues
        for issue in st.session_state.issues[start_idx:end_idx]:
            analysis = next(
                (r for r in st.session_state.analysis_results if r['issue_number'] == issue.number),
                None
            )
            display_issue(issue, analysis)

        # 如果分析完成，重置状态
        if st.session_state.analysis_complete:
            st.session_state.analysis_complete = False
            st.rerun()

        # 显示分页控制
        st.write("---")  # 添加分隔线
        display_pagination(current_page, num_pages)
        st.write("")  # 添加空行
        
        # 导出功能
        if st.session_state.analysis_results:
            display_action_buttons()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"程序执行出错: {str(e)}")
        st.error(f"发生错误: {str(e)}")
        sys.exit(1)