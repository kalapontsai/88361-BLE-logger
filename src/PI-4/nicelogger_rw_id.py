#!/usr/bin/env python3
import asyncio
import datetime
import random
import string
import sys
from typing import Optional, Tuple
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

UUID_WRITE = "0000fff1-0000-1000-8000-00805f9b34fb"
UUID_NOTIFY = "0000fff2-0000-1000-8000-00805f9b34fb"

class NiceLoggerRWID:
    def __init__(self, mac_address: str):
        self.mac = mac_address.upper()
        self.client: Optional[BleakClient] = None
        self.read_id_resp: Optional[str] = None
        self.id_event = asyncio.Event()

    def generate_random_id(self, length: int = 6) -> str:
        """生成 6 碼大寫英數隨機 ID (硬體標準長度)"""
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choices(chars, k=length))

    @staticmethod
    def extract_id_from_adv(adv_data: AdvertisementData) -> Optional[str]:
        """從 BLE 廣播 Manufacturer Data (0x5002) 或名稱解析 Logger ID"""
        # 1. 優先從 Manufacturer Data 解析 (Idle 狀態: data[1]==0x49, data[3:9] 為 6 碼 ID)
        mfr_data = adv_data.manufacturer_data.get(0x5002) or adv_data.manufacturer_data.get(20482)
        if mfr_data and len(mfr_data) >= 24:
            state = mfr_data[1]
            if state in (0x49, 0x48):
                lid = mfr_data[3:9].decode("latin1", errors="ignore").strip()
                if lid and lid != "Nicelog":
                    return lid

        # 2. 備援：從 Local Name 提取 (如 'HLogg01' -> 'Logg01', 'I250014' -> '250014')
        if adv_data.local_name:
            name = adv_data.local_name.strip()
            if len(name) > 1 and name[0] in ("H", "I"):
                return name[1:]
            if name not in ("NiceLogger", ""):
                return name
        return None

    async def scan_target_device(self, timeout: float = 12.0) -> Tuple[Optional[str], Optional[BLEDevice]]:
        """步驟 1: 掃描廣播，解析目前既有 ID 並鎖定 BLEDevice 物件"""
        found_id: Optional[str] = None
        target_dev: Optional[BLEDevice] = None
        event = asyncio.Event()

        def _callback(device: BLEDevice, adv: AdvertisementData):
            nonlocal found_id, target_dev
            if device.address.upper() == self.mac:
                target_dev = device
                lid = self.extract_id_from_adv(adv)
                if lid:
                    found_id = lid
                    event.set()

        scanner = BleakScanner(detection_callback=_callback, scanning_mode="active")
        await scanner.start()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await scanner.stop()

        return found_id, target_dev

    async def verify_new_id_from_adv(self, expected_id: str, timeout: float = 20.0) -> Tuple[bool, Optional[str]]:
        """
        步驟 3: 專門驗證新寫入 ID。
        忽略 BlueZ 快取中的舊封包，持續監聽直到收到與 expected_id 相符的廣播才退出。
        """
        matched = False
        last_seen_id: Optional[str] = None
        event = asyncio.Event()

        def _callback(device: BLEDevice, adv: AdvertisementData):
            nonlocal matched, last_seen_id
            if device.address.upper() == self.mac:
                lid = self.extract_id_from_adv(adv)
                if lid:
                    last_seen_id = lid
                    if lid == expected_id:
                        matched = True
                        event.set()

        scanner = BleakScanner(detection_callback=_callback, scanning_mode="active")
        await scanner.start()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await scanner.stop()

        return matched, last_seen_id

    def notification_handler(self, sender, data: bytearray):
        """解析 FFF2 Notify 回傳 Raw Bytes"""
        if len(data) >= 8 and data[0] == 0x49 and data[1] == 0x49:
            id_bytes = data[2:8]
            self.read_id_resp = id_bytes.decode("ascii", errors="ignore").strip()
            self.id_event.set()

    async def run(self):
        print(f"=== [步驟 1] 掃描目標設備與現有 Logger ID ===")
        print(f"[*] 正在監聽廣播: {self.mac} (請先按亮記錄器實體螢幕)...")
        adv_id, device = await self.scan_target_device(timeout=12.0)

        if not device:
            raise RuntimeError(f"找不到裝置 {self.mac}。請確認螢幕點亮且已暫停 gateway 服務。")

        if adv_id:
            print(f"[+] 透過 BLE 廣播成功取得現有 Logger ID: 【 {adv_id} 】\n")
        else:
            print(f"[*] 裝置在線，但未從廣播直接擷取到 ID，將透過 GATT 建立連線。\n")

        new_id = self.generate_random_id(6)
        print(f"=== [步驟 2] 連線並寫入新 Logger ID: 【 {new_id} 】 ===")
        print(f"[*] 建立 GATT 連線 (pair=False)...")

        # 延遲 1.5 秒讓 MCU 退出前置掃描回應視窗
        await asyncio.sleep(1.5)
        self.client = BleakClient(device, timeout=20.0)
        await self.client.connect()

        try:
            print("[+] 連線成功，等待 1.2 秒讓 MCU 緩衝穩定...")
            await asyncio.sleep(1.2)

            # 註冊 Notify
            await self.client.start_notify(UUID_NOTIFY, self.notification_handler)
            print("[+] FFF2 Notify 註冊成功！")
            await asyncio.sleep(0.4)

            # 1. 執行 0x43 校時握手 (解鎖狀態機)
            t_now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            cmd_43 = bytes([0x43, 0x43]) + t_now_str.encode("ascii")
            print(f"  [TX RAW] 送出 0x43 校時握手 ({len(cmd_43)}B)")
            await self.client.write_gatt_char(UUID_WRITE, cmd_43, response=True)
            await asyncio.sleep(0.5)

            # 2. 透過 GATT 發送 0x49 讀取現有 ID 驗證通道
            self.id_event.clear()
            cmd_read_49 = bytes([0x49, 0x49]) + (adv_id.encode("ascii") if adv_id else b"250014")
            print(f"  [TX RAW] 送出 0x49 讀取指令 ({len(cmd_read_49)}B)")
            await self.client.write_gatt_char(UUID_WRITE, cmd_read_49, response=True)
            try:
                await asyncio.wait_for(self.id_event.wait(), timeout=3.0)
                print(f"  [RX GATT] 讀取 ID 成功回應: 【 {self.read_id_resp} 】")
            except asyncio.TimeoutError:
                print("  [!] GATT 0x49 讀取逾時，繼續執行寫入流程...")

            # 3. 發送 0x49 寫入新 ID
            cmd_write_49 = bytes([0x49, 0x49]) + new_id.encode("ascii")
            print(f"  [TX RAW] 寫入新 ID: bytes([0x49, 0x49]) + '{new_id}' ({len(cmd_write_49)}B)")
            await self.client.write_gatt_char(UUID_WRITE, cmd_write_49, response=True)

            print("[+] 寫入指令發送完畢，等待 2 秒讓 MCU 完成 Flash / EEPROM 存儲...")
            await asyncio.sleep(2.0)

        finally:
            if self.client and self.client.is_connected:
                await self.client.disconnect()
                print("[*] 藍牙連線已安全斷開，記錄器恢復廣播模式。")

        # 步驟 3: 重新監聽廣播驗證
        print("\n=== [步驟 3] 監聽最新廣播驗證寫入結果 ===")
        print("[*] 提示：連線已斷開，請【實體按一下記錄器按鈕喚醒螢幕】刷新廣播！")
        print(f"[*] 等候新廣播封包中 (預期新 ID: {new_id}，最多等候 20 秒)...")
        
        # 先等待 1.5 秒讓硬體廣播 Radio 重新啟動
        await asyncio.sleep(1.5)
        success, final_id = await self.verify_new_id_from_adv(new_id, timeout=20.0)

        print("========================================")
        print(f"  [廣播解析] 儲存後新 Logger ID: 【 {final_id} 】")
        if success:
            print("  [狀態] ★ 寫入與儲存驗證成功！")
        else:
            print(f"  [狀態] 讀回值 ({final_id}) 與設定值 ({new_id}) 尚未相符。")
            print("  [說明] 可能是廣播間隔延遲，可直接重新執行腳本確認。")
        print("========================================\n")

def main():
    target_mac = sys.argv[1].strip() if len(sys.argv) > 1 else "3C:E4:B0:81:0E:81"
    runner = NiceLoggerRWID(target_mac)
    asyncio.run(runner.run())

if __name__ == "__main__":
    main()
