FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /usr/src/app
COPY ai-chat-bot.py requirements.txt ./
RUN pip install --upgrade 'pip<24.1' && \
    pip install --no-cache-dir --requirement requirements.txt
CMD ["python", "ai-chat-bot.py"]