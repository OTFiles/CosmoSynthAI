import os
import sys
import time

def parse_log_line(line):
    """
    解析日志行并提取内容部分
    
    参数:
        line: 日志行字符串
        
    返回:
        解析后的内容部分，如果格式无效则返回None
    """
    # 解析日志行格式: timestamp - model - provider - content - streaming
    parts = line.strip().split(' - ', 4)  # 最多分割4次
    
    # 确保有足够的部分（至少5部分）
    if len(parts) >= 5:
        # 获取内容部分（索引3）
        content = parts[3]
        
        # 恢复换行符（将占位符替换回实际换行符）
        content = content.replace('\\n', '\n').replace('\\r', '\r')
        return content
    
    return None

def output_historical_content(log_file):
    """
    输出日志文件的全部历史内容
    
    参数:
        log_file: 日志文件路径
    """
    print("=== 历史输出开始 ===")
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                content = parse_log_line(line)
                if content:
                    print(content, end='', flush=True)
    except Exception as e:
        print(f"\n读取历史输出时出错: {str(e)}")
    
    print("\n=== 历史输出结束 ===")

def monitor_new_content(log_file, last_size):
    """
    监控日志文件的新增内容并输出
    
    参数:
        log_file: 日志文件路径
        last_size: 上次读取的文件大小
        
    返回:
        更新后的文件大小
    """
    try:
        current_size = os.path.getsize(log_file)
        
        # 如果文件被截断或重新创建
        if current_size < last_size:
            last_size = 0
        
        # 如果有新内容
        if current_size > last_size:
            with open(log_file, 'r', encoding='utf-8') as f:
                # 跳转到上次读取位置
                f.seek(last_size)
                
                # 读取所有新增行
                lines = f.readlines()
                for line in lines:
                    content = parse_log_line(line)
                    if content:
                        print(content, end='', flush=True)
                
                # 更新最后位置
                last_size = f.tell()
    
    except FileNotFoundError:
        # 文件可能被临时删除，等待重试
        time.sleep(1)
    except Exception as e:
        print(f"读取新内容时出错: {str(e)}")
    
    return last_size

def tail_ai_output(log_file):
    """
    实时读取AI输出日志文件并仅输出AI内容部分
    启动时输出文件的全部内容，然后监控新增内容
    正确处理换行符
    
    参数:
        log_file: 日志文件路径
    """
    # 确保日志文件存在
    if not os.path.exists(log_file):
        print(f"日志文件不存在: {log_file}")
        sys.exit(1)
    
    # 首先输出文件的全部内容
    output_historical_content(log_file)
    print("开始实时监控...\n")
    
    # 获取当前文件大小（准备监控新增内容）
    last_size = os.path.getsize(log_file)
    
    try:
        while True:
            last_size = monitor_new_content(log_file, last_size)
            # 短暂休眠
            time.sleep(0.1)
                
    except KeyboardInterrupt:
        print("\n程序已终止")
        sys.exit(0)
    except Exception as e:
        print(f"发生错误: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    # 日志文件路径（与chat_core.py中的定义一致）
    LOG_DIR = "logs"
    AI_OUTPUT_LOG = os.path.join(LOG_DIR, "AIoutput.log")
    
    # 确保日志目录存在
    os.makedirs(LOG_DIR, exist_ok=True)
    
    print(f"开始监控AI输出日志: {AI_OUTPUT_LOG}")
    print("按 Ctrl+C 停止监控\n")
    
    tail_ai_output(AI_OUTPUT_LOG)