
"""组装单个用户可复用的 ThreatWeave 主 Agent 图。

本模块是 Agent 层的组合根：它选择后端、工具、子 Agent 和中间件，并将它们交给 ``create_deep_agent``。

图由 ``api.agent_loader`` 按 ``user_id`` 缓存。
同一用户的不同会话共用本图和沙箱代理，但通过不同 ``thread_id`` 使用独立的 checkpoint；
长期记忆则经由 ``/memories/`` 路由到该用户的 PostgreSQL Store 命名空间。
"""

import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    StoreBackend,
)
from langchain_core.runnables import RunnableConfig

from agent.subagents.async_registry import (
    get_async_subagent_instructions,
    get_async_subagent_specs,
)
from agent.subagents.loader import load_subagent
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.skill_sync import SandboxSkillSynchronizer
from agent.memory.prompts import system_prompt
from agent.config import (
    AGENTS_MD_FILENAME,
    MAIN_MODEL,
    MAIN_SKILLS_PATH,
    MAIN_AGENT_MODEL_RUN_LIMIT,
    MAIN_AGENT_TOOL_RUN_LIMIT,
    SKILLS_ROOT,
    SUMMARY_MODEL,
)
from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.context_injection import ContextInjectionMiddleware
from agent.schema import ThreatWeaveContext
from agent.tools.hitl_tools import request_additional_info
from agent.tools.mcp_client import load_common_tools, load_threatweave_tools
from agent.tools.skill_tools import create_skill_management_tools
from agent.tools.async_sandbox_tools import create_async_sandbox_tools
from agent.tools.threat_pipeline_tools import create_threat_pipeline_tools
from services.deliverables import create_write_deliverable_tool
from agent.middlewares.memory_update import MemoryUpdateMiddleware, ensure_preferences_file
from agent.middlewares.skill_management_visibility import (
    SkillManagementVisibilityMiddleware,
)
from agent.middlewares.async_task_visibility import AsyncTaskStatusVisibilityMiddleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware


# 异步 ThreatWeave 子 Agent 运行在独立的 Agent Protocol 服务中。
ASYNC_AGENT_PROTOCOL_URL = os.getenv(
    "MYAGENT_ASYNC_AGENT_PROTOCOL_URL",
    "http://127.0.0.1:18082",
)


# ============================================================
# 主 Agent 图装配
# ============================================================


