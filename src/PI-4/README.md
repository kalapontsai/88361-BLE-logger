以下為依據 REA 反組譯原始碼、Wireshark/pcapng 空中封包側錄與實機驗證結果更新的完整 `report.md` 內容：

---

# AZ Instrument 88361 (NiceLogger) — BLE 協定與實作完整報告

**日期**: 2026-11-03 **平台**: Raspberry Pi 4 (`nice-logger`, Kali Linux)

**目標設備**: AZ Instrument 88361 BLE 溫濕度記錄器 (`3C:E4:B0:81:0E:81`, `3C:E4:B0:81:18:48`)

---

## 1. 核心結論與真相

| 項目 | 舊假設 (錯誤) | REA / pcapng 實測真相 (正確) | 影響範圍 |
| --- | --- | --- | --- |
| **Wire 指令格式** | ASCII 字串 `MAC/00TT00CC...` 加 `\r\n`

 | **純二進位 Raw Bytes**：`[Opcode 2B BE] + Payload`

 | 舊字串格式被 MCU 靜默丟棄，無任何回覆

 |
| **Logger ID (Name) 定位** | 特殊內部私有暫存器變數 | 即為 **BLE 廣播設備名稱 (Complete Local Name)** 與 Manufacturer Data [3:9]

 | App 顯示的 Name 與廣播名稱完全連動

 |
| **讀取 ID 方法** | 僅透過 GATT FFF2 等待通知回覆

 | **雙軌制**：空閒時直接監聽空中廣播 (免連線)；GATT 需先送 `0x43` 校時

 | 避免因單獨送 `0x49` 狀態機未解鎖而逾時

 |
| **歷史紀錄讀取** | 需透過多次送出帶索引的封包分頁 (Paging)

 | **兩階段無分頁 (Phase 1 查筆數 $\rightarrow$ Phase 2 全量接收)**

 | 原廠 App 透過 Phase 2 一次性串流全部紀錄

 |
| **寫入 ID 驗證** | 只要收到廣播有 ID 就直接判定驗證結束 | **必須比對是否符合預期的新 ID**，否則會誤中 BlueZ 殘留快取 | 解決寫入後第二次重新讀取顯示舊值的瑕疵 |

---

## 2. REA 反組譯與 pcapng 封包指令對照

### 2.1 逆向架構對照 (`BleMeter` 與 `DeviceManager`)

透過 REA 反組譯原廠 Android App (Unity + IL2CPP 架構)，還原出完整的發送與接收資料流：

1. **指令封裝層**：
* 在 C# / IL2CPP 層呼叫 `InternalSendData` 時，字串雖然帶有 `<MAC>,<Hex>`。


* 進入 Java 底層 `BleMeter.doSendData(str)` 時，執行了 `str.split(",")[1..]`，**直接將前綴 MAC 丟棄**，僅將後續十六進位轉換為 Raw `byte[]` 發送至 `FFF1`。




2. **接收與分發層**：
* 記錄器由 `FFF2` 吐出資料時，Java 層將 Raw Bytes 轉為 Hex 字串組合為 `"<MAC>|" + ParseData(bytes)`，交由 IL2CPP `OnReceivedDataFromDeviceSend` 依照 Opcode 分發。





### 2.2 完整指令 Wire Format 對照表 (以 Raw Bytes 呈現)

所有指令寫入特徵值 **FFF1** (`0000fff1-0000-1000-8000-00805f9b34fb`)，一律要求 `response=True`。

| 動作名稱 | pcapng / REA 實際 Raw Bytes (Hex) | 長度 | 說明 |
| --- | --- | --- | --- |
| **校時握手 (0x43)** | `43 43` + 14 碼西元時間 ASCII (例: `43 43 32 30 32 36 31 31 30 33...`) | 16B | 連線後必備握手，通知 MCU 解鎖狀態機

 |
| **讀取組態 (0x61)** | `61 61` | 2B | 取得上下限門檻與取樣間隔

 |
