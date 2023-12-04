import json
import os
import sys
from settings import bot_strategies, greetings

if len(sys.argv) < 2:
    exit(1)

bot = sys.argv[1].strip()
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8587

MAXGAMES = 10
GTP2OGS = "/home/raylu/src/gtp2ogs/packaged/gtp2ogs-linux"

#username = f"katrain-{bot}"
#username = "raylubot-beta"
username = "simplebot katrain"

with open("config.json") as f:
    settings = json.load(f)
    all_ai_settings = settings["ai"]

ai_strategy, x_ai_settings, x_engine_settings = bot_strategies[bot]

ai_settings = {**all_ai_settings[ai_strategy], **x_ai_settings}

with open("secret/apikey.json") as f:
    apikeys = json.load(f)

if bot not in greetings or username not in apikeys:
    print(f"BOT {username} NOT FOUND")
    exit(2)

APIKEY = apikeys[username]
settings_dump = ", ".join(f"{k}={v}" for k, v in ai_settings.items() if not k.startswith("_"))
print(settings_dump)

cmd = [GTP2OGS, '--apikey', APIKEY, '--config', 'gtp2ogs.json5']
if username.endswith('-beta'):
    cmd.append('--beta')
cmd += ['--', 'python3', 'ai2gtp.py', bot, str(port)]
print(f"starting bot {username} using server port {port} --> {' '.join(cmd)}")
os.execv(cmd[0], cmd)
