import streamlit as st
from openai import OpenAI
from duckduckgo_search import DDGS
from pathlib import Path
import asyncio
import os
import nest_asyncio
import logging
import json
import atexit
import psutil

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 应用nest_asyncio
nest_asyncio.apply()

# 设置pyppeteer环境变量
#os.environ['PYPPETEER_CHROMIUM_REVISION'] = '1380989'
#os.environ["PYPPETEER_DOWNLOAD_HOST"] = "http://npm.taobao.org/mirrors/chromium-browser-snapshots/"
os.environ['PYPPETEER_NO_SIGNAL'] = '1'  # 禁用pyppeteer的信号处理

from pyppeteer import launch

# 初始化全局事件循环
loop = asyncio.get_event_loop()

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
if "model_options" not in st.session_state:
    st.session_state.model_options = {'o1-mini': 'o1-mini', 'o3-mini': 'o3-mini', 'deepseek-r1': 'deepseek-r1'}
if "selected_model" not in st.session_state:
    saved_config = load_config()
    config_model = saved_config.get('model')
    if config_model:
        if config_model not in st.session_state.model_options:
            st.session_state.model_options[config_model] = config_model
        st.session_state.selected_model = config_model
    else:
        st.session_state.selected_model = list(st.session_state.model_options.keys())[0]

# 添加导出会话功能
def export_conversation_to_markdown():
    """将会话导出为Markdown文件"""
    # 过滤掉系统消息，只保留用户和助手的对话
    conversation_messages = [msg for msg in st.session_state.messages if msg["role"] in ["user", "assistant"]]
    
    if not conversation_messages:
        st.warning("当前没有会话内容可导出")
        return None
    
    from datetime import datetime
    
    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"conversation_{timestamp}.md"
    
    # 生成Markdown内容
    markdown_content = "# 会话记录\n\n"
    markdown_content += f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    markdown_content += f"使用模型：{st.session_state.model}\n\n"
    markdown_content += "---\n\n"
    
    for msg in conversation_messages:
        role = "用户" if msg["role"] == "user" else "助手"
        markdown_content += f"## {role}\n\n{msg['content']}\n\n---\n\n"
    
    return markdown_content, filename

# 侧边栏配置
with st.sidebar:
    st.header("配置参数")
    
    saved_config = load_config()
    api_key = st.text_input("OpenAI API密钥", value=saved_config.get('openai_api_key', ''), type="password")
    api_base = st.text_input("OpenAI Base URL", value=saved_config.get('openai_base_url', "https://yunwu.ai/v1"))

    # 添加 "获取模型列表" 按钮
    if st.button("获取模型列表"):
        client = OpenAI(api_key=api_key, base_url=api_base)
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

    # 添加是否携带历史会话的开关
    include_history = st.toggle("携带历史会话", value=saved_config.get('include_history', False))
    st.session_state.include_history = include_history

    # 添加历史会话条目数的配置
    if include_history:
        history_count = st.number_input(
            "历史会话条目数",
            min_value=1,
            max_value=20,
            value=saved_config.get('history_count', 5),
            help="设置要携带的历史会话条目数（1-20条）"
        )
    else:
        history_count = 5

    # 添加是否启用联网搜索的开关
    enable_search = st.toggle("启用联网搜索", value=saved_config.get('enable_search', False))
    st.session_state.enable_search = enable_search

    # 只在启用联网搜索时显示搜索结果数量设置
    search_count = "15"
    if enable_search:
        search_count = st.text_input("搜索结果数量", value=saved_config.get('search_count', "15"))

    # 添加保存配置按钮
    if st.button("保存配置"):
        # 先读取现有配置
        current_config = load_config()
        # 更新需要修改的配置项
        if api_key:
            current_config['openai_api_key'] = api_key
        if api_base:
            current_config['openai_base_url'] = api_base
        current_config['model'] = selected_models[-1] if selected_models else st.session_state.selected_model
        current_config['search_count'] = search_count
        current_config['include_history'] = include_history
        current_config['history_count'] = history_count
        current_config['enable_search'] = enable_search
        
        # 保存更新后的配置
        if save_config(current_config):
            st.success("配置已保存")
        else:
            st.error("配置保存失败")

    # 添加导出会话功能
    if st.button("导出会话"):
        result = export_conversation_to_markdown()
        if result is not None:
            markdown_content, filename = result
            st.download_button(
                label="下载会话文件",
                data=markdown_content,
                file_name=filename,
                mime="text/markdown"
            )

# 添加处理deepseek模型返回结果的函数
def process_deepseek_response(response, model):
    """处理deepseek模型的返回结果，移除<think>标签"""
    if 'deepseek-r1' in model.lower():
        import re
        return re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    return response

def generate_search_query(user_input):
    """使用模型生成搜索关键词"""
    try:
        client = OpenAI(api_key=api_key, base_url=api_base)
        response = client.chat.completions.create(
            model=st.session_state.model,
            messages=[
                {"role": "user", "content": f"请根据以下用户问题生成适用于duckduckgo的搜索关键词或短语，如有多个则以空格分隔：\n{user_input}"}
            ]
        )
        
        if not response or not response.choices or not response.choices[0].message or not response.choices[0].message.content:
            logger.error("API返回结果为空")
            return user_input  # 如果API调用失败，直接使用用户输入作为搜索关键词
            
        result = response.choices[0].message.content.strip()
        return process_deepseek_response(result, st.session_state.model)
    except Exception as e:
        logger.error(f"生成搜索关键词失败: {str(e)}")
        return user_input  # 发生异常时，直接使用用户输入作为搜索关键词

