"""异步图表任务状态接口测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.agent_loader import AgentLoader
from agent.schema import AsyncTaskBinding
from api.async_tasks import (
    _extract_deliverables,
    _extract_task_deliverables,
    _sanitize_task_content,
    extract_async_task_id,
    get_async_task_status,
)
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.memory.prompts import system_prompt
from agent.subagents.async_registry import get_async_subagent_instructions
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime


def _configured_tool(name: str):
    """创建可被 DeepAgents 编译的最小工具，避免网络依赖。"""

    def test_tool() -> str:
        """测试用工具。"""
        return "ok"

    test_tool.__name__ = name
    return tool(test_tool)


DELIVERABLE_LINE = "DELIVERABLE: /deliverables/threat-report.md | text/markdown | 下载威胁分析报告"


class AsyncTaskStatusTests(unittest.IsolatedAsyncioTestCase):
    """验证终态远程结果只经主会话投递。"""

    async def asyncSetUp(self) -> None:
        self.binding_patch = patch(
            "api.async_tasks.agent_loader.get_async_task_binding",
            new=AsyncMock(return_value=AsyncTaskBinding("task-1", "u1", "张三", "thread-1")),
        )
        self.binding_patch.start()
        self.addCleanup(self.binding_patch.stop)
        self.session_patch = patch("api.async_tasks.agent_loader.require_session", new=AsyncMock())
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    async def test_delivers_multiple_sandbox_deliverables_to_main_thread(self) -> None:
        """完成任务应登记多个交付件并投递主会话。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(
                list=AsyncMock(return_value=[{"status": "success", "run_id": "run-1"}])
            ),
            threads=SimpleNamespace(
                get_state=AsyncMock(
                    return_value=SimpleNamespace(
                        values={
                            "messages": [
                                {"role": "human", "content": "生成采购分析报告和趋势图"},
                                {"role": "assistant", "content": (
                                    "分析完成。\n"
                                    "DELIVERABLE: /deliverables/threat-report.md | text/markdown | 下载威胁分析报告\n"
                                    "DELIVERABLE: /deliverables/threat-graph.html | text/html | 打开威胁关系图"
                                )},
                            ]
                        }
                    )
                )
            ),
        )

        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch(
                "api.async_tasks.agent_loader.register_sandbox_deliverables",
                new=AsyncMock(return_value=[
                    {"artifact_id": "b" * 32, "filename": "threat-report.md", "mime_type": "text/markdown", "label": "下载威胁分析报告"},
                    {"artifact_id": "c" * 32, "filename": "threat-graph.html", "mime_type": "text/html", "label": "打开威胁关系图"},
                ]),
            ) as register,
            patch(
                "api.async_tasks.agent_loader.publish_async_task_result",
                new=AsyncMock(return_value=True),
            ) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertTrue(response.done)
        self.assertTrue(response.delivered)
        self.assertEqual(len(response.deliverables), 2)
        self.assertEqual(response.deliverables[1].preview_src, "/deliverables/" + "c" * 32 + "?user_id=u1&preview=1")
        register.assert_awaited_once()
        self.assertEqual(len(publish.await_args.kwargs["deliverables"]), 2)

    async def test_chart_only_task_does_not_require_or_publish_report(self) -> None:
        """只要求图表时，成功终态不能凭空出现报告下载入口。"""
        artifact_id = "c" * 32
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])) ,
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "基于采购订单画一个月度趋势图"},
                    {"role": "assistant", "content": json.dumps({
                        "type": "chart_artifact", "artifact_id": artifact_id, "mime_type": "text/html",
                    })},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "success")
        self.assertEqual(response.deliverables, [])
        self.assertEqual(response.result, "主 Agent 已完成结果整理。")
        self.assertEqual(publish.await_args.kwargs["deliverables"], [])

    async def test_successful_delivery_does_not_expose_raw_subagent_text_in_task_card(self) -> None:
        """主 Agent 已接管结果时，状态接口不能再返回子 Agent 原文。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "查询库内文章"},
                    {"role": "assistant", "content": "子 Agent 的原始调查正文。"},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.result, "主 Agent 已完成结果整理。")
        self.assertEqual(publish.await_args.kwargs["content"], "子 Agent 的原始调查正文。")

    async def test_failed_legacy_update_keeps_prior_successful_deliverables(self) -> None:
        """旧 update_async_task 追加失败运行时，不能覆盖同线程已成功的图表。"""
        html_spec = {
            "type": "deliverable_spec",
            "path": "/deliverables/threat-graph.html",
            "filename": "threat-graph.html",
            "mime_type": "text/html",
            "label": "打开关系图",
        }
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[
                {"status": "error", "run_id": "update-failed"},
                {"status": "success", "run_id": "original-success"},
            ])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "只生成 HTML 图"},
                    {"type": "tool", "name": "write_deliverable", "content": json.dumps(html_spec)},
                    {"role": "assistant", "content": "图表已生成。"},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch(
                "api.async_tasks.agent_loader.register_sandbox_deliverables",
                new=AsyncMock(return_value=[{
                    "artifact_id": "d" * 32,
                    "filename": "threat-graph.html",
                    "mime_type": "text/html",
                    "label": "打开关系图",
                }]),
            ),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)),
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "success")
        self.assertEqual(response.run_id, "original-success")
        self.assertEqual([item.filename for item in response.deliverables], ["threat-graph.html"])

    async def test_registers_all_actual_deliverables_without_keyword_inference(self) -> None:
        """交付件以子 Agent 实际写入结果为准，不按用户文本关键词筛选。"""
        markdown_spec = {
            "type": "deliverable_spec",
            "path": "/deliverables/unrequested-report.md",
            "filename": "unrequested-report.md",
            "mime_type": "text/markdown",
            "label": "分析报告",
        }
        html_spec = {
            "type": "deliverable_spec",
            "path": "/deliverables/requested-graph.html",
            "filename": "requested-graph.html",
            "mime_type": "text/html",
            "label": "请求的图谱",
        }
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "只生成 HTML 图，不要 Markdown 报告"},
                    {"type": "tool", "name": "write_deliverable", "content": json.dumps(markdown_spec)},
                    {"type": "tool", "name": "write_deliverable", "content": json.dumps(html_spec)},
                    {"role": "assistant", "content": "图谱已生成。"},
                ],
            }))),
        )

        async def register(_task_id, specifications):
            return [{
                "artifact_id": "c" * 32,
                "filename": specification["filename"],
                "mime_type": specification["mime_type"],
                "label": specification["label"],
            } for specification in specifications]

        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch(
                "api.async_tasks.agent_loader.register_sandbox_deliverables",
                new=AsyncMock(side_effect=register),
            ) as register_deliverables,
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)),
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(
            [item.filename for item in response.deliverables],
            ["unrequested-report.md", "requested-graph.html"],
        )
        self.assertEqual(
            [item["filename"] for item in register_deliverables.await_args.args[1]],
            ["unrequested-report.md", "requested-graph.html"],
        )

    async def test_plain_text_task_with_report_negation_remains_successful(self) -> None:
        """任务约束提及未要求报告时，纯文本结果不能被改写为失败。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "没有要求报告，只返回库内文章列表。"},
                    {"role": "assistant", "content": "库内共有 4 篇文章。"},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)),
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "success")
        self.assertIsNone(response.error)

    async def test_registers_repeated_deliverables_written_by_subagent(self) -> None:
        """同类型文件也应按子 Agent 实际写入结果完整登记。"""
        first_html = {
            "type": "deliverable_spec",
            "path": "/deliverables/old-graph.html",
            "filename": "old-graph.html",
            "mime_type": "text/html",
            "label": "旧图谱",
        }
        latest_html = {
            "type": "deliverable_spec",
            "path": "/deliverables/latest-graph.html",
            "filename": "latest-graph.html",
            "mime_type": "text/html",
            "label": "最新图谱",
        }
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "重新生成一个 HTML 关系图"},
                    {"type": "tool", "name": "write_deliverable", "content": json.dumps(first_html)},
                    {"type": "tool", "name": "write_deliverable", "content": json.dumps(latest_html)},
                    {"role": "assistant", "content": "图谱已重新生成。"},
                ],
            }))),
        )

        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch(
                "api.async_tasks.agent_loader.register_sandbox_deliverables",
                new=AsyncMock(return_value=[
                    {
                        "artifact_id": "d" * 32,
                        "filename": "old-graph.html",
                        "mime_type": "text/html",
                        "label": "旧图谱",
                    },
                    {
                        "artifact_id": "e" * 32,
                        "filename": "latest-graph.html",
                        "mime_type": "text/html",
                        "label": "最新图谱",
                    },
                ]),
            ) as register_deliverables,
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)),
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(
            [item.filename for item in response.deliverables],
            ["old-graph.html", "latest-graph.html"],
        )
        self.assertEqual(
            [item["filename"] for item in register_deliverables.await_args.args[1]],
            ["old-graph.html", "latest-graph.html"],
        )

    async def test_report_request_without_file_keeps_successful_text_result(self) -> None:
        """是否生成文件由子 Agent 决定，成功文本结果不因报告关键词失败。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "生成采购分析报告"},
                    {"role": "assistant", "content": "分析完成，但未写入报告"},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "success")
        self.assertIsNone(response.error)
        self.assertEqual(publish.await_args.kwargs["content"], "分析完成，但未写入报告")

    async def test_missing_requested_report_does_not_create_api_fallback(self) -> None:
        """API 不代写交付件，子 Agent 的成功文本结果应原样投递。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(get_state=AsyncMock(return_value=SimpleNamespace(values={
                "messages": [
                    {"role": "human", "content": "生成 Markdown 威胁分析报告和 HTML 关系图"},
                    {"type": "tool", "name": "execute_read_query", "content": json.dumps({
                        "rows": [{"id": 1, "canonical_value": "OpenClaw"}], "rowCount": 1,
                    })},
                    {"role": "assistant", "content": "分析完成，但未写入文件"},
                ],
            }))),
        )
        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch("api.async_tasks.agent_loader.publish_async_task_result", new=AsyncMock(return_value=True)) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "success")
        self.assertIsNone(response.error)
        self.assertEqual(response.deliverables, [])
        self.assertEqual(publish.await_args.kwargs["content"], "分析完成，但未写入文件")

    async def test_returns_pending_when_remote_thread_has_no_runs(self) -> None:
        """尚未物化运行记录时，前端应继续轮询而不是视为失败。"""
        client = SimpleNamespace(runs=SimpleNamespace(list=AsyncMock(return_value=[])))

        with patch("api.async_tasks.get_client", return_value=client):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "pending")
        self.assertFalse(response.done)

    async def test_run_limit_error_is_not_delivered_as_a_successful_report(self) -> None:
        """框架的调用限额终态必须显示为可理解的失败，而非原始内部错误。"""
        client = SimpleNamespace(
            runs=SimpleNamespace(list=AsyncMock(return_value=[{"status": "success"}])),
            threads=SimpleNamespace(
                get_state=AsyncMock(return_value=SimpleNamespace(values={
                    "messages": [{
                        "role": "assistant",
                        "content": "Tool call limit reached: run limit exceeded (11/10 calls).",
                    }],
                }))
            ),
        )

        with (
            patch("api.async_tasks.get_client", return_value=client),
            patch(
                "api.async_tasks.agent_loader.publish_async_task_result",
                new=AsyncMock(return_value=True),
            ) as publish,
        ):
            response = await get_async_task_status("task-1", user_id="u1")

        self.assertEqual(response.status, "error")
        self.assertIn("执行限额", response.error)
        self.assertNotIn("Tool call", response.result)
        self.assertIn("后台任务未完成", response.result)
        self.assertNotIn("Tool call", publish.await_args.kwargs["content"])

    def test_extracts_agent_protocol_task_id_from_tool_result(self) -> None:
        """主聊天流应能从异步工具返回的嵌套文本中登记任务归属。"""
        task_id = "12fd2b03-f2c1-4b80-a8ca-9cb91bc43ccd"
        self.assertEqual(
            extract_async_task_id({"result": [f"任务已创建：{task_id}"]}),
            task_id,
        )

    def test_removes_internal_artifact_references_from_task_report(self) -> None:
        """图表链接由响应字段提供，报告正文不能泄露资源标识或沙箱路径。"""
        content = _sanitize_task_content(
            "结论：采购金额集中。\n• **资源 ID**： abcdef0123456789abcdef0123456789\n"
            "• **静态 HTML 文件**： /mnt/procurement_chart.html\n建议：复核大额订单。"
        )
        self.assertEqual(content, "结论：采购金额集中。\n建议：复核大额订单。")

    def test_extracts_only_standard_sandbox_deliverables(self) -> None:
        """下载登记只能接受受控目录、MIME 类型和文件名组成的交付协议。"""
        self.assertEqual(_extract_deliverables(DELIVERABLE_LINE)[0]["path"], "/deliverables/threat-report.md")
        self.assertEqual(_extract_deliverables("DELIVERABLE: /tmp/report.md | text/markdown | 报告"), [])

    def test_prefers_structured_write_deliverable_result(self) -> None:
        """新 C 交付件从工具结果读取，不依赖模型自由文本。"""
        values = {"messages": [{
            "name": "write_deliverable",
            "content": json.dumps({
                "type": "deliverable_spec",
                "path": "/deliverables/report.md",
                "filename": "report.md",
                "mime_type": "text/markdown",
                "label": "分析报告",
            }),
        }]}
        deliverables = _extract_task_deliverables(values, "DELIVERABLE: /tmp/invalid.md | text/markdown | 无效")
        self.assertEqual(deliverables[0]["path"], "/deliverables/report.md")

class AsyncTaskDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """验证 AgentLoader 向主 checkpoint 投递异步结果的边界。"""

    async def test_publishes_main_message_once_for_repeated_polling(self) -> None:
        """固定消息 ID 应让重复轮询保持幂等。"""
        task_id = "12fd2b03-f2c1-4b80-a8ca-9cb91bc43ccd"
        store = SimpleNamespace(
            aget=AsyncMock(
                return_value=SimpleNamespace(
                    value={"user_id": "u1", "username": "张三", "thread_id": "thread-1"}
                )
            )
        )
        agent = SimpleNamespace(
            ainvoke=AsyncMock(return_value={
                "messages": [AIMessage(id="main-final", content="主 Agent 的最终回复。")],
            }),
            aupdate_state=AsyncMock(),
        )
        loader = AgentLoader()
        loader._initialized = True
        loader._store = store
        loader._history_reader = SimpleNamespace(
            get_state=AsyncMock(
                side_effect=[
                    SimpleNamespace(values={"messages": []}, next=(), interrupts=()),
                    SimpleNamespace(values={"messages": [SimpleNamespace(id=f"async-task-result:{task_id}")]}, next=(), interrupts=()),
                ]
            )
        )

        with (
            patch.object(loader, "get_agent_for_user", new=AsyncMock(return_value=agent)),
            patch.object(loader, "save_session", new=AsyncMock()),
        ):
            delivered = await loader.publish_async_task_result(
                task_id,
                content="图表已生成。",
                artifact={"type": "chart_artifact", "artifact_id": "a" * 32, "mime_type": "text/html"},
                deliverables=[{"artifact_id": "b" * 32, "filename": "report.md", "mime_type": "text/markdown", "label": "下载报告"}],
            )
            delivered_again = await loader.publish_async_task_result(
                task_id,
                content="图表已生成。",
                artifact={"type": "chart_artifact", "artifact_id": "a" * 32, "mime_type": "text/html"},
                deliverables=[{"artifact_id": "b" * 32, "filename": "report.md", "mime_type": "text/markdown", "label": "下载报告"}],
            )

        self.assertTrue(delivered)
        self.assertTrue(delivered_again)
        self.assertEqual(agent.aupdate_state.await_count, 1)
        message = agent.aupdate_state.await_args.args[1]["messages"][0]
        self.assertEqual(message.id, "main-final")
        self.assertEqual(message.additional_kwargs["source"], "main")
        self.assertEqual(message.content[1]["artifact_id"], "a" * 32)
        self.assertEqual(message.content[2]["artifact_id"], "b" * 32)

    async def test_async_result_is_rewritten_by_main_agent_before_delivery(self) -> None:
        """异步子 Agent 原文必须由主 Agent 整理后才能进入主会话。"""
        task_id = "12fd2b03-f2c1-4b80-a8ca-9cb91bc43ccd"
        store = SimpleNamespace(
            aget=AsyncMock(
                return_value=SimpleNamespace(
                    value={"user_id": "u1", "username": "张三", "thread_id": "thread-1"}
                )
            )
        )
        agent = SimpleNamespace(
            ainvoke=AsyncMock(return_value={
                "messages": [
                    AIMessage(id="main-final", content="这是主 Agent 整理后的结论。"),
                ]
            }),
            aupdate_state=AsyncMock(),
        )
        loader = AgentLoader()
        loader._initialized = True
        loader._store = store
        loader.get_thread_state = AsyncMock(return_value=SimpleNamespace(
            values={"messages": []}, next=(), interrupts=(),
        ))
        loader.get_session = AsyncMock(return_value={"thread_id": "thread-1"})
        loader.get_agent_for_user = AsyncMock(return_value=agent)
        loader.save_session = AsyncMock()

        delivered = await loader.publish_async_task_result(
            task_id,
            content="子 Agent 的原始分析结果。",
            artifact=None,
            deliverables=[],
        )

        self.assertTrue(delivered)
        agent.ainvoke.assert_awaited_once()
        internal_message = agent.ainvoke.await_args.args[0]["messages"][0]
        self.assertEqual(internal_message.additional_kwargs["async_task_id"], task_id)
        self.assertIn("子 Agent 的原始分析结果。", internal_message.content)
        delivered_message = agent.aupdate_state.await_args.args[1]["messages"][0]
        self.assertEqual(delivered_message.id, "main-final")
        self.assertEqual(delivered_message.content[0]["text"], "这是主 Agent 整理后的结论。")


class NonStreamingAsyncTaskBindingTests(unittest.IsolatedAsyncioTestCase):
    """``/chat`` 必须绑定与 ``/chat/stream`` 相同的任务 ID。"""

    async def test_binds_task_id_from_final_tool_message(self) -> None:
        """已完成的 ``ainvoke`` 结果必须登记其异步任务归属。"""
        task_id = "12fd2b03-f2c1-4b80-a8ca-9cb91bc43ccd"
        result = {
            "messages": [
                SimpleNamespace(name="start_async_task", content=f"task_id: {task_id}"),
                SimpleNamespace(type="ai", content="任务已提交。"),
            ]
        }

        with patch("api.chat.agent_loader.bind_async_task", new=AsyncMock()) as bind:
            from api.chat import _bind_async_tasks_from_result

            await _bind_async_tasks_from_result(
                result,
                user_id="u1",
                username="张三",
                thread_id="thread-1",
            )

        bind.assert_awaited_once_with(
            task_id,
            user_id="u1",
            username="张三",
            thread_id="thread-1",
        )
