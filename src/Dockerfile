FROM python:3.11

WORKDIR /app
COPY . /app

RUN chmod +x wait-for-it.sh

RUN pip install fastapi uvicorn aiomysql redis[asyncio] boto3 pydantic python-dotenv sqlalchemy cryptography jinja2

EXPOSE 8000
CMD ["./wait-for-it.sh", "db:3306", "--", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
