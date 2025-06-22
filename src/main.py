import os
import random
import string
import datetime
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
import aiomysql
import redis.asyncio as aioredis
import boto3
from botocore.client import Config
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "pasteuser")
DB_PASS = os.getenv("DB_PASS", "pastepass")
DB_NAME = os.getenv("DB_NAME", "pastebin")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minio")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "miniopass")
S3_BUCKET = os.getenv("S3_BUCKET", "pastes")

# Use s3 sdk to initialize
s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)

# Create bucket
try:
    s3.create_bucket(Bucket=S3_BUCKET)
except Exception:
    pass

app = FastAPI()

def random_shortlink(length=7):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

class PasteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=3072)
    expires_in: int = Field(..., ge=60, le=60*60*24*30)  # 1 min - 30 days

class PasteResponse(BaseModel):
    url: str

class PasteContentResponse(BaseModel):
    content: str

@app.on_event("startup")
async def startup():
    app.state.mysql = await aiomysql.create_pool(
        host=DB_HOST, user=DB_USER, password=DB_PASS, db=DB_NAME, autocommit=True
    )
    app.state.redis = aioredis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}", decode_responses=True)

@app.on_event("shutdown")
async def shutdown():
    app.state.mysql.close()
    await app.state.mysql.wait_closed()
    await app.state.redis.close()

async def get_db():
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            yield cur

@app.post("/paste", response_model=PasteResponse)
async def create_paste(req: PasteRequest, request: Request):
    # Generate unique shortlink for the paste
    shortlink = random_shortlink()
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor() as cur:
            while True:
                await cur.execute("SELECT 1 FROM pastes WHERE shortlink=%s", (shortlink,))
                if await cur.fetchone() is None:
                    break
                shortlink = random_shortlink()
    now = datetime.datetime.utcnow()
    expires_at = now + datetime.timedelta(seconds=req.expires_in)
    s3_key = f"{shortlink}/{now.timestamp()}.txt"
    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=req.content.encode())
    # Insert metadata into DB
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO pastes (shortlink, s3_path, created_at, expires_at, size) VALUES (%s,%s,%s,%s,%s)",
                (shortlink, s3_key, now, expires_at, len(req.content))
            )
    return PasteResponse(url=f"/{shortlink}")

@app.get("/{shortlink}", response_model=PasteContentResponse)
async def read_paste(shortlink: str):
    # Check cache
    content = await app.state.redis.get(f"paste:{shortlink}")
    if content is not None:
        return PasteContentResponse(content=content)
    # Query DB for S3 path and expiration
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT s3_path, expires_at FROM pastes WHERE shortlink=%s", (shortlink,)
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Paste not found")
            expires_at = row["expires_at"]
            if expires_at < datetime.datetime.utcnow():
                raise HTTPException(status_code=410, detail="Paste expired")
            s3_path = row["s3_path"]
    obj = s3.get_object(Bucket=S3_BUCKET, Key=s3_path)
    content = obj["Body"].read().decode()
    # Cache it
    ttl = int((expires_at - datetime.datetime.utcnow()).total_seconds())
    await app.state.redis.set(f"paste:{shortlink}", content, ex=ttl)
    return PasteContentResponse(content=content)

@app.get("/", response_class=HTMLResponse)
async def home():
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)