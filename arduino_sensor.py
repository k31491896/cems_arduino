import serial
import time
import sys
import psycopg2
from psycopg2 import sql, Error
from datetime import datetime
import os
from dotenv import load_dotenv
import re

# 載入環境變數，指定編碼
try:
    load_dotenv(encoding='utf-8')
except:
    try:
        load_dotenv(encoding='big5')
    except:
        load_dotenv()

class DatabaseConfig:
    """資料庫配置類別"""
    
    @staticmethod
    def get_local_config():
        """本地 PostgreSQL 配置"""
        return {
            'host': 'localhost',
            'database': 'sensor_data',
            'user': 'postgres',
            'password': 'c5718!ak',
            'port': '5432'
        }
    
    @staticmethod
    def get_render_config():
        """直接讀取 DATABASE_URL 環境變數"""
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            raise ValueError("缺少必要的環境變數: DATABASE_URL")
        return database_url

class SerialDataReader:
    """串列埠資料讀取器，處理編碼問題"""
    
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_connection = None
        self.encoding_list = ['utf-8', 'big5', 'gb2312', 'ascii', 'latin1']
    
    def connect(self):
        """建立串列埠連接"""
        try:
            self.serial_connection = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            print(f"✅ 已連接到串列埠 {self.port}，鮑率: {self.baudrate}")
            return True
        except serial.SerialException as e:
            print(f"❌ 串列埠連接失敗: {e}")
            return False
    
    def safe_decode(self, raw_data):
        """安全解碼資料，嘗試多種編碼方式"""
        if not raw_data:
            return ""
        
        # 嘗試不同的編碼方式
        for encoding in self.encoding_list:
            try:
                decoded = raw_data.decode(encoding, errors='ignore').strip()
                if decoded:  # 確保解碼後有內容
                    return decoded
            except (UnicodeDecodeError, AttributeError):
                continue
        
        # 如果所有編碼都失敗，使用 errors='replace' 來替換錯誤字符
        try:
            decoded = raw_data.decode('utf-8', errors='replace').strip()
            if decoded:
                return decoded
        except:
            pass
        
        # 最後的備用方案：使用 latin1 並清理不可見字符
        try:
            decoded = raw_data.decode('latin1', errors='ignore').strip()
            # 清理不可見字符，只保留可印字符
            cleaned = ''.join(char for char in decoded if ord(char) >= 32 or char in ['\t', '\n', '\r'])
            return cleaned
        except:
            return ""
    
    def read_line(self):
        """讀取一行資料"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return None
        
        try:
            if self.serial_connection.in_waiting > 0:
                raw_data = self.serial_connection.readline()
                if raw_data:
                    decoded_data = self.safe_decode(raw_data)
                    return decoded_data
        except Exception as e:
            print(f"⚠️  讀取資料時發生錯誤: {e}")
        
        return None
    
    def flush_input(self):
        """清空輸入緩衝區"""
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.flushInput()
    
    def close(self):
        """關閉串列埠連接"""
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            print("🔒 串列埠連接已關閉")

class SensorDataParser:
    """感測器資料解析器 - 添加詳細調試"""
    
    def __init__(self):
        # 擴展正則表達式模式來匹配更多可能的NTU格式
        self.ph_pattern = re.compile(r'pH[_\s]*value:?\s*(-?\d+\.?\d*)', re.IGNORECASE)
        self.orp_pattern = re.compile(r'ORP:?\s*(-?\d+)', re.IGNORECASE)
        self.ntu_pattern = re.compile(r'(?:Turbidity|NTU):?\s*(-?\d+)', re.IGNORECASE)
    
    def parse_sensor_data(self, data_string):
        """解析感測器資料字符串 - 添加詳細調試"""
        if not data_string:
            return None, None, None
        
        # 清理不可見字元
        cleaned_data = ''.join(c for c in data_string if ord(c) >= 32 or c in '\t\n\r')
        
        ph_value = None
        orp_value = None
        ntu_value = None
        
        ph_match = self.ph_pattern.search(cleaned_data)
        if ph_match:
            try:
                ph_value = float(ph_match.group(1))
            except:
                pass
        
        orp_match = self.orp_pattern.search(cleaned_data)
        if orp_match:
            try:
                orp_value = int(orp_match.group(1))
            except:
                pass
        
        ntu_match = self.ntu_pattern.search(cleaned_data)
        if ntu_match:
            try:
                ntu_value = int(ntu_match.group(1))
            except:
                pass
        
        return ph_value, orp_value, ntu_value
    
    def validate_sensor_values(self, ph_value, orp_value, ntu_value):
        """驗證感測器數值的合理性"""
        warnings = []
        
        if ph_value is not None:
            if ph_value < 0 or ph_value > 14:
                warnings.append(f"pH值 {ph_value:.2f} 超出正常範圍 (0-14)")
        
        if orp_value is not None:
            if orp_value < -2000 or orp_value > 2000:
                warnings.append(f"ORP值 {orp_value}mV 超出正常範圍 (-2000 to 2000)")
        
        if ntu_value is not None:
            if ntu_value < 0 or ntu_value > 4000:
                warnings.append(f"NTU值 {ntu_value} 超出正常範圍 (0-4000)")
        
        return warnings

class SensorDatabase:
    """感測器資料庫操作類別 - 添加詳細調試"""
    
    def __init__(self, config_type='local'):
        self.config_type = config_type
        self.connection = None
        self.cursor = None
        
        try:
            if config_type == 'local':
                self.config = DatabaseConfig.get_local_config()
            elif config_type == 'render':
                self.config = DatabaseConfig.get_render_config()
            else:
                raise ValueError("config_type 必須是 'local' 或 'render'")
        except Exception as e:
            print(f"❌ 配置初始化失敗: {e}")
            raise
    
    def connect(self):
        try:
            print(f"🔄 正在連接到 {self.config_type} PostgreSQL 資料庫...")
            
            if self.config_type == 'render':
                print(f"   使用 DATABASE_URL 連線字串")
                # 用完整連線字串連線，並強制 sslmode
                self.connection = psycopg2.connect(self.config, sslmode='require')
            else:
                # local 用字典設定連線
                self.connection = psycopg2.connect(**self.config)
            
            self.cursor = self.connection.cursor()
            self.cursor.execute("SELECT version();")
            version = self.cursor.fetchone()
            print(f"✅ 成功連接到 {self.config_type} PostgreSQL 資料庫")
            if version and version[0]:
                version_str = str(version[0])[:50]
                print(f"   PostgreSQL 版本: {version_str}...")
            return True
            
        except Error as e:
            print(f"❌ 連接資料庫失敗: {e}")
            return False
        except Exception as e:
            print(f"❌ 連接過程中發生未預期錯誤: {e}")
            return False
    
    def check_connection(self):
        """檢查連線是否仍然有效"""
        try:
            if self.connection and not self.connection.closed:
                self.cursor.execute("SELECT 1;")
                return True
            return False
        except Error:
            return False
    
    def reconnect(self):
        """重新連線"""
        if self.connection:
            try:
                self.connection.close()
            except:
                pass
        return self.connect()
    
    def create_table(self):
        """建立感測器資料表"""
        create_table_query = """
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            ph_value DECIMAL(4,2),
            orp_value INTEGER,
            ntu_value INTEGER,
            ph_status VARCHAR(50),
            orp_status VARCHAR(50),
            water_quality_good BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        
        try:
            self.cursor.execute(create_table_query)
            self.connection.commit()
            print("✅ 資料表建立/確認完成")
            return True
        except Error as e:
            print(f"❌ 建立資料表失敗: {e}")
            return False
    
    def save_sensor_data(self, ph_value, orp_value, ntu_value=None):
        """儲存感測器資料到資料庫 - 添加詳細調試"""
        try:
            # 檢查連線狀態
            if not self.check_connection():
                print("⚠️  資料庫連線中斷，嘗試重新連線...")
                if not self.reconnect():
                    print("❌ 重新連線失敗，無法儲存資料")
                    return False
            
            # 詳細記錄準備寫入的數值
            print(f"💾 準備寫入資料庫的數值:")
            print(f"   pH: {ph_value} (type: {type(ph_value)})")
            print(f"   ORP: {orp_value} (type: {type(orp_value)})")
            print(f"   NTU: {ntu_value} (type: {type(ntu_value)})")
            
            # 計算狀態
            ph_status = self._get_ph_status(ph_value) if ph_value is not None else None
            orp_status = self._get_orp_status(orp_value) if orp_value is not None else None
            water_quality_good = self._is_water_quality_good(ph_value, orp_value)
            
            insert_query = """
            INSERT INTO sensor_readings (
                timestamp, ph_value, orp_value, ntu_value, 
                ph_status, orp_status, water_quality_good
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            
            values = (
                datetime.now(),
                ph_value,
                orp_value,
                ntu_value,
                ph_status,
                orp_status,
                water_quality_good
            )
            
            #print(f"💾 SQL 查詢: {insert_query}")
            #print(f"💾 參數值: {values}")
            
            self.cursor.execute(insert_query, values)
            self.connection.commit()
            
            # 驗證資料是否正確插入
            self.cursor.execute("SELECT * FROM sensor_readings ORDER BY id DESC LIMIT 1;")
            last_record = self.cursor.fetchone()
            if last_record:
                print(f"💾 最新插入的記錄: {last_record}")
            
            print(f"💾 資料已儲存到資料庫: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            return True
            
        except Error as e:
            print(f"❌ 儲存資料失敗: {e}")
            if self.connection:
                self.connection.rollback()
            return False
    
    def _get_ph_status(self, ph_value):
        """取得pH狀態"""
        if ph_value < 6.5:
            return "酸性"
        elif ph_value > 7.5:
            return "鹼性"
        else:
            return "中性"
    
    def _get_orp_status(self, orp_value):
        """取得ORP狀態"""
        if orp_value > 650:
            return "強氧化性"
        elif orp_value > 300:
            return "氧化性"
        elif orp_value > 0:
            return "弱氧化性"
        else:
            return "還原性"
    
    def _is_water_quality_good(self, ph_value, orp_value):
        """判斷水質是否良好"""
        if ph_value is None or orp_value is None:
            return None
        return (ph_value >= 6.5 and ph_value <= 8.5 and 
                orp_value >= 200 and orp_value <= 800)
    
    def close(self):
        """關閉資料庫連接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        print("🔒 資料庫連接已關閉")

