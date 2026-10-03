"""修正本机损坏的代理绕过列表（NO_PROXY），避免所有 HTTP 客户端构造失败。

当前机器的 NO_PROXY 环境变量含带方括号的 IPv6 条目，例如 ``[::1]``。httpx/httpx2
的 ``get_environment_proxies`` 会把 ``[::1]`` 误判为普通主机名并生成无效挂载
``all://*[::1]``，构造任何默认 ``trust_env`` 的客户端（模型、MCP、LangGraph SDK）
时都会抛出 ``InvalidURL``。这里只剔除这类方括号 IPv6 条目（转换为无方括号的规范
形式或直接忽略），其余代理配置保持不变。

CPython 在解释器启动时、任何业务代码之前导入 ``sitecustomize``。本仓库所有进程
（FastAPI、MCP、Agent Protocol、调度器、转换脚本与测试）都将 ``src`` 加入
``PYTHONPATH``，因此该模块会在这些进程中最先运行，覆盖全部 HTTP 客户端入口。
"""

from __future__ import annotations

import os
import re

# 形如 [::1]、[2001:db8::1] 的带方括号 IPv6 条目。
_BRACKETED_IPV6 = re.compile(r"^\[([0-9a-fA-F:]+)\]$")


def _sanitize_no_proxy(value: str) -> str:
    """去掉 NO_PROXY 中带方括号的 IPv6 条目，保留其余主机名。"""
    cleaned: list[str] = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        match = _BRACKETED_IPV6.match(entry)
        # 无方括号的 ::1 由 httpx 正确处理（all://[::1]），因此仅转换带方括号的形式。
        cleaned.append(match.group(1) if match else entry)
    return ",".join(cleaned)


def _apply() -> None:
    """进程启动时规范化 NO_PROXY/no_proxy，防止客户端构造崩溃。"""
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if not current or "[" not in current:
        return
    cleaned = _sanitize_no_proxy(current)
    # Windows 环境变量大小写不敏感，只写一个规范键；同时设置多个大小写变体
    # 会导致部分工具（如 PowerShell Start-Process）在构建环境块时发生键冲突。
    os.environ["NO_PROXY"] = cleaned


_apply()
