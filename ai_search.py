import streamlit as st
from openai import OpenAI
from duckduckgo_search import DDGS
from pathlib import Path
import asyncio
import os
import nest_asyncio
import signal
import platform
import logging
import json

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 应用nest_asyncio
nest_asyncio.apply()

os.environ['PYPPETEER_CHROMIUM_REVISION'] = '1380989'
os.environ["PYPPETEER_DOWNLOAD_HOST"] = "http://npm.taobao.org/mirrors/chromium-browser-snapshots/"
from pyppeteer import launch

# 初始化全局事件循环
loop = asyncio.get_event_loop()

# 禁用信号处理（仅在Windows上）
if platform.system() == 'Windows':
    signal.signal = lambda *args: None

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
if "messages" not in st.session_state:
    st.session_state.messages = []
if "search_results" not in st.session_state:
    st.session_state.search_results = []


# 侧边栏配置
with st.sidebar:
    st.header("配置参数")
    saved_config = load_config()
    api_key = st.text_input("OpenAI API密钥", value=saved_config.get('openai_api_key', ''), type="password")
    api_base = st.text_input("OpenAI Base URL", value=saved_config.get('openai_base_url', "https://api.wlai.vip/v1"))
    search_count = st.text_input("搜索结果数量", value="15")

# 添加模型选择下拉框
    model_options = {
        'o1-mini': 'o1-mini',
        'o3-mini': 'o3-mini',
        'deepseek-r1': 'deepseek-r1'
    }
    selected_model = st.selectbox(
        "选择模型",
        options=list(model_options.keys()),
        format_func=lambda x: model_options[x],
        index=0 if saved_config.get('model') not in model_options else list(model_options.keys()).index(saved_config.get('model'))
    )
    st.session_state.model = selected_model

    # 添加是否携带历史会话的开关
    include_history = st.toggle("携带历史会话", value=saved_config.get('include_history', False))
    st.session_state.include_history = include_history

    # 添加保存配置按钮
    if st.button("保存配置"):
        # 先读取现有配置
        current_config = load_config()
        # 更新需要修改的配置项
        if api_key:
            current_config['openai_api_key'] = api_key
        if api_base:
            current_config['openai_base_url'] = api_base
        current_config['model'] = selected_model
        current_config['search_count'] = search_count
        current_config['include_history'] = include_history
        
        # 保存更新后的配置
        if save_config(current_config):
            st.success("配置已保存")
        else:
            st.error("配置保存失败")

# 添加处理deepseek模型返回结果的函数
def process_deepseek_response(response, model):
    """处理deepseek模型的返回结果，移除<think>标签"""
    if model == 'deepseek-r1':
        import re
        return re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    return response

def generate_search_query(user_input):
    """使用模型生成搜索关键词"""
    client = OpenAI(api_key=api_key, base_url=api_base)
    response = client.chat.completions.create(
        model=st.session_state.model,
        messages=[
            {"role": "user", "content": f"请根据以下用户问题生成适用于duckduckgo的搜索关键词或短语，如有多个则以空格分隔：\n{user_input}"}
        ]
    )
    result = response.choices[0].message.content.strip()
    return process_deepseek_response(result, st.session_state.model)

async def extract_webpage_content(url):
    """使用pyppeteer提取网页主要内容"""
    browser = await launch({
        'headless': True
    })
    page = await browser.newPage()
    await page.goto(url, {'waitUntil': 'networkidle2'})
    content = await page.evaluate('''() => {
        return document.body.innerText;
    }''')
    await browser.close()
    content = '\n'.join(line.strip() for line in content.splitlines() if line.strip())
    content = ' '.join(content.split())
    return content

