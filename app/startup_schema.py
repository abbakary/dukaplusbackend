"""Wire into FastAPI lifespan so sales/reports work before any accounting route is called.

Example (main.py):

    from app.startup_schema import run_startup_schema_patches
    from app.database import engine

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_startup_schema_patches(engine)
        yield
"""

from app.services.db_schema_patches import apply_schema_patches_on_startup


async def run_startup_schema_patches(engine) -> None:
    await apply_schema_patches_on_startup(engine)
