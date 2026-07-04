# Импортируем библиотеки
# FastAPI — это фреймворк для создания API (как конструктор для веб-серверов)
from fastapi import FastAPI, HTTPException
# Pydantic — проверяет, правильные ли данные пришли к нам в запросе
from pydantic import BaseModel
# httpx — отправляет HTTP-запросы наружу (в DeepSeek)
import httpx
# os — чтобы читать переменные окружения (например, ключ DeepSeek)
import os
# Optional — указывает, что поле может быть пустым
from typing import Optional

# Создаём приложение FastAPI
# Это наш "сервер" — он будет слушать запросы
app = FastAPI()

# Описываем, как должен выглядеть запрос от мобильного приложения
# Это как бланк, который пользователь должен заполнить
class DiagnoseRequest(BaseModel):
    error_code: str          # Например, "P0340"
    car_brand: str           # Например, "ВАЗ"
    car_model: Optional[str] = None  # Например, "2114" (необязательно)

# Берем API-ключ DeepSeek из переменных окружения Render
# Так ключ не светится в коде — это безопасно
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# Адрес DeepSeek API — куда будем отправлять запросы
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Обработчик POST-запроса на адрес /diagnose
# Когда приложение отправит сюда данные, запустится эта функция
@app.post("/diagnose")
async def diagnose(request: DiagnoseRequest):
    # Проверяем, есть ли ключ DeepSeek
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="API ключ не настроен")
    
    # Формируем промпт — инструкцию для DeepSeek
    # Это текст, который говорит DeepSeek, что от него требуется
    prompt = f"""
Ты — эксперт по диагностике российских автомобилей (ВАЗ, КАМАЗ, УАЗ, ГАЗ).
Марка: {request.car_brand}
Модель: {request.car_model or "не указана"}
Код ошибки: {request.error_code}

Дай ответ в таком формате:
1. Расшифровка ошибки
2. 3 наиболее вероятные причины (с учётом российских условий)
3. Способы устранения (от простого к сложному)
4. Рекомендация — можно ехать или нужен эвакуатор

Ответ пиши на русском языке, понятным языком.
"""
    
    # Отправляем запрос в DeepSeek
    # Это как отправить письмо в DeepSeek с нашей просьбой
    async with httpx.AsyncClient() as client:
        response = await client.post(
            DEEPSEEK_URL,  # Куда отправляем
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",  # Наш ключ-пропуск
                "Content-Type": "application/json"  # Говорим, что отправляем в формате JSON
            },
            json={
                "model": "deepseek-chat",  # Используем модель DeepSeek
                "messages": [{"role": "user", "content": prompt}],  # Наше сообщение AI
                "max_tokens": 1500,  # Максимальная длина ответа
                "temperature": 0.3  # Чем меньше, тем точнее ответ (0 — строго, 1 — креативно)
            },
            timeout=30.0  # Ждём ответ не больше 30 секунд
        )
    
    # Проверяем, всё ли хорошо
    if response.status_code != 200:
        # Если DeepSeek вернул ошибку — передаём её дальше
        raise HTTPException(status_code=response.status_code, detail=response.text)
    
    # Возвращаем ответ от DeepSeek пользователю
    return response.json()

# Обработчик GET-запроса на корневой адрес /
# Просто чтобы проверить, что сервер жив
@app.get("/")
async def root():
    return {"status": "ok", "message": "Сервер работает"}