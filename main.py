# main.py
import os
import traceback
from chat_core import ChatCore
from logging_system import UnifiedLogger
from configuration_manager import ConfigurationManager
from message_processor import MessageProcessor
from prompt_manager import PromptManager
from chat_orchestrator import ChatOrchestrator

class MultiAIChatSystem:
    """重构后的多AI聊天系统主类"""
    
    def __init__(self):
        # 初始化核心组件
        self.logger = UnifiedLogger()
        
        self.config_manager = ConfigurationManager(self.logger)
        
        # ChatCore 实例（保持原有功能）- 修复初始化参数
        self.chat_core = ChatCore("api-config.json")
        
        # 先不初始化其他组件，等配置加载后再初始化
    
    def run(self):
        """运行系统"""
        try:
            # 加载配置
            self.config_manager.load_api_config("api-config.json")
            self.config_manager.load_tool_config("config.json")
            
            # 在配置加载后初始化其他组件
            self.message_processor = MessageProcessor(self.config_manager, self.logger)
            self.prompt_manager = PromptManager(self.config_manager, self.logger)
            
            # 协调器（在配置加载后初始化）
            self.orchestrator = ChatOrchestrator(
                self.config_manager,
                self.message_processor,
                self.prompt_manager,
                self.logger,
                self.chat_core
            )
            
            # 运行主循环
            self.orchestrator.run_main_loop()
            
        except Exception as e:
            self.logger.error(f"系统启动失败: {str(e)}")
            self.logger.error(traceback.format_exc())

if __name__ == "__main__":
    system = MultiAIChatSystem()
    system.run()