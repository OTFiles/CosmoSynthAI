# logging_system.py
import os
import json
import time
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import threading
from pathlib import Path

class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class LogType(Enum):
    SYSTEM = "SYSTEM"
    AI_MESSAGE = "AI_MESSAGE"
    COMMAND = "COMMAND"
    ERROR = "ERROR"
    REJECTION = "REJECTION"

class UnifiedLogger:
    """统一日志系统，负责所有类型的日志记录"""
    
    def __init__(self, logs_dir: str = "logs", max_file_size: int = 10 * 1024 * 1024):  # 10MB
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)
        
        self.max_file_size = max_file_size
        self.current_log_file = self.logs_dir / "unified_system.log"
        self._lock = threading.Lock()
        
        # 颜色配置
        self.colors = {
            LogLevel.DEBUG: "\033[36m",     # Cyan
            LogLevel.INFO: "\033[32m",      # Green
            LogLevel.WARNING: "\033[33m",   # Yellow
            LogLevel.ERROR: "\033[31m",     # Red
            LogLevel.CRITICAL: "\033[35m",  # Magenta
        }
        self.reset_color = "\033[0m"
        
        self._rotate_if_needed()
    
    def _rotate_if_needed(self) -> None:
        """检查日志文件大小，必要时进行轮转"""
        if not self.current_log_file.exists():
            return
            
        if self.current_log_file.stat().st_size >= self.max_file_size:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.logs_dir / f"unified_system_{timestamp}.log"
            self.current_log_file.rename(backup_file)
    
    def _write_log_entry(self, entry: Dict[str, Any]) -> None:
        """写入日志条目到文件"""
        with self._lock:
            self._rotate_if_needed()
            
            try:
                with open(self.current_log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"写入日志文件失败: {str(e)}")
    
    def _format_console_output(self, level: LogLevel, message: str, 
                             log_type: LogType, ai_id: Optional[str] = None) -> str:
        """格式化控制台输出"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color = self.colors.get(level, "")
        
        base_output = f"{color}[{timestamp}][{level.value}][{log_type.value}]"
        if ai_id:
            base_output += f"[{ai_id}]"
        base_output += f" {message}{self.reset_color}"
        
        return base_output
    
    def _create_log_entry(self, level: LogLevel, message: str, 
                         log_type: LogType, ai_id: Optional[str] = None,
                         metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """创建标准化的日志条目"""
        return {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "level": level.value,
            "type": log_type.value,
            "ai_id": ai_id,
            "message": message,
            "metadata": metadata or {}
        }
    
    def log(self, level: LogLevel, message: str, log_type: LogType,
            ai_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        """记录日志的主方法"""
        log_entry = self._create_log_entry(level, message, log_type, ai_id, metadata)
        
        # 写入文件
        self._write_log_entry(log_entry)
        
        # 控制台输出
        console_output = self._format_console_output(level, message, log_type, ai_id)
        print(console_output)
    
    # 便捷方法
    def info(self, message: str, log_type: LogType = LogType.SYSTEM,
             ai_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.log(LogLevel.INFO, message, log_type, ai_id, metadata)
    
    def warning(self, message: str, log_type: LogType = LogType.SYSTEM,
                ai_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.log(LogLevel.WARNING, message, log_type, ai_id, metadata)
    
    def error(self, message: str, log_type: LogType = LogType.ERROR,
              ai_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.log(LogLevel.ERROR, message, log_type, ai_id, metadata)
    
    def debug(self, message: str, log_type: LogType = LogType.SYSTEM,
              ai_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.log(LogLevel.DEBUG, message, log_type, ai_id, metadata)
    
    def log_ai_message(self, ai_id: str, message: str, 
                      channels: List[str], metadata: Optional[Dict[str, Any]] = None) -> None:
        """记录AI消息"""
        channels_str = ",".join(channels)
        full_message = f"[{channels_str}] {message}"
        self.info(full_message, LogType.AI_MESSAGE, ai_id, metadata)
    
    def log_command(self, ai_id: str, command: str, 
                   result: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """记录命令执行"""
        message = f"执行命令: {command} -> {result}"
        self.info(message, LogType.COMMAND, ai_id, metadata)
    
    def log_rejection(self, ai_id: str, message: str, reason: str,
                     metadata: Optional[Dict[str, Any]] = None) -> None:
        """记录消息驳回"""
        full_message = f"消息被驳回: {message} (原因: {reason})"
        self.warning(full_message, LogType.REJECTION, ai_id, metadata)
    
    def get_recent_logs(self, count: int = 100, level: Optional[LogLevel] = None,
                       log_type: Optional[LogType] = None) -> List[Dict[str, Any]]:
        """获取最近的日志条目"""
        logs = []
        
        if not self.current_log_file.exists():
            return logs
        
        try:
            with open(self.current_log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            log_entry = json.loads(line.strip())
                            
                            # 过滤条件
                            if level and log_entry.get("level") != level.value:
                                continue
                            if log_type and log_entry.get("type") != log_type.value:
                                continue
                                
                            logs.append(log_entry)
                        except json.JSONDecodeError:
                            continue
            
            return logs[-count:]
        except Exception as e:
            self.error(f"读取日志文件失败: {str(e)}")
            return []