from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
import asyncpg
import json
from datetime import datetime
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

router = APIRouter()

#資料庫連接選擇
"""
選擇資料庫連接方式：
1. 本地資料庫連接設定
2. Render資料庫連接設定
"""
database_config_choice = 2
if database_config_choice == 1:
    # 本地資料庫連接設定
    DATABASE_CONFIG = {
        "host": os.getenv("RENDER_DB_HOST", "localhost"),
        "port": int(os.getenv("RENDER_DB_PORT", "5432")),
        "database": os.getenv("RENDER_DB_NAME", "sensor_data"),
        "user": os.getenv("RENDER_DB_USER", "postgres"),
        "password": os.getenv("RENDER_DB_PASSWORD", "c5718!ak")
    }
else:
    DATABASE_URL = os.getenv("DATABASE_URL")
    if DATABASE_URL:
        DATABASE_CONFIG = DATABASE_URL


# 快取變數
cached_data = None
last_cache_time = None
cache_duration = 5  # 快取持續時間（秒）
last_sent_data = None

async def get_db_connection():
    """建立資料庫連接"""
    try:
        if isinstance(DATABASE_CONFIG, str):
            # 如果是資料庫URL，使用asyncpg的connect方法
            conn = await asyncpg.connect(DATABASE_CONFIG)
        else:
            # 如果是字典格式的資料庫配置，使用asyncpg的connect方法
            conn = await asyncpg.connect(**DATABASE_CONFIG)
        return conn
    except Exception as e:
        print(f"資料庫連接失敗: {e}")
        return None

async def read_latest_data():
    """讀取最新的感測器數據"""
    global cached_data, last_cache_time
    
    # 檢查快取是否有效
    current_time = datetime.now()
    if (cached_data and last_cache_time and 
        (current_time - last_cache_time).total_seconds() < cache_duration):
        return cached_data
    
    conn = await get_db_connection()
    if not conn:
        return {"error": "無法連接到資料庫"}
    
    try:
        # 查詢最新數據
        query = """
        SELECT timestamp, ph_value, orp_value, ntu_value 
        FROM sensor_readings 
        ORDER BY timestamp DESC 
        LIMIT 1
        """
        
        row = await conn.fetchrow(query)
        
        if not row:
            return {"message": "資料庫中沒有數據", "data": []}
        
        # 格式化結果
        result = {
            "status": "success",
            "latest_data": {
                "timestamp": row['timestamp'].isoformat() if row['timestamp'] else None,
                "ph_value": str(row['ph_value']) if row['ph_value'] is not None else None,
                "orp_value": str(row['orp_value']) if row['orp_value'] is not None else None,
                "ntu_value": str(row['ntu_value']) if row['ntu_value'] is not None else None
            },
            "last_updated": current_time.isoformat()
        }
        
        # 更新快取
        cached_data = result
        last_cache_time = current_time
        
        return result
        
    except Exception as e:
        return {"error": f"讀取數據時發生錯誤: {str(e)}"}
    finally:
        await conn.close()

async def read_all_data(limit: int = 1000):
    """讀取所有歷史數據"""
    conn = await get_db_connection()
    if not conn:
        return {"error": "無法連接到資料庫"}
    
    try:
        # 查詢歷史數據（限制數量避免過多）
        query = """
        SELECT timestamp, ph_value, orp_value, ntu_value 
        FROM sensor_readings 
        ORDER BY timestamp DESC 
        LIMIT $1
        """
        
        rows = await conn.fetch(query, limit)
        
        # 格式化結果
        parsed_data = []
        for row in rows:
            parsed_data.append({
                "timestamp": row['timestamp'].isoformat() if row['timestamp'] else None,
                "ph_value": str(row['ph_value']) if row['ph_value'] is not None else None,
                "orp_value": str(row['orp_value']) if row['orp_value'] is not None else None,
                "ntu_value": str(row['ntu_value']) if row['ntu_value'] is not None else None
            })
        
        return {
            "status": "success",
            "data": parsed_data,
            "total_records": len(parsed_data),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {"error": f"讀取數據時發生錯誤: {str(e)}"}
    finally:
        await conn.close()

async def get_total_count():
    """獲取資料庫中的總記錄數"""
    conn = await get_db_connection()
    if not conn:
        return 0
    
    try:
        query = "SELECT COUNT(*) FROM sensor_readings"
        result = await conn.fetchval(query)
        return result or 0
    except Exception as e:
        print(f"獲取總數時發生錯誤: {e}")
        return 0
    finally:
        await conn.close()

@router.get("/data")
async def get_data():
    """原有的API端點，用於一次性獲取數據"""
    result = await read_latest_data()
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    
    # 添加總記錄數
    total_count = await get_total_count()
    result["total_records"] = total_count
    
    return result

@router.get("/replace")
async def stream_data_with_replace():
    """SSE端點，專門用於替換模式 - 只推送最新數據"""
    last_sent_replace_data = None
    
    async def event_generator():
        nonlocal last_sent_replace_data
        
        while True:
            try:
                # 讀取最新數據
                data = await read_latest_data()
                
                if "error" in data:
                    # 發送錯誤訊息
                    error_response = {
                        "error": data["error"], 
                        "timestamp": datetime.now().isoformat()
                    }
                    json_data = json.dumps(error_response, ensure_ascii=False)
                    yield f"data: {json_data}\n\n"
                    await asyncio.sleep(5)
                    continue
                
                # 檢查數據是否有變化
                current_data_key = None
                if "latest_data" in data:
                    latest_data = data["latest_data"]
                    if "timestamp" in latest_data and latest_data["timestamp"]:
                        current_data_key = latest_data["timestamp"]
                    else:
                        current_data_key = str(latest_data)
                
                # 只在數據有變化時發送
                if current_data_key != last_sent_replace_data:
                    # 建立簡化的回應
                    replace_response = {
                        "latest_data": data["latest_data"],
                        "timestamp": datetime.now().isoformat(),
                        "status": "success"
                    }
                    
                    json_data = json.dumps(replace_response, ensure_ascii=False)
                    yield f"data: {json_data}\n\n"
                    
                    last_sent_replace_data = current_data_key
                
                await asyncio.sleep(5)  # 調整檢查頻率
                
            except Exception as e:
                error_response = {
                    "error": f"串流發生錯誤: {str(e)}", 
                    "timestamp": datetime.now().isoformat()
                }
                json_data = json.dumps(error_response, ensure_ascii=False)
                yield f"data: {json_data}\n\n"
                await asyncio.sleep(5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )

@router.get("/data/all")
async def get_all_data(limit: int = 1000):
    """獲取所有歷史數據"""
    result = await read_all_data(limit)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@router.get("/health")
async def health_check():
    """健康檢查端點"""
    conn = await get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="資料庫連接失敗")
    
    try:
        # 測試查詢
        await conn.fetchval("SELECT 1")
        await conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        if conn:
            await conn.close()
        raise HTTPException(status_code=503, detail=f"資料庫錯誤: {str(e)}")