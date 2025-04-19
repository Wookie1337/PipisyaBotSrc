VERSION="0.0.1"
API_TOKEN = 'YOUR_BOT_API_TOKEN'

import ast
import random
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite


# === Класс для работы с базой данных SQLite ===
class DataBase:
    """
    Асинхронный класс для работы с базой данных SQLite.
    """

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self):
        self.db = await aiosqlite.connect(self.db_name)
        self.db.row_factory = aiosqlite.Row

    async def close(self):
        if self.db:
            await self.db.close()

    async def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        async with self.db.execute(sql, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self.db.execute(sql, params)
        await self.db.commit()

    async def create_table(self, table: str, schema: dict) -> None:
        columns = [f"{name} {dtype}" for name, dtype in schema.items()]
        await self.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})")

    async def insert(self, table: str, data: dict) -> None:
        try:
            columns = ", ".join(data.keys())
            placeholders = ", ".join("?" * len(data))
            cursor = await self.db.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(data.values())
            )
            await self.db.commit()
        except aiosqlite.IntegrityError:
            return

    async def get(self, table: str, where: Optional[Dict] = None) -> Optional[Dict]:
        conditions, params = self._prepare_conditions(where)
        query = f"SELECT * FROM {table} {conditions}"
        results = await self.query(query, params)
        return results[0] if results else None

    async def find(self, table: str, where: Optional[Dict] = None, add_query: str = '') -> List[Dict]:
        conditions, params = self._prepare_conditions(where)
        query = f"SELECT * FROM {table} {conditions} {add_query}"
        return await self.query(query, params)

    async def update(self, table: str, data: dict, where: Optional[Dict] = None) -> None:
        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        where_clause, where_params = self._prepare_conditions(where)
        params = tuple(data.values()) + where_params
        await self.execute(f"UPDATE {table} SET {set_clause} {where_clause}", params)

    async def delete(self, table: str, where: Optional[Dict] = None) -> None:
        conditions, params = self._prepare_conditions(where)
        await self.execute(f"DELETE FROM {table} {conditions}", params)

    @staticmethod
    def _prepare_conditions(conditions: Optional[Dict]) -> tuple[str, tuple]:
        if not conditions:
            return "", ()
        clauses = [f"{k} = ?" for k in conditions.keys()]
        return f"WHERE {' AND '.join(clauses)}", tuple(conditions.values())


# === Класс для управления игровой логикой ===
class DickManager:
    CONFIG = {
        "delay": timedelta(hours=24, minutes=0, seconds=0),
        "time": {"h": 24, "m": 0, "s": 0},
        "max": 10,
        "min": -5,
        "date_format": "%Y-%m-%d %H:%M:%S",
        "messages": {
            "yes": (
                "{username}, твой писюн {state} на {add_size} см.\n"
                "Теперь он равен {size} см.\n"
                "Ты занимаешь {top} место в топе.\n"
                "Следующая попытка через — {h}ч. {m}м. {s}с."
            ),
            "no": (
                "{username}, ты уже играл.\n"
                "Твой писюн равен {size} см.\n"
                "Ты занимаешь {top} место в топе.\n"
                "Следующая попытка через — {h}ч. {m}м. {s}с."
            )
        }
    }

    def __init__(self, database: DataBase):
        self.db = database
        self.data = None

    async def get_data(self, table: str, user_id: int) -> Optional[Dict]:
        data = await self.db.get(table, {"id": user_id})
        return data

    async def add_group(self, groups: list, user_id: int, chat_id: int):
        if chat_id in groups:
            return
        groups.append(chat_id)
        await self.db.update("users", {"groups": str(groups)}, {"id": user_id})

    async def get_time_next_play(self, last_played) -> Dict[str, int]:
        now = datetime.now()
        delta = now - last_played
        delay = self.CONFIG["delay"]

        remaining = delay - delta
        if remaining.total_seconds() <= 0:
            return {"h": 0, "m": 0, "s": 0}

        total_seconds = int(remaining.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)

        return {"h": h, "m": m, "s": s}

    async def get_top(self, table: str) -> str:
        TOP_HEADER = "Топ 10 игроков\n\n"

        data = await self.db.find(table, add_query="ORDER BY size DESC LIMIT 10")
        if not data:
            return "Список топ игроков пуст."

        lines = [
            f"{n}) [{user['username']}]({user['url']}) — {user['size']} см."
            for n, user in enumerate(data, start=1)
        ]

        return TOP_HEADER + "\n".join(lines)

    async def get_n_top(self, user_id: int, chat_id: int) -> int:
        data = await self.db.find(f"group_{chat_id}", add_query="ORDER BY size DESC")
        if not data:
            return 0
        for index, user in enumerate(data, start=1):
            if user["id"] == user_id:
                return index

    async def get_global_top(self) -> str:
        return await self.get_top("users")

    async def get_chat_top(self, chat_id) -> str:
        return await self.get_top(f"group_{chat_id}")

    async def dick(self, user_id, username, chat_id) -> str:
        self.data = {
            "users": await self.get_data("users", user_id),
            "group": await self.get_data(f"group_{chat_id}", user_id)
        }

        last_played = datetime.strptime(self.data["group"]["last_played"], self.CONFIG["date_format"])
        format_data = {"username": username, "size": self.data["group"]["size"], "top": await self.get_n_top(user_id, chat_id)}
        groups = ast.literal_eval(self.data["users"]["groups"])

        await self.add_group(groups, user_id, chat_id)

        if (datetime.now() - last_played) > self.CONFIG["delay"]:
            while (random_size := random.randint(self.CONFIG["min"], self.CONFIG["max"])) == 0:
                continue

            new_size = self.data["group"]["size"] + random_size
            if new_size < 0:
                new_size = 0

            self.data["group"]["size"] = new_size
            self.data["group"]["last_played"] = datetime.strftime(datetime.now(), self.CONFIG["date_format"])

            await self.db.update(
                table=f"group_{chat_id}",
                data=self.data["group"],
                where={"id": user_id}
            )

            format_data["state"] = "вырос" if random_size > 0 else "сократился"
            format_data["size"] = new_size
            format_data["add_size"] = random_size if random_size > 0 else abs(random_size)
            format_data["top"] = await self.get_n_top(user_id, chat_id)

            dick_sizes = []
            for chat_id in groups:
                data = await self.db.get(f"group_{chat_id}", {"id": user_id})
                if (value := data["size"]) is not None:
                    dick_sizes.append(value)

            await self.db.update(
                table="users",
                data={"size": max(dick_sizes)},
                where={"id": user_id}
            )

            return self.CONFIG["messages"]["yes"].format(
                **format_data,
                **self.CONFIG["time"]
            )

        else:
            return self.CONFIG["messages"]["no"].format(
                **format_data,
                **await self.get_time_next_play(last_played)
            )


