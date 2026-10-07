from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routes import auth, documents, share


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="脱敏资料发布平台", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(share.router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # 前端静态资源（Docker 构建产物）；SPA 路由回退到 index.html
    static_dir = config.STATIC_DIR
    if static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            target = (static_dir / full_path).resolve()
            if full_path and target.is_file() and target.is_relative_to(static_dir):
                return FileResponse(target)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