async def create_main_agent(
    config: RunnableConfig,
    *,
    store,
    checkpointer,
    sandbox_backend: SandboxBackendProxy,
):
    """创建绑定用户身份、持久化资源和共享沙箱的主 Agent 图。

    参数：
        config: 调用配置；``configurable.user_id`` 决定记忆隔离范围。
        store: PostgreSQL 长期记忆与会话索引使用的 Store。
        checkpointer: 按 ``thread_id`` 保存和恢复 LangGraph 执行状态的组件。
        sandbox_backend: 用户级稳定沙箱代理。底层沙箱失效时管理器替换其内部实现，已缓存的 Agent 图无需重建。

    返回：
        已配置主工具、同步子 Agent、中间件和持久化后端的 DeepAgents 图。

    MCP 工具的发现包含异步客户端操作，因此工厂必须保持异步。异步 ThreatWeave
    任务共享当前用户的沙箱，但工具权限与会话状态彼此独立。
    """
    configurable = config.get("configurable", {})
    user_id = str(configurable.get("user_id", "anonymous"))
    # 主提示要求每轮读取偏好文件，因此首次构图前必须提供默认文件。
    await ensure_preferences_file(store, user_id)

    # 文件路径决定存储位置：普通文件和命令执行进入 OpenSandbox；
    # 只有 /memories/ 下的文件写入 PostgreSQL。
    # user_id 是 Store namespace 的唯一隔离键。
    backend = CompositeBackend(
        default=sandbox_backend,
        routes={
            "/memories/": StoreBackend(
                namespace=lambda rt: (user_id,),
                store=store,
            ),
        },
    )

    # 同步器把宿主维护的技能和固定 AGENTS.md 增量复制到用户沙箱，供本次 run 发现。
    skill_synchronizer = SandboxSkillSynchronizer(
        SKILLS_ROOT,
        Path(__file__).parent / "memory" / "AGENTS.md",
    )

    common_tools = await load_common_tools()
    _, read_tools = await load_threatweave_tools({"describe_read_model", "execute_read_query"})
    threat_handle_subagent = load_subagent(
        Path(__file__).parent / "subagents" / "configs" / "threat_handle.yaml",
        available_tools=read_tools,
        local_tools=[
            *create_threat_pipeline_tools(user_id),
            create_write_deliverable_tool(sandbox_backend),
        ],
    )

    # 异步子 Agent 的图不嵌入主图。
    # 主 Agent 可提交、查询、列举和取消当前会话的异步任务；远端任务完成后，
    # 前端轮询会将交付物登记到当前会话。
    async_subagents = get_async_subagent_specs(ASYNC_AGENT_PROTOCOL_URL)
    async_sandbox_tools = create_async_sandbox_tools(
        async_subagents,
        sandbox_backend=sandbox_backend,
    )
    async_task_status_tools = [
        tool for tool in async_sandbox_tools if getattr(tool, "name", "") == "check_async_task"
    ]

    # 技能管理操作必须使用稳定代理，才能在沙箱恢复后继续生效。
    # 每次管理前先同步本地变更，避免下载、分配或删除操作覆盖开发者尚未同步的技能文件。
    skill_management_tools = create_skill_management_tools(
        SKILLS_ROOT,
        {
            "threat_handle",
            *(subagent["name"] for subagent in async_subagents),
        },
        sandbox_backend=sandbox_backend,
        synchronize=lambda: skill_synchronizer.sync(sandbox_backend),
    )

    # 主 Agent 可用：公共网络搜索、异步任务提交和通用信息补齐。情报文档、实体关系及
    # 图谱数据访问仅由同步编排子 Agent 或相应异步子 Agent 获得。
    # 未显式传入 general-purpose 时，DeepAgents 自动装配其默认通用子 Agent，并按框架规则
    # 继承 main_tools，不在本项目覆盖其配置。
    main_tools = [
        *common_tools,
        *async_sandbox_tools,
        request_additional_info,
        # 主 Agent 可以自行改写用户要求的 HTML/Markdown；统一通过该工具登记下载入口。
        create_write_deliverable_tool(sandbox_backend),
    ]

    # 主 Agent 是唯一对外的同步执行图。
    # 中间件顺序具有语义：before_* 按声明顺序运行，after_* 反序运行，wrap_model_call 按洋葱模型嵌套；
    # 因此先准备身份和技能，再施加调用保护，最后按当前请求决定是否暴露内部技能管理工具。
    return create_deep_agent(
        model=MAIN_MODEL,
        system_prompt=f"{system_prompt}\n{get_async_subagent_instructions()}",
        memory=[AGENTS_MD_FILENAME],
        tools=main_tools,
        # threat_handle 作为本地同步子 Agent；threat_analyst 保持独立异步执行。
        subagents=[threat_handle_subagent, *async_subagents],
        middleware=[
            # 将运行上下文中的用户身份与记忆路径加入模型可见的系统上下文。
            ContextInjectionMiddleware(),
            # 在 DeepAgents 读取技能元数据前完成同步，保证本次 run 使用最新技能副本。
            SandboxSkillsMiddleware(
                backend=backend,
                sources=[MAIN_SKILLS_PATH],
                synchronizer=skill_synchronizer,
            ),
            # 主 Agent 同时具备自动摘要、主动压缩和模型/工具调用上限；
            # 主动压缩与自动摘要共享同一状态，避免两套摘要上下文产生偏差。
            *build_agent_protection_middleware(
                backend=backend,
                summary_model=SUMMARY_MODEL,
                model_run_limit=MAIN_AGENT_MODEL_RUN_LIMIT,
                tool_run_limit=MAIN_AGENT_TOOL_RUN_LIMIT,
                enable_compaction_tool=True,
            ),
            # 普通威胁情报对话隐藏技能维护工具；仅技能相关请求临时获得这些工具 schema。
            SkillManagementVisibilityMiddleware(skill_management_tools),
            # 后台任务的自动轮询会让主 Agent重复转述同一分析结果；只有用户主动
            # 询问状态或进度时，才让模型使用 check_async_task。
            AsyncTaskStatusVisibilityMiddleware(async_task_status_tools),
            # after_agent 阶段先执行，用最终对话结果更新用户长期偏好和近期查询。
            MemoryUpdateMiddleware(SUMMARY_MODEL),
        ],
        # 主图只发现主技能目录；每个子 Agent 从自己的配置读取独立技能目录。
        skills=[MAIN_SKILLS_PATH],
        backend=backend,
        store=store,
        checkpointer=checkpointer,
        context_schema=ThreatWeaveContext,
    )
