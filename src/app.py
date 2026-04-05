import uuid
import shutil
import os
import tempfile

from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Depends
from .schemas import UserRead, UserCreate, UserUpdate
from .db import Post, User, create_db_and_tables, get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from sqlalchemy import select
from .images import imagekit
from .users import current_active_user, fastapi_users, auth_backend



@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield
    
    
app = FastAPI(lifespan=lifespan)

app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])




@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    caption: str = Form(""),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session)
):
    temp_file_path = None
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_file:
            temp_file_path = temp_file.name
            shutil.copyfileobj(file.file, temp_file)

        with open(temp_file_path, "rb") as temp_file_handle:
            upload_result = imagekit.upload_file(
                file=temp_file_handle,
                file_name=file.filename,
            )
        
        if upload_result.response.http_status_code == 200:
            post = Post(
                user_id=str(user.id),
                caption=caption,
                url=upload_result.url,
                file_type="video" if file.content_type.startswith("video/") else "image",
                file_name=upload_result.name
            )
            session.add(post)
            await session.commit()
            await session.refresh(post)
            return post
        raise HTTPException(status_code=502, detail="Upload provider returned a non-success response")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        await file.close()

@app.get("/feed")
async def get_feed(
    session: AsyncSession = Depends(get_async_session),
    _: User = Depends(current_active_user)
):
    result = await session.execute(select(Post).order_by(Post.created_at.desc()).limit(10))
    posts = [row[0] for row in result.all()]
    
    post_data = []
    for post in posts:
        post_data.append(
            {
                "id": post.id,
                "caption": post.caption,
                "url": post.url,
                "file_type": post.file_type,
                "file_name": post.file_name,
                "created_at": post.created_at.isoformat()
            }
        )
    
    return {"posts": post_data}


@app.delete("/delete/{post_id}")
async def delete_post(
    post_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user)
):
    result = await session.execute(
        select(Post).where(Post.id == str(post_id), Post.user_id == str(user.id))
    )
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    await session.delete(post)
    await session.commit()

    return {"success": True, "message": "Post deleted successfully"}