"""
修改群名插件

提供智能修改群名功能的群聊管理插件。

功能特性：
- 智能LLM判定：根据聊天内容智能判断是否需要修改群名
- 模板化消息：支持自定义修改群名提示消息
- 参数验证：完整的输入参数验证和错误处理
- 配置文件支持：所有设置可通过配置文件调整
- 权限管理：支持用户权限和群组权限控制

包含组件：
- 智能修改群名Action - 基于LLM判断是否需要修改群名（支持群组权限控制）
- 修改群名命令Command - 手动执行修改群名操作（支持用户权限控制）
"""

from typing import List, Tuple, Type, Optional
import random

from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.base_plugin import register_plugin
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.common.logger import get_logger
from src.plugin_system.apis import generator_api

logger = get_logger("change_group_name_plugin")

# ===== Action组件 =====

class ChangeGroupNameAction(BaseAction):
    """智能修改群名Action - 基于LLM智能判断是否需要修改群名"""

    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.KEYWORD
    mode_enable = ChatMode.ALL
    parallel_action = True

    action_name = "change_group_name"
    action_description = "智能修改群名系统，基于LLM判断是否需要修改群名"

    activation_keywords = ["改群名", "修改群名", "change group name", "rename group"]
    keyword_case_sensitive = False

    llm_judge_prompt = """
你是有温度的赛博群友，而非机械执行程序。修改群名决策需综合聊天语境和群组氛围判断
判定是否需要使用修改群名动作
修改群名动作的严格条件：

使用修改群名的情况：
1. 群主或管理员明确要求修改群名
2. 群名包含违规内容需要更改
3. 群名不符合群组主题需要优化
4. 特殊节日或活动需要临时更换群名

绝对不要使用的情况：
1. 没有明确授权的情况下擅自修改群名
2. 仅仅因为个人喜好而修改群名
3. 在没有讨论的情况下突然改变群名
"""

    action_parameters = {
        "new_name": "新的群名称，必填，请仔细确认新群名符合群组主题且不包含违规内容",
        "reason": "修改群名理由，可选",
    }

    action_require = [
        "当群主或管理员明确要求修改群名时使用",
        "当群名包含违规内容需要更改时使用",
        "当群名不符合群组主题需要优化时使用",
    ]

    associated_types = ["text", "command"]

    def _check_group_permission(self) -> Tuple[bool, Optional[str]]:
        if not self.is_group:
            return False, "修改群名动作只能在群聊中使用"
        allowed_groups = self.get_config("permissions.allowed_groups", [])
        if not allowed_groups:
            logger.info(f"{self.log_prefix} 群组权限未配置，允许所有群使用修改群名动作")
            return True, None
        current_group_key = f"{self.platform}:{self.group_id}"
        for allowed_group in allowed_groups:
            if allowed_group == current_group_key:
                logger.info(f"{self.log_prefix} 群组 {current_group_key} 有修改群名动作权限")
                return True, None
        logger.warning(f"{self.log_prefix} 群组 {current_group_key} 没有修改群名动作权限")
        return False, "当前群组没有使用修改群名动作的权限"

    async def execute(self) -> Tuple[bool, Optional[str]]:
        logger.info(f"{self.log_prefix} 执行智能修改群名动作")
        has_permission, permission_error = self._check_group_permission()
        new_name = self.action_data.get("new_name")
        reason = self.action_data.get("reason", "管理员操作")
        
        if not new_name:
            error_msg = "新群名不能为空"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_text("没有指定新群名呢~")
            return False, error_msg
            
        # 验证群名长度
        if len(new_name) > 20:
            error_msg = "群名过长，不能超过20个字符"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_text("群名太长啦，不能超过20个字符哦~")
            return False, error_msg
            
        message = self._get_template_message(new_name, reason)
        if not has_permission:
            logger.warning(f"{self.log_prefix} 权限检查失败: {permission_error}")
            result_status, result_message = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                reply_data={
                    "raw_reply": "我想把群名改为{new_name}，但是我没有权限",
                    "reason": "表达自己没有在这个群修改群名的能力",
                },
            )
            if result_status:
                for reply_seg in result_message:
                    data = reply_seg[1]
                    await self.send_text(data)
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"尝试修改群名为 {new_name}，但是没有权限，无法操作",
                action_done=True,
            )
            return False, permission_error
        result_status, result_message = await generator_api.rewrite_reply(
            chat_stream=self.chat_stream,
            reply_data={
                "raw_reply": message,
                "reason": reason,
            },
        )
        if result_status:
            for reply_seg in result_message:
                data = reply_seg[1]
                await self.send_text(data)
        # 发送群聊修改群名命令（使用 NapCat API）
        from src.plugin_system.apis import send_api
        group_id = self.group_id if hasattr(self, "group_id") else None
        platform = self.platform if hasattr(self, "platform") else "qq"
        if not group_id:
            error_msg = "无法获取群聊ID"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_text("执行修改群名动作失败（群ID缺失）")
            return False, error_msg
        # Napcat API 修改群名实现
        import httpx
        napcat_api = "http://127.0.0.1:3000/set_group_name"
        payload = {
            "group_id": str(group_id),
            "group_name": new_name
        }
        logger.info(f"{self.log_prefix} Napcat修改群名API请求: {napcat_api}, payload={payload}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(napcat_api, json=payload, timeout=5)
            logger.info(f"{self.log_prefix} Napcat修改群名API响应: status={response.status_code}, body={response.text}")
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("status") == "ok" and resp_json.get("retcode") == 0:
                    logger.info(f"{self.log_prefix} 成功修改群名，群: {group_id}，新群名: {new_name}")
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=f"尝试修改群名为 {new_name}，原因：{reason}",
                        action_done=True,
                    )
                    return True, f"成功修改群名为 {new_name}"
                else:
                    error_msg = f"Napcat API返回失败: {resp_json}"
                    logger.error(f"{self.log_prefix} {error_msg}")
                    await self.send_text("执行修改群名动作失败（API返回失败）")
                    return False, error_msg
            else:
                error_msg = f"Napcat API请求失败: HTTP {response.status_code}"
                logger.error(f"{self.log_prefix} {error_msg}")
                await self.send_text("执行修改群名动作失败（API请求失败）")
                return False, error_msg
        except Exception as e:
            error_msg = f"Napcat API请求异常: {e}"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_text("执行修改群名动作失败（API异常）")
            return False, error_msg

    def _get_template_message(self, new_name: str, reason: str) -> str:
        templates = self.get_config("change_name.templates")
        template = random.choice(templates)
        return template.format(new_name=new_name, reason=reason)

