FROM python:3.10-slim

WORKDIR /app

# Установка системных зависимостей (если потребуются для openpyxl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаем пустой файл базы данных и заглушку для PDF, если её нет
RUN touch bot.db

CMD ["python", "bot.py"]