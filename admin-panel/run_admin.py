import uvicorn

from admin.settings import settings


if __name__ == "__main__":
    uvicorn.run(
        "admin.main:app",
        host=settings.admin_host,
        port=settings.admin_port,
        reload=False,
    )
