from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from routers.api_v1.routers import router

app = FastAPI()

# 設定模板資料夾
templates = Jinja2Templates(directory="templates")

# 保留你的 API router
app.include_router(router, prefix="/api/v1")

# 新增一個路由，回傳 HTML 頁面
@app.get("/", response_class=templates.TemplateResponse)
async def home(request: Request):
    return templates.TemplateResponse("index_test.html", {"request": request})