def read_arduino_data(port='COM3', baudrate=9600, timeout=1, db_type='local'):
    """讀取Arduino感測器資料並儲存到PostgreSQL"""
    
    # 初始化元件
    serial_reader = SerialDataReader(port, baudrate, timeout)
    data_parser = SensorDataParser()
    
    try:
        db = SensorDatabase(db_type)
    except Exception as e:
        print(f"❌ 資料庫初始化失敗: {e}")
        return
    
    # 建立連接
    if not serial_reader.connect():
        return
    
    if not db.connect():
        serial_reader.close()
        return
    
    if not db.create_table():
        serial_reader.close()
        db.close()
        return
    
    try:
        # 等待Arduino初始化
        print("⏳ 等待Arduino初始化...")
        time.sleep(3)
        
        # 清空緩衝區
        serial_reader.flush_input()
        
        print("🔄 開始讀取感測器資料... (按 Ctrl+C 停止)")
        print("=" * 70)
        
        # 用來儲存歷史資料
        ph_readings = []
        orp_readings = []
        ntu_readings = []
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while True:
            # 讀取資料
            raw_data = serial_reader.read_line()
            
            if raw_data is None:
                time.sleep(0.1)
                continue
            
            if not raw_data:  # 空字符串
                time.sleep(0.1)
                continue
            
            print(f"📡 原始資料: {repr(raw_data)}")
            
            # 解析資料
            ph_value, orp_value, ntu_value = data_parser.parse_sensor_data(raw_data)
            
            if ph_value is not None or orp_value is not None or ntu_value is not None:
                # 重置錯誤計數
                consecutive_errors = 0
                
                # 驗證數值
                warnings = data_parser.validate_sensor_values(ph_value, orp_value, ntu_value)
                
                if warnings:
                    print(f"⚠️  資料驗證警告:")
                    for warning in warnings:
                        print(f"   - {warning}")
                
                # 儲存有效的讀取值
                if ph_value is not None:
                    ph_readings.append(ph_value)
                if orp_value is not None:
                    orp_readings.append(orp_value)
                if ntu_value is not None:
                    ntu_readings.append(ntu_value)
                
                # 保持最近50個讀取值
                if len(ph_readings) > 50:
                    ph_readings.pop(0)
                if len(orp_readings) > 50:
                    orp_readings.pop(0)
                if len(ntu_readings) > 50:
                    ntu_readings.pop(0)
                
                # 顯示解析後的資料
                result_parts = []
                if ph_value is not None:
                    result_parts.append(f"pH: {ph_value:.2f}")
                if orp_value is not None:
                    result_parts.append(f"ORP: {orp_value}mV")
                if ntu_value is not None:
                    result_parts.append(f"NTU: {ntu_value}")
                
                print(f"📊 解析結果: {' | '.join(result_parts)}")
                
                # 儲存資料到資料庫
                if db.save_sensor_data(ph_value, orp_value, ntu_value):
                    print("✅ 資料儲存成功")
                else:
                    print("❌ 資料儲存失敗")
                
                # 計算平均值
                if len(ph_readings) >= 5:
                    avg_ph = sum(ph_readings[-5:]) / 5
                    print(f"📈 最近5次pH平均值: {avg_ph:.2f}")
                
                if len(orp_readings) >= 5:
                    avg_orp = sum(orp_readings[-5:]) / 5
                    print(f"📈 最近5次ORP平均值: {avg_orp:.0f}mV")
                
                if len(ntu_readings) >= 5:
                    avg_ntu = sum(ntu_readings[-5:]) / 5
                    print(f"📈 最近5次NTU平均值: {avg_ntu:.0f}")
                
                print("-" * 70)
                
            else:
                consecutive_errors += 1
                print(f"⚠️  無法解析資料 (連續錯誤: {consecutive_errors}/{max_consecutive_errors})")
                
                if consecutive_errors >= max_consecutive_errors:
                    print("❌ 連續解析錯誤過多，請檢查Arduino程式或連接")
                    break
                
                # 其他系統訊息
                if "初始化" in raw_data or "sensor" in raw_data.lower():
                    print(f"📝 系統訊息: {raw_data}")
                    consecutive_errors = 0  # 重置錯誤計數
            
            # 短暫延遲
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n⏹️  程式已停止")
    except Exception as e:
        print(f"❌ 發生未預期錯誤: {e}")
    finally:
        # 清理資源
        serial_reader.close()
        db.close()

