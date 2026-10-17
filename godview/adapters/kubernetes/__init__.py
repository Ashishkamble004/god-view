"""God-View v4 Kubernetes Sense Adapter package."""

from godview.adapters.kubernetes.adapter import (
    SYSTEM_NAMESPACES,
    KubernetesSenseAdapter,
    McpToolClientProtocol,
    parse_prometheus_text,
)

__all__ = [
    "KubernetesSenseAdapter",
    "McpToolClientProtocol",
    "SYSTEM_NAMESPACES",
    "parse_prometheus_text",
]
