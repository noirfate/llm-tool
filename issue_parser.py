from github import Github
from openai import OpenAI
from datetime import datetime
import streamlit as st
import time, json, sys, math
from functools import lru_cache
import logging
import os
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

with st.sidebar:
    st.header("配置参数")
    
    # 加载已保存的配置
    saved_config = load_config()
    
    repo_name = st.text_input("代码仓库", saved_config.get('repo_name', "kubernetes/kubernetes"))
    labels = st.text_input("标签（用逗号分隔）", saved_config.get('labels', "kind/bug"))
    since_time = st.date_input("起始时间", datetime(2025, 1, 1))
    until_time = st.date_input("结束时间", datetime.now())
    openai_api_key = st.text_input("OpenAI API Key", value=saved_config.get('openai_api_key', ''), type="password")
    openai_base_url = st.text_input("OpenAI Base URL（可选）", value=saved_config.get('openai_base_url', "https://api.wlai.vip/v1"))
    github_token = st.text_input("GitHub Token", value=saved_config.get('github_token', ''), type="password")
    
    # 添加模型选择下拉框
    model_options = {
        'o1-preview': 'o1-preview',
        'gpt-4o': 'gpt-4o'
    }
    selected_model = st.selectbox(
        "选择模型",
        options=list(model_options.keys()),
        format_func=lambda x: model_options[x],
        index=0 if saved_config.get('model') not in model_options else list(model_options.keys()).index(saved_config.get('model'))
    )
    st.session_state.model = selected_model
    
    # 添加保存配置按钮
    if st.button("保存配置"):
        config = {
            'repo_name': repo_name,
            'labels': labels,
            'openai_api_key': openai_api_key,
            'openai_base_url': openai_base_url,
            'github_token': github_token,
            'model': selected_model
        }
        if save_config(config):
            st.success("配置已保存")
        else:
            st.error("配置保存失败")
    
    execute_button = st.button("获取issue")

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
    if 'model' not in st.session_state:
        st.session_state.model = 'o1-preview'

# 添加缓存装饰器
@lru_cache(maxsize=100)
def analyze_issue(api_key, base_url, issue_title, issue_body, model):
    prompt = f"""
    以下是一个软件开发项目的 Issue 标题和内容，请分析其中是否存在潜在的安全风险，如果不存在安全风险则仅回复不涉及，如果有则详细说明原因和可能的影响，给出proof of concept

    风险判断标准：
    1. 该风险能被攻击者利用
    2. 该风险有可能成为一个漏洞，并被分配CVE编号，使用CVSS 3.1评分标准打分，结果要在high以上
    
    Issue 标题：
    {issue_title}

    Issue 内容：
    {issue_body}

    请注意，只需要关注与安全相关的内容，回答请用中文。
    """
    try:
        logger.info('开始分析')
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        analysis = response.choices[0].message.content.strip()
        logger.info('分析完成')
        if '不涉及' in analysis:
            has_risk = False
        else:
            has_risk = True
        return analysis, has_risk
    except Exception as e:
        logger.error(f"分析 Issue 时发生错误: {str(e)}")
        st.error(f"分析失败: {str(e)}")
        return "分析失败，请稍后重试", False

@st.cache_data(ttl=3600)  # 缓存一小时
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
            if analysis.get('has_risk'):
                title_color = "red"
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
                    analysis_content = analysis['analysis'].replace('\n', '  \n')
                    st.markdown(f"**分析结果：**  \n{analysis_content}")
    
    with cols[1]:
        # 只显示分析按钮
        if not analysis:
            with st.container():
                st.markdown('<div class="analyze-button">', unsafe_allow_html=True)
                st.button("分析", key=f"analyze_{issue.number}", type="secondary", use_container_width=True,
                         on_click=analyze_single_issue, args=(issue, openai_api_key, openai_base_url))
                st.markdown('</div>', unsafe_allow_html=True)

def analyze_single_issue(issue, api_key, base_url):
    """分析单个issue的辅助函数"""
    try:
        analysis_result, has_risk = analyze_issue(
            api_key,
            base_url,
            issue.title,
            issue.body or '',
            st.session_state.model
        )
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
    
    # 分离有风险和无风险的 issues
    risk_issues = []
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
        content += f"{item['analysis']}\n\n"
        
        # 添加分隔线
        content += "---\n\n"
        
        # 根据分析结果分类
        if item['has_risk']:
            risk_issues.append(content)
        else:
            no_risk_issues.append(content)
    
    # 添加有风险的 issues
    if risk_issues:
        markdown += f"# 🚨 存在安全风险的 Issues ({len(risk_issues)} 个)\n\n"
        markdown += "".join(risk_issues)
    
    # 添加无风险的 issues
    if no_risk_issues:
        markdown += f"# 📌 不涉及安全风险的 Issues ({len(no_risk_issues)} 个)\n\n"
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
    init_session_state()
    
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