# ===== Command组件 =====

class ChangeGroupNameCommand(BaseCommand):
    """修改群名命令 - 手动执行修改群名操作"""
    command_name = "change_group_name_command"
    command_description = "修改群名命令，手动执行修改群名操作"
    command_pattern = r"^/change_group_name\s+(?P<new_name>.+?)(?:\s+(?P<reason>.+))?$"
    command_help = "修改群名，用法：/change_group_name <新群名> [理由]"
    command_examples = ["/change_group_name 新群名", "/change_group_name 新群名 更换主题"]
    intercept_message = True

    def _check_user_permission(self) -> Tuple[bool, Optional[str]]:
        chat_stream = self.message.chat_stream
        if not chat_stream:
            return False, "无法获取聊天流信息"
        current_platform = chat_stream.platform
        current_user_id = str(chat_stream.user_info.user_id)
        allowed_users = self.get_config("permissions.allowed_users", [])
        if not allowed_users:
            logger.info(f"{self.log_prefix} 用户权限未配置，允许所有用户使用修改群名命令")
            return True, None
        current_user_key = f"{current_platform}:{current_user_id}"
        for allowed_user in allowed_users:
            if allowed_user == current_user_key:
                logger.info(f"{self.log_prefix} 用户 {current_user_key} 有修改群名命令权限")
                return True, None
        logger.warning(f"{self.log_prefix} 用户 {current_user_key} 没有修改群名命令权限")
        return False, "你没有使用修改群名命令的权限"

    async def execute(self) -> Tuple[bool, Optional[str]]:
        try:
            has_permission, permission_error = self._check_user_permission()
            if not has_permission:
                logger.error(f"{self.log_prefix} 权限检查失败: {permission_error}")
                await self.send_text(f"❌ {permission_error}")
                return False, permission_error
            new_name = self.matched_groups.get("new_name")
            reason = self.matched_groups.get("reason", "管理员操作")
                
            if not new_name:
                await self.send_text("❌ 命令参数不完整，请检查格式")
                return False, "参数不完整"
                
            # 验证群名长度
            if len(new_name) > 20:
                await self.send_text("❌ 群名太长啦，不能超过20个字符哦~")
                return False, "群名过长"
                
            logger.info(f"{self.log_prefix} 执行修改群名命令: 新群名: {new_name}")
            # 发送群聊修改群名命令（使用 NapCat API）
            from src.plugin_system.apis import send_api
            group_id = self.message.chat_stream.group_info.group_id if self.message.chat_stream and self.message.chat_stream.group_info else None
            platform = self.message.chat_stream.platform if self.message.chat_stream else "qq"
            if not group_id:
                await self.send_text("❌ 无法获取群聊ID")
                return False, "群聊ID缺失"
            # Napcat API 修改群名实现
            import httpx
            napcat_api = "http://127.0.0.1:3000/set_group_name"
            payload = {
                "group_id": str(group_id),
                "group_name": new_name
            }
            logger.info(f"{self.log_prefix} Napcat修改群名API请求: {napcat_api}, payload={payload}")
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(napcat_api, json=payload, timeout=5)
                logger.info(f"{self.log_prefix} Napcat修改群名API响应: status={response.status_code}, body={response.text}")
                if response.status_code == 200:
                    resp_json = response.json()
                    if resp_json.get("status") == "ok" and resp_json.get("retcode") == 0:
                        message = self._get_template_message(new_name, reason)
                        await self.send_text(message)
                        logger.info(f"{self.log_prefix} 成功修改群名，群: {group_id}，新群名: {new_name}")
                        return True, f"成功修改群名为 {new_name}"
                    else:
                        error_msg = f"Napcat API返回失败: {resp_json}"
                        logger.error(f"{self.log_prefix} {error_msg}")
                        await self.send_text("❌ 发送修改群名命令失败（API返回失败）")
                        return False, error_msg
                else:
                    error_msg = f"Napcat API请求失败: HTTP {response.status_code}"
                    logger.error(f"{self.log_prefix} {error_msg}")
                    await self.send_text("❌ 发送修改群名命令失败（API请求失败）")
                    return False, error_msg
            except Exception as e:
                error_msg = f"Napcat API请求异常: {e}"
                logger.error(f"{self.log_prefix} {error_msg}")
                await self.send_text("❌ 发送修改群名命令失败（API异常）")
                return False, error_msg
        except Exception as e:
            logger.error(f"{self.log_prefix} 修改群名命令执行失败: {e}")
            await self.send_text(f"❌ 修改群名命令错误: {str(e)}")
            return False, str(e)

    def _get_template_message(self, new_name: str, reason: str) -> str:
        templates = self.get_config("change_name.templates")
        template = random.choice(templates)
        return template.format(new_name=new_name, reason=reason)

