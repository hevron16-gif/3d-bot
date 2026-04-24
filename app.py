# Самый первый print, чтобы убедиться, что файл вообще запустился
print("0: НАЧАЛО файла app.py")

import sys
print(f"1: Версия Python: {sys.version}")

print("2: Пытаюсь импортировать datetime...")
import datetime
from datetime import date
print("3: datetime импортирован")

print("4: Пытаюсь импортировать остальные библиотеки...")
import requests
import json
import time
import os
import threading
from flask import Flask
print("5: Все библиотеки импортированы")

print("6: Пытаюсь создать приложение Flask...")
app = Flask(__name__)
print("7: Flask создан")

# Заглушка для бота, пока просто выводим сообщение
@app.route('/')
def home():
    return "Test bot is running"

if __name__ == "__main__":
    print("8: Запускаю Flask сервер...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
