from fastapi import APIRouter
from routers.api_v1.endpoints.user import router as user_router
from routers.api_v1.endpoints.data1 import router as data1_router

router = APIRouter()

router.include_router(user_router, prefix="/user")
router.include_router(data1_router, prefix="/data1")