FROM python:3.11-slim

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

ENV PYTHONUNBUFFERED=1 \
    PORT=7860 \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

RUN mkdir -p data/media data/users sessions && chown -R user:user /home/user/app

USER user

EXPOSE 7860
EXPOSE 8080
EXPOSE 10000

CMD ["python", "main.py"]
