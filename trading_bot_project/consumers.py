import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from trading.models import BacktestTask
from trading.passphrase import is_passphrase_verified


class BacktestConsumer(AsyncWebsocketConsumer):
    @database_sync_to_async
    def user_can_access(self, task_id):
        user = self.scope.get("user")
        return bool(
            user
            and user.is_authenticated
            and (
                not settings.PASSPHRASE_GATE_ENABLED
                or is_passphrase_verified(self.scope.get("session", {}))
            )
            and BacktestTask.objects.filter(
                id=task_id,
                configuration__user_id=user.id,
            ).exists()
        )

    async def connect(self):
        self.task_id = int(self.scope["url_route"]["kwargs"]["task_id"])
        if not await self.user_can_access(self.task_id):
            await self.close(code=4403)
            return
        self.group_name = f"backtest_progress_{self.task_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def backtest_progress(self, event):
        await self.send(text_data=json.dumps(event))