def cleanup_chrome_processes():
    """清理所有残留的Chrome进程"""
    try:
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and ('chrom' in proc.info['name'].lower()):
                try:
                    proc.terminate()
                except:
                    pass
    except Exception as e:
        logger.error(f"清理Chrome进程失败: {str(e)}")

# 注册退出时的清理函数
atexit.register(cleanup_chrome_processes)

async def extract_webpage_content(url):
    """使用pyppeteer提取网页主要内容"""
    for attempt in range(2):  # 最多尝试2次
        browser = await launch({
            'headless': True,
            'handleSIGINT': False,  # 禁用SIGINT处理
            'handleSIGTERM': False,  # 禁用SIGTERM处理
            'handleSIGHUP': False,   # 禁用SIGHUP处理
            'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        })
        try:
            page = await browser.newPage()
            # 设置更长的超时时间（90秒）
            await page.goto(url, {
                'waitUntil': 'networkidle2',
                'timeout': 90000  # 90秒
            })
            content = await page.evaluate('''() => {
                return document.body.innerText;
            }''')
            await browser.close()
            content = '\n'.join(line.strip() for line in content.splitlines() if line.strip())
            content = ' '.join(content.split())
            return content
        except Exception as e:
            await browser.close()
            if attempt == 0:  # 第一次失败
                logger.warning(f"提取网页内容失败，正在重试: {str(e)}")
                await asyncio.sleep(2)  # 等待2秒后重试
                continue
            else:  # 第二次失败
                logger.error(f"提取网页内容两次尝试都失败: {str(e)}")
                return None

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

async def process_single_result(result, query, client, idx, total_results, progress_placeholder):
    """异步处理单个搜索结果"""
    try:
        # 显示当前进度
        progress_placeholder.info(f"正在处理搜索结果 {idx}: {result['title']}")
        
        # 提取网页内容
        content = await extract_webpage_content(result["href"])
        
        # 生成摘要
        if content:
            progress_placeholder.info(f"正在为搜索结果 {idx} 生成摘要...")
            summary = generate_content_summary(content, query, client)
        else:
            summary = result["body"]  # 如果无法提取内容，使用原始摘要
            
        return {
            "title": result["title"],
            "snippet": summary or result["body"],  # 如果摘要生成失败，使用原始摘要
            "url": result["href"]
        }
    except Exception as e:
        st.error(f"处理结果失败 ({result['href']}): {str(e)}")
        # 如果处理失败，使用原始结果
        return {
            "title": result["title"],
            "snippet": result["body"],
            "url": result["href"]
        }

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
        
        # 创建信号量限制并发数
        semaphore = asyncio.Semaphore(3)
        
        async def process_with_semaphore(result, idx):
            async with semaphore:
                return await process_single_result(
                    result, query, client, idx + 1, total_results, progress_placeholder
                )
        
        # 创建所有任务
        tasks = [
            process_with_semaphore(result, idx)
            for idx, result in enumerate(results)
        ]
        
        # 并行执行所有任务
        processed_results = loop.run_until_complete(asyncio.gather(*tasks))
        
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
    
    # 创建OpenAI客户端
    client = OpenAI(api_key=api_key, base_url=api_base)
    
    # 根据是否启用联网搜索执行不同的逻辑
    if st.session_state.enable_search:
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
        
        # 构建消息历史
        messages = []
        if st.session_state.include_history:
            # 获取最近的历史会话
            recent_messages = st.session_state.messages[:-1]  # 排除当前的用户消息
            start_idx = max(0, len(recent_messages) - history_count)
            messages.extend([{"role": m["role"], "content": m["content"]} 
                           for m in recent_messages[start_idx:]])
        
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
    else:
        # 不启用联网搜索时，直接使用对话历史
        messages = []
        if st.session_state.include_history:
            messages.extend([{"role": m["role"], "content": m["content"]} 
                           for m in st.session_state.messages[:-1]])
        
        # 添加系统提示词
        messages.append({
            "role": "system",
            "content": """你是一个AI助手。你的回答需要：
1. 运用自身知识回答问题
2. 回答要详尽
3. 使用中文回答"""
        })
        
        # 添加当前问题
        messages.append({"role": "user", "content": prompt})
    
    # 获取模型回复
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        response = client.chat.completions.create(
            model=st.session_state.model,
            messages=messages
        )
        
        full_response = response.choices[0].message.content
        #full_response = process_deepseek_response(full_response, st.session_state.model)
        message_placeholder.markdown(full_response)
    
    st.session_state.messages.append({"role": "assistant", "content": full_response})
   
    # 添加调试信息
    if st.session_state.enable_search:
        with st.expander("调试信息"):
            st.write("### 发送给模型的消息")
            for msg in messages:
                st.markdown(f"""
**角色**: {msg['role']}
**内容**:
```
{msg['content']}
```
---
""")
            
            st.write("### 搜索关键词")
            st.code(search_query)
            
            st.write("### 搜索结果")
            for i, res in enumerate(st.session_state.search_results):
                st.markdown(f"""
#### 来源{i+1}. [{res['title']}]({res['url']})
{res['snippet']}
---
""")
    else:
        with st.expander("调试信息"):
            st.write("### 发送给模型的消息")
            for msg in messages:
                st.markdown(f"""
**角色**: {msg['role']}
**内容**:
```
{msg['content']}
```
---
""")

