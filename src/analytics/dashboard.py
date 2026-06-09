import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from src.analytics.collector import AnalyticsCollector


class DashboardManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket, collector: AnalyticsCollector) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            while True:
                summary = collector.get_summary()
                await websocket.send_json(summary)
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass
        finally:
            self.active_connections.remove(websocket)