# ===== 插件主类 =====

@register_plugin
class ChangeGroupNamePlugin(BasePlugin):
    """修改群名插件
    提供智能修改群名功能：
    - 智能修改群名Action：基于LLM判断是否需要修改群名（支持群组权限控制）
    - 修改群名命令Command：手动执行修改群名操作（支持用户权限控制）
    """
    plugin_name = "change_group_name_plugin"
    enable_plugin = True
    config_file_name = "config.toml"
    config_section_descriptions = {
        "plugin": "插件基本信息配置",
        "components": "组件启用控制",
        "permissions": "权限管理配置",
        "change_name": "核心修改群名功能配置",
        "smart_change_name": "智能修改群名Action的专属配置",
        "change_name_command": "修改群名命令Command的专属配置",
        "logging": "日志记录相关配置",
    }
    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=False, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="0.0.1", description="配置文件版本"),
        },
        "components": {
            "enable_smart_change_name": ConfigField(type=bool, default=True, description="是否启用智能修改群名Action"),
            "enable_change_name_command": ConfigField(
                type=bool, default=False, description="是否启用修改群名命令Command（调试用）"
            ),
        },
        "permissions": {
            "allowed_users": ConfigField(
                type=list,
                default=[],
                description="允许使用修改群名命令的用户列表，格式：['platform:user_id']，如['qq:123456789']。空列表表示不启用权限控制",
            ),
            "allowed_groups": ConfigField(
                type=list,
                default=[],
                description="允许使用修改群名动作的群组列表，格式：['platform:group_id']，如['qq:987654321']。空列表表示不启用权限控制",
            ),
        },
        "change_name": {
            "enable_message_formatting": ConfigField(
                type=bool, default=True, description="是否启用人性化的消息显示"
            ),
            "log_change_name_history": ConfigField(type=bool, default=True, description="是否记录修改群名历史（未来功能）"),
            "templates": ConfigField(
                type=list,
                default=[
                    "好的，已将群名修改为 {new_name}，理由：{reason}",
                    "收到，将群名修改为 {new_name}，因为{reason}",
                    "明白了，群名已改为 {new_name}，原因是{reason}",
                    "已将群名修改为 {new_name}，理由：{reason}",
                    "群名修改完成，新群名为 {new_name}，原因：{reason}",
                ],
                description="成功修改群名后发送的随机消息模板",
            ),
            "error_messages": ConfigField(
                type=list,
                default=[
                    "没有指定新群名呢~",
                    "群名太长啦，不能超过20个字符哦~",
                    "修改群名时出现问题~",
                ],
                description="执行修改群名过程中发生错误时发送的随机消息模板",
            ),
        },
        "smart_change_name": {
            "strict_mode": ConfigField(type=bool, default=True, description="LLM判定的严格模式"),
            "keyword_sensitivity": ConfigField(
                type=str, default="normal", description="关键词激活的敏感度", choices=["low", "normal", "high"]
            ),
            "allow_parallel": ConfigField(type=bool, default=False, description="是否允许并行执行（暂未启用）"),
        },
        "change_name_command": {
            "max_batch_size": ConfigField(type=int, default=5, description="最大批量修改群名数量（未来功能）"),
            "cooldown_seconds": ConfigField(type=int, default=3, description="命令冷却时间（秒）"),
        },
        "logging": {
            "level": ConfigField(
                type=str, default="INFO", description="日志记录级别", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
            ),
            "prefix": ConfigField(type=str, default="[ChangeGroupNamePlugin]", description="日志记录前缀"),
            "include_user_info": ConfigField(type=bool, default=True, description="日志中是否包含用户信息"),
            "include_action_info": ConfigField(type=bool, default=True, description="日志中是否包含操作信息"),
        },
    }
    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        enable_smart_change_name = self.get_config("components.enable_smart_change_name", True)
        enable_change_name_command = self.get_config("components.enable_change_name_command", True)
        components = []
        if enable_smart_change_name:
            components.append((ChangeGroupNameAction.get_action_info(), ChangeGroupNameAction))
        if enable_change_name_command:
            components.append((ChangeGroupNameCommand.get_command_info(), ChangeGroupNameCommand))
        return components