| **讀取 Logger ID (0x49)** | `49 49` + 6 碼既有 ID ASCII (例: `49 49 32 35 30 30 31 34`) | 8B | 需在校時握手後發送，回覆帶有 `T` 結尾之 ID

 |
| **寫入 Logger ID (0x49)** | `49 49` + 6 碼新 ID ASCII (例: `49 49 4B 34 58 58 56 52`) | 8B | **嚴格限 6 碼**，寫入後斷線促使 MCU 存入 Flash |
| **ReadOut Phase 1 (0x64)** | `64 00 00 00 00` | 5B | 查詢筆數，回覆 3 Bytes (`[0:2]` 總筆數, `[2]` 幀數)

 |
| **ReadOut Phase 2 (0x64)** | `64 FF FF FF FF` | 5B | 啟動全量下載，連續回傳整段歷史資料

 |

---

## 3. BLE 廣播結構（24 Bytes，Company ID: `0x5002` / `20482`）

記錄器發出的 `ADV_IND` (主廣播) 與 `SCAN_RSP` (掃描回應) 結構：

```text
01 49 49 32 35 30 30 31 34 01 A0 00 03 0F 7F FD 7F 9B 00 00 0A 00 02 64

```

| Offset (Byte) | 原始值範例 | 意義與解析規則

 |
| --- | --- | --- |
| `[0]` | `0x01` | 協定版本識別碼

 |
| `[1]` | `0x49` ('I') 或 `0x48` ('H') | 運作狀態：`0x49` 為 Idle (待機)，`0x48` 為 Recording (記錄中)

 |
| `[2]` | `0x49` ('I') 或 `0x48` ('H') | 狀態確認重複碼

 |
| **`[3:9]`** | **`32 35 30 30 31 34`** | **Logger ID (6 碼 ASCII，例如 `"250014"`)**

 |
| `[9]` | `0x01` / `0x20` | 模式旗標

 |
| `[10:12]` | `A0 00` | 系統狀態旗標

 |
| `[12:14]` | `03 0F` | 機身內部溫度 T1 (0.1°F Big-Endian)；換算為 °C 公式：$((raw / 10.0) - 32) \times 5 / 9$

 |
| `[14:16]` | `7F FD` | 外部探棒溫度 T2 ($\ge \text{0x7F00}$ 代表未接探棒)

 |
| `[16:19]` | `7F 9B 00` | 固定保留位元組

 |
| `[19:21]` | `00 0A` | 記錄間隔 (秒，Big-Endian)，此處為 10 秒

 |
| `[21:23]` | `00 02` | 已記錄資料筆數 (Big-Endian)，此處為 2 筆

 |
| `[23]` | `0x64` | 電池剩餘電量百分比 (0x64 = 100%)

 |

> **特別注意**：當記錄器處於 **Recording 狀態 (`0x48`)** 時，`[3:8]` 會固定顯示為 `"Logg2"`。唯有在 **Idle 狀態 (`0x49`)** 下，廣播中才會攜帶真實的 Logger ID。
> 
> 

---

## 4. 服務與程式實作指南

### 4.1 廣播收集閘道器 (`nicelogger_gateway.py`)

* **執行方式**：由 `nicelogger_gateway.service` 常駐執行，主動掃描廣播，將遙測指標發布至 MQTT。


* **採集核心邏輯**：
```python
def parse_nicelog_adv(data: bytes):
    if len(data) < 24:
        return None
    state = data[1]
    if state not in (0x49, 0x48):
        return None

    # 擷取 Logger ID
    logger_id = data[3:9].decode("latin1", errors="ignore").strip()

    # 溫度換算
    raw_t1 = (data[12] << 8) | data[13]
    t1_c = (
        round(((raw_t1 / 10.0) - 32.0) * 5.0 / 9.0, 2)
        if raw_t1 < 0x7000
        else None
    )

    return {
        "logger_id": logger_id,
        "state": "Idle" if state == 0x49 else "Recording",
        "temperature_c": t1_c,
        "battery_pct": data[23],
        "log_count": (data[21] << 8) | data[22],
    }

```



