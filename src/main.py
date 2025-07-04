import os
import random
import string
import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import aiomysql
import redis.asyncio as aioredis
import boto3
from botocore.client import Config
from fastapi.responses import FileResponse
from passlib.hash import bcrypt
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

# Initialize S3 client
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
templates = Jinja2Templates(directory="templates")


def random_shortlink(length: int = 7) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


# -----------------------------#
#        Pydantic models       #
# -----------------------------#
class PasteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=3072)
    expires_in: int = Field(..., ge=60, le=60 * 60 * 24 * 30)  # 1 min – 30 days
    burn_after_read: bool = Field(
        False,
        description="Delete the paste immediately after the first successful read",
    )
    password: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        description="Optional password to protect paste",
    )


class PasteResponse(BaseModel):
    url: str


class PasteContentResponse(BaseModel):
    content: str


# -----------------------------#
#         Lifecycle            #
# -----------------------------#
@app.on_event("startup")
async def startup() -> None:
    app.state.mysql = await aiomysql.create_pool(
        host=DB_HOST, user=DB_USER, password=DB_PASS, db=DB_NAME, autocommit=True
    )
    app.state.redis = aioredis.from_url(
        f"redis://{REDIS_HOST}:{REDIS_PORT}", decode_responses=True
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    app.state.mysql.close()
    await app.state.mysql.wait_closed()
    await app.state.redis.close()


async def get_db():
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            yield cur


# -----------------------------#
#            Routes            #
# -----------------------------#
@app.post("/paste", response_model=PasteResponse)
async def create_paste(req: PasteRequest, request: Request) -> PasteResponse:
    # Generate unique shortlink for the paste
    shortlink = random_shortlink()
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor() as cur:
            while True:
                await cur.execute(
                    "SELECT 1 FROM pastes WHERE shortlink=%s", (shortlink,)
                )
                if await cur.fetchone() is None:
                    break
                shortlink = random_shortlink()

    now = datetime.datetime.utcnow()
    expires_at = now + datetime.timedelta(seconds=req.expires_in)
    s3_key = f"{shortlink}/{now.timestamp()}.txt"
    password_hash = bcrypt.hash(req.password) if req.password else None

    # 2. Write content to S3
    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=req.content.encode())
    # Insert metadata into DB
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO pastes
                       (shortlink, s3_path, created_at, expires_at, size, burn_after_read, password_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    shortlink,
                    s3_key,
                    now,
                    expires_at,
                    len(req.content),
                    int(req.burn_after_read),
                    password_hash,
                ),
            )
    return PasteResponse(url=f"/{shortlink}")


@app.get("/{shortlink}", response_class=HTMLResponse)
async def read_paste(request: Request, shortlink: str) -> HTMLResponse:
    # Lookup
    async with app.state.mysql.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT s3_path, expires_at, burn_after_read, password_hash FROM pastes WHERE shortlink=%s",
                (shortlink,),
            )
            row = await cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Paste not found")

            expires_at = row["expires_at"]
            burn_after_read = bool(row["burn_after_read"])
            password_hash = row["password_hash"]

            if expires_at < datetime.datetime.utcnow():
                raise HTTPException(status_code=410, detail="Paste expired")

            if password_hash:
                provided_pw = request.query_params.get("p")
                if not provided_pw or not bcrypt.verify(provided_pw, password_hash):
                    # Wrong or missing password → render prompt
                    return templates.TemplateResponse(
                        "password_prompt.html",
                        {
                            "request": request,
                            "shortlink": shortlink,
                            "error": bool(provided_pw),
                        },
                    )

            # Redis cache **only if not burn‑after‑read**
            if not burn_after_read:
                cached = await app.state.redis.get(f"paste:{shortlink}")
                if cached is not None:
                    return templates.TemplateResponse(
                        "paste.html",
                        {"request": request, "content": cached, "shortlink": shortlink},
                    )

            # Fetch content from S3
            obj = s3.get_object(Bucket=S3_BUCKET, Key=row["s3_path"])
            content = obj["Body"].read().decode()

            # Handle burn‑after‑read (delete everywhere and skip caching)
            if burn_after_read:
                # Delete row on DB
                async with app.state.mysql.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "DELETE FROM pastes WHERE shortlink=%s", (shortlink,)
                        )

                # Delete Redis cache and S3 object
                await app.state.redis.delete(f"paste:{shortlink}")
                s3.delete_object(Bucket=S3_BUCKET, Key=row["s3_path"])
                return templates.TemplateResponse(
                    "paste.html",
                    {"request": request, "content": content, "shortlink": shortlink},
                )

    # Otherwise, cache and return
    ttl = int((expires_at - datetime.datetime.utcnow()).total_seconds())
    await app.state.redis.set(f"paste:{shortlink}", content, ex=ttl)

    return templates.TemplateResponse(
        "paste.html", {"request": request, "content": content, "shortlink": shortlink}
    )


# -----------------------------#
#            Static            #
# -----------------------------#
@app.get("/", response_class=HTMLResponse)
async def home():
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(content=html)
