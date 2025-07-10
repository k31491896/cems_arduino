from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import os
import json
import asyncio
from datetime import datetime
import time

router = APIRouter()

# 檔案路徑常數
DATA_FILE_PATH = "static\\sensor_readings.txt"

# 儲存上次讀取的檔案修改時間
last_modified_time = None
cached_data = None
last_sent_data = None  # 新增：儲存上次發送的數據

def read_data_file():
    """讀取數據檔案"""
    global last_modified_time, cached_data
    
    try:
        if not os.path.exists(DATA_FILE_PATH):
            return {"error": "數據檔案未找到"}
        
        # 檢查檔案修改時間
        current_modified_time = os.path.getmtime(DATA_FILE_PATH)
        
        # 如果檔案沒有更新且有快取，返回快取數據
        if last_modified_time == current_modified_time and cached_data:
            return cached_data
        
        # 讀取檔案
        with open(DATA_FILE_PATH, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        
        data_lines = [line.strip() for line in lines if line.strip()]
        
        if not data_lines:
            return {"message": "檔案為空", "data": []}
        
        # 解析最新數據
        latest_line = data_lines[-1]
        
        try:
            parts = latest_line.split(', ')
            if len(parts) >= 3:
                timestamp = parts[0]
                ph_value = parts[1]
                orp_value = parts[2]
                
                result = {
                    "status": "success",
                    "latest_data": {
                        "timestamp": timestamp,
                        "ph_value": ph_value,
                        "orp_value": orp_value
                    },
                    "total_records": len(data_lines),
                    "last_updated": datetime.now().isoformat()
                }
            else:
                result = {
                    "status": "success",
                    "latest_data": {
                        "data": latest_line
                    },
                    "total_records": len(data_lines),
                    "last_updated": datetime.now().isoformat()
                }
        except Exception:
            result = {
                "status": "success",
                "latest_data": {
                    "data": latest_line
                },
                "total_records": len(data_lines),
                "last_updated": datetime.now().isoformat()
            }
        
        # 更新快取
        last_modified_time = current_modified_time
        cached_data = result
        
        return result
        
    except Exception as e:
        return {"error": f"讀取檔案時發生錯誤: {str(e)}"}

def read_all_data():
    """讀取所有歷史數據"""
    try:
        if not os.path.exists(DATA_FILE_PATH):
            return {"error": "數據檔案未找到"}
        
        with open(DATA_FILE_PATH, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        
        data_lines = [line.strip() for line in lines if line.strip()]
        
        # 解析所有數據
        parsed_data = []
        for line in data_lines:
            try:
                parts = line.split(', ')
                if len(parts) >= 3:
                    parsed_data.append({
                        "timestamp": parts[0],
                        "ph_value": parts[1],
                        "orp_value": parts[2]
                    })
                else:
                    parsed_data.append({
                        "data": line
                    })
            except Exception:
                parsed_data.append({
                    "data": line
                })
        
        return {
            "status": "success",
            "data": parsed_data,
            "total_records": len(data_lines),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        return {"error": f"讀取檔案時發生錯誤: {str(e)}"}

@router.get("/data")
async def get_data():
    """原有的API端點，用於一次性獲取數據"""
    result = read_data_file()
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
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
                data = read_data_file()
                
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
                    if "timestamp" in latest_data:
                        current_data_key = latest_data["timestamp"]
                    else:
                        current_data_key = str(latest_data)
                
                # 只在數據有變化時發送
                if current_data_key != last_sent_replace_data:
                    # 建立簡化的回應，移除多餘的包裝
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
async def get_all_data():
    """獲取所有歷史數據"""
    result = read_all_data()
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result