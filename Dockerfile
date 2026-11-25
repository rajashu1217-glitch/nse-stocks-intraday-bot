FROM python:3.12-slim

WORKDIR /app

ENV TZ="Asia/Kolkata"
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["python", "server.py"]