def list_serial_ports():
    """列出所有可用的串列埠"""
    import serial.tools.list_ports
    
    ports = serial.tools.list_ports.comports()
    print("可用的串列埠:")
    for port in ports:
        print(f"  {port.device} - {port.description}")
    return [port.device for port in ports]

def test_database_connection(db_type):
    """測試資料庫連接"""
    print(f"🔍 測試 {db_type} 資料庫連接...")
    
    try:
        db = SensorDatabase(db_type)
        
        if db.connect():
            print("✅ 資料庫連接成功")
            if db.create_table():
                print("✅ 資料表準備完成")
            db.close()
            return True
        else:
            print("❌ 資料庫連接失敗")
            return False
            
    except Exception as e:
        print(f"❌ 連接測試失敗: {e}")
        return False

def main():
    print("🔬 Arduino 感測器資料讀取程式 (NTU調試版)")
    print("=" * 50)
    
    # 選擇資料庫類型
    print("請選擇資料庫類型:")
    print("1. 本地 PostgreSQL")
    print("2. Render PostgreSQL")
    
    while True:
        choice = input("請輸入選擇 (1 或 2): ").strip()
        if choice == '1':
            db_type = 'local'
            break
        elif choice == '2':
            db_type = 'render'
            break
        else:
            print("❌ 請輸入 1 或 2")
    
    # 測試資料庫連接
    if not test_database_connection(db_type):
        print("⚠️  資料庫連接失敗，程式無法繼續執行")
        return
    
    # 列出可用串列埠
    available_ports = list_serial_ports()
    
    if not available_ports:
        print("❌ 未找到可用的串列埠")
        return
    
    # 讓使用者選擇串列埠
    print(f"\n請選擇串列埠 (預設: {available_ports[0]}):")
    port_input = input("串列埠名稱: ").strip()
    port = port_input if port_input else available_ports[0]
    
    # 設定鮑率
    baudrate = 9600
    print(f"使用鮑率: {baudrate}")
    
    print(f"\n💾 資料將儲存到 {db_type} PostgreSQL 資料庫")
    print("🚀 程式啟動中...")
    
    # 開始讀取資料
    read_arduino_data(port, baudrate, db_type=db_type)

if __name__ == "__main__":
    main()