# === Основной код бота ===

async def ensure_user_in_db(db: DataBase, table: str, user_id: int, first_name: str, username: str, url: str) -> None:
    await db.insert(table=table, data={"id": user_id, "firstname": first_name, "username": username, "url": url})


async def setup_group_table(db: DataBase, table: str, chat_id: int) -> None:
    await db.create_table(
        table=table,
        schema={
            "id": "INTEGER PRIMARY KEY",
            "firstname": "TEXT DEFAULT 'None'",
            "username": "TEXT DEFAULT 'None'",
            "url": "TEXT DEFAULT 'None'",
            "size": "INTEGER DEFAULT 0",
            "last_played": "TEXT DEFAULT '2000-01-01 00:00:00'"
        }
    )


async def init_func(db: DataBase, message: Message) -> Tuple[int, str, int, str, bool]:
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username
    url = message.from_user.url
    chat_id = abs(message.chat.id)
    chat_type = message.chat.type
    in_group = False

    await ensure_user_in_db(db, "users", user_id, first_name, username, url)

    if chat_type in ["group", "supergroup"]:
        await setup_group_table(db, f"group_{chat_id}", chat_id)
        await ensure_user_in_db(db, f"group_{chat_id}", user_id, first_name, username, url)
        in_group = True

    return user_id, first_name, chat_id, chat_type, in_group


async def main() -> None:
    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()

    keyboards = {
        "add_bot": InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Добавить бота в группу", url="http://t.me/testxdxdxdxdxdx_bot?startgroup=Lichka")]
            ]
        )
    }

    async with DataBase("database.db") as db:
        await db.create_table(
            table="users",
            schema={
                "id": "INTEGER PRIMARY KEY",
                "firstname": "TEXT DEFAULT 'None'",
                "username": "TEXT DEFAULT 'None'",
                "url": "TEXT DEFAULT 'None'",
                "size": "INTEGER DEFAULT 0",
                "groups": "TEXT DEFAULT '[]'"
            }
        )

        dick_mgr = DickManager(db)

        @dp.message(Command("start"))
        async def start_handler(message: Message):
            try:
                user_id, first_name, chat_id, chat_type, in_group = await init_func(db, message)

                await message.answer(f"""
                    \rПривет! я линейка — бот для чатов (групп) \

                    \nСмысл бота: бот работает только в чатах. Раз в 24 часа игрок может прописать команду /dick, где в ответ получит от бота рандомное число. \
                    \nРандом работает от -5 см до +10 см. \

                    \nЕсли у тебя есть вопросы — пиши команду: /help"""
                )

            except Exception as e:
                print(f"Ошибка в start_handler: {e}")
                await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже!")

        @dp.message(Command("dick"))
        async def dick_handler(message: Message):
            user_id, first_name, chat_id, chat_type, in_group = await init_func(db, message)
            username = message.from_user.username
            url = message.from_user.url

            if in_group:
                ret_msg = await dick_mgr.dick(user_id, f"[{username}]({url})", chat_id)
                await message.answer(ret_msg, parse_mode="Markdown")
            else:
                await message.answer(f"Я работаю только в чатах (группах)", reply_markup=keyboards["add_bot"])

        @dp.message(Command("global_top"))
        async def global_top_handler(message: Message):
            user_id, first_name, chat_id, chat_type, in_group = await init_func(db, message)

            if in_group:
                await message.answer(f"Данная команда доступна только в личке с ботом❗️")
            else:
                ret_msg = await dick_mgr.get_global_top()
                await message.answer(ret_msg, parse_mode="Markdown")

        @dp.message(Command("chat_top"))
        async def chat_top_handler(message: Message):
            user_id, first_name, chat_id, chat_type, in_group = await init_func(db, message)

            if in_group:
                ret_msg = await dick_mgr.get_chat_top(chat_id)
                await message.answer(ret_msg, parse_mode="Markdown")
            else:
                await message.answer(f"Я работаю только в чатах (группах)", reply_markup=keyboards["add_bot"])

        @dp.message(Command("help"))
        async def help_handler(message: Message):
            await message.answer("""
                  \rКоманды бота:\
                  \n/dick — Вырастить/уменьшить пипису\
                  \n/chat_top — Топ 10 пипис чата\
                  \n/global_top — Глобальный топ 10 игроков\

                  \nКонтакты:\
                  \nСоздатель — @wookie1337""")

        await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
