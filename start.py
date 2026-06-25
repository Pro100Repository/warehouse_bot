#!/usr/bin/env python3
"""
Launcher: sets BOT_TOKEN and starts the bot.
Edit BOT_TOKEN below with your token from @BotFather.
"""

import os
import subprocess
import sys

# ← Вставте ваш токен тут
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")


if BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
    print("❌ Відредагуйте файл start.py і вставте ваш BOT_TOKEN!")
    sys.exit(1)

os.environ["BOT_TOKEN"] = BOT_TOKEN

# Launch bot
subprocess.run([sys.executable, "bot.py"])