def generate_content_summary(content, query, client):
    """使用大模型生成内容摘要"""
    if not content:
        return None

    prompt = f"""
    请根据以下内容，生成一个与查询相关的内容总结，不要过于简单，要尽量保留有价值的信息

    查询：
    {query}
    
    内容：
    {content}

    总结要求：
    - 对有价值的、与查询相关的内容要尽量保留，以免造成信息损失
    - 不要忽略和遗漏任何相关的信息
    - 如内容中不包含相关信息，则只回复"无相关内容"
    - 使用中文回答
    """
    try:
        response = client.chat.completions.create(
            model=st.session_state.model,
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.choices[0].message.content.strip()
        return process_deepseek_response(result, st.session_state.model)
    except Exception as e:
        st.error(f"生成摘要失败: {str(e)}")
        return None

def web_search(query):
    """使用DuckDuckGo执行搜索并生成相关摘要"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=int(search_count)))
            
        if not results:
            st.error("搜索未找到任何结果")
            return []
        
        # 创建OpenAI客户端
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        # 创建进度显示
        progress_placeholder = st.empty()
        total_results = len(results)
        
        processed_results = []
        for idx, result in enumerate(results, 1):
            try:
                # 显示当前进度
                progress_placeholder.info(f"正在处理搜索结果 {idx}/{total_results}: {result['title']}")
                
                # 使用全局事件循环执行异步函数
                content = loop.run_until_complete(extract_webpage_content(result["href"]))
                
                # 生成摘要
                if content:
                    progress_placeholder.info(f"正在为搜索结果 {idx}/{total_results} 生成摘要...")
                    summary = generate_content_summary(content, query, client)
                else:
                    summary = result["body"]  # 如果无法提取内容，使用原始摘要
                    
                processed_results.append({
                    "title": result["title"],
                    "snippet": summary or result["body"],  # 如果摘要生成失败，使用原始摘要
                    "url": result["href"]
                })
            except Exception as e:
                st.error(f"处理结果失败 ({result['href']}): {str(e)}")
                # 如果处理失败，使用原始结果
                processed_results.append({
                    "title": result["title"],
                    "snippet": result["body"],
                    "url": result["href"]
                })
        
        # 清除进度显示
        progress_placeholder.empty()
        return processed_results
            
    except Exception as e:
        st.error(f"搜索失败: {str(e)}")
        return []

# 显示聊天记录
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 处理用户输入
if prompt := st.chat_input("请输入您的问题"):
    # 用户消息
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    # 生成搜索关键词
    with st.spinner("正在生成搜索关键词..."):
        search_query = generate_search_query(prompt)
    
    # 执行搜索
    with st.spinner("正在搜索..."):
        results = web_search(search_query)
        st.session_state.search_results = results
    
    # 构建带搜索结果的提示
    context = "\n".join([f"- 来源{i+1}. [{res['title']}]({res['url']})\n{res['snippet']}" 
                       for i, res in enumerate(results)])
    
    # 获取模型回复
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        # 构建消息历史
        messages = []
        if st.session_state.include_history:
            # 只包含到倒数第二条消息的历史（不包含当前问题）
            messages.extend([{"role": m["role"], "content": m["content"]} 
                           for m in st.session_state.messages[:-1]])
        
        # 添加系统提示词
        messages.append({
            "role": "system",
            "content": """你是一个搜索增强的AI助手。你的回答需要：
1. 运用自身知识并参考搜索结果
2. 引用相关的搜索结果时给出相应编号和链接，如[来源1](https://example.com)
3. 不要忽略和遗漏任何相关的搜索结果
4. 回答要详尽
5. 使用中文回答"""
        })
        
        # 添加当前问题和搜索结果
        messages.append({
            "role": "user",
            "content": f"""### 搜索结果：
{context}

### 用户问题：
{prompt}"""
        })
        
        response = client.chat.completions.create(
            model=st.session_state.model,
            messages=messages
        )
        
        full_response = response.choices[0].message.content
        full_response = process_deepseek_response(full_response, st.session_state.model)
        message_placeholder.markdown(full_response)
    
    st.session_state.messages.append({"role": "assistant", "content": full_response})
   
    # 添加调试信息
    with st.expander("调试信息"):
        st.write("### 搜索关键词")
        st.code(search_query)
        
        st.write("### 搜索结果")
        # 使用markdown格式显示搜索结果，每个结果独立成段
        for i, res in enumerate(st.session_state.search_results):
            st.markdown(f"""
#### 来源{i+1}. [{res['title']}]({res['url']})
{res['snippet']}
---
""")

