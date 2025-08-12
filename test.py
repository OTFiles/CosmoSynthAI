# 其他程序中调用示例
from chat_core import (
    load_configs, 
    run_chat_session, 
    attach_file, 
    save_session, 
    load_session,
    APIConnectionError,    # 新增
    APIResponseError,       # 新增
    FileTooLargeError,      # 新增
    ConfigLoadError         # 新增
)

# 可以设置日志级别（可选）
import logging
logging.basicConfig(level=logging.DEBUG)  # 如需更详细日志

# 加载配置
try:
    configs = load_configs("config.txt")
except ConfigLoadError as e:
    print(f"配置加载失败: {str(e)}")
    exit()

# 创建新会话
session = [
    {"role": "user", "content": "解释量子纠缠{{:Fphysics.txt}}"}
]

# 在发送前处理文件标记
try:
    session[0]["content"] = attach_file(session[0]["content"], "physics.txt")
except FileNotFoundError as e:
    print(f"错误: {str(e)}")
    exit()
except FileTooLargeError as e:
    print(f"错误: {str(e)}")
    exit()

# 运行会话 (使用第一个配置)
try:
    updated_session, ai_reply = run_chat_session(configs, session, 1)
    
    # 保存会话
    save_session(updated_session, "quantum_chat.json")
    
    print(f"AI回复: {ai_reply}")
    
except APIConnectionError as e:
    print(f"API连接错误: {str(e)}")
except APIResponseError as e:
    print(f"API响应错误: {str(e)}")