```

### 4.2 Logger ID 讀寫工具 (`nicelogger_rw_id.py`)
* **完整工作流程**：
  1. **步驟 1 (免連線讀取)**：啟動 `BleakScanner` 掃描廣播，從 Manufacturer Data 或 Complete Local Name 取得當前 ID，同時取得 `BLEDevice` 物件。
  2. **步驟 2 (GATT 連線寫入)**：
     * 連線至 `BLEDevice` (設定 `pair=False`)。
     * 註冊 `FFF2` Notify 監聽[cite: 7]。
     * 送出 `0x43` 校時握手解鎖微控制器狀態機：`bytes([0x43, 0x43]) + YYYYMMDDHHMMSS`[cite: 7]。
     * 發送寫入新 ID 指令：`bytes([0x49, 0x49]) + new_id.encode('ascii')` (限制 6 碼)。
     * 等待 2 秒寫入 Flash，主動斷開 GATT 連線。
  3. **步驟 3 (目標驗證)**：
     * 啟動廣播驗證，**持續比對是否收到 `new_id`**，避開 BlueZ 殘留的上一包快取。

---

## 5. 踩坑紀錄與工程排錯備忘 (Gotchas)

### 踩坑 1：重新讀取驗證時出現「讀回舊值」的假性失敗
* **現象**：寫入新 ID 成功後，步驟 3 驗證時讀出的仍然是寫入前的舊 ID；但重新啟動程式執行步驟 1 時，卻能正確讀出新 ID。
* **原因**：Linux BlueZ 底層的 D-Bus 快取機制在連線斷開瞬間，仍暫存著連線前捕捉到的舊廣播封包。若驗證程式只要「收到任何一包廣播」就立刻回傳退出，極易命中該筆舊快取。
* **解法**：在步驟 3 改用 **目標過濾機制**（`verify_new_id_from_adv`），在超時倒數（20 秒）內持續監聽，直到空中飄來與預期新 ID 相符的廣播才判定成功並跳出。

### 踩坑 2：ASCII 字串指令被 MCU 靜默丟棄
* **現象**：發送 `3C:E4.../00490049` 或加 `\r\n`，GATT Write 雖然回傳成功，但記錄器永遠逾時不回覆[cite: 7]。
* **原因**：88361 底層藍牙透傳韌體期望的是 **二進位 Raw Bytes**，沒有 MAC 字串前綴，也沒有換行結尾[cite: 7]。
* **解法**：發送指令一律使用 `bytes([0x49, 0x49]) + payload` 格式封裝[cite: 7]。

### 踩坑 3：GATT 連線競爭與 Active Scanning 衝突
* **現象**：執行 `rw_id.py` 時，出現連線逾時 (`CancelledError` / `TimeoutError`)。
* **原因**：背景的 `nicelogger_gateway.service` 正在以 Active Scanning 模式高頻發送 `SCAN_REQ`[cite: 1, 7]，88361 為低功耗單工無線電，在密集回應掃描時無法接受新的連線請求。
* **解法**：執行連線讀寫前，必須先停止常駐服務：
  ```bash
  sudo systemctl stop nicelogger_gateway.service

```

讀寫完畢後再行啟動。

### 踩坑 4：嚴禁使用 `pair=True`

* **現象**：連線時卡死在 SMP 配對階段，最終逾時中斷。


* **原因**：88361 為 No-Bonding 設備，強制要求配對會觸發 BlueZ 的安全協商失敗。


* **解法**：`BleakClient(..., pair=False)`。



### 踩坑 5：CCCD 註冊拋出 `INSUFFICIENT_RESOURCE (17)`

* **現象**：連線後註冊 Notify 報錯 `GATT Protocol Error: Insufficient Resource`。


* **原因**：微控制器因異常中斷連線導致內部連線實例未清除，CCCD 被鎖死。


* **解法**：將記錄器電池拔除 5 秒後重裝，或靜置 30 秒以上待硬體看門狗重置。