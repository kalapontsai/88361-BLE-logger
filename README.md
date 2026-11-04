# 88361-BLE-logger

AZ Instrument 88361（Nice Logger）BLE 溫濕度記錄器的逆向分析、協定實作與資料視覺化專案。

本倉庫收錄了透過 **REA 反組譯**、**nRF Sniffer / Wireshark 空中封包側錄** 與 **實機驗證**
所還原的完整 BLE 協定規格，以及一套可直接運作的資料收集 → MQTT → 網頁儀表板管線。

> **核心貢獻**：官方 App 未公開 88361 的通訊協定。本專案還原出其 wire format、
> 廣播封包結構與完整 opcode 集合，並以 Python 實作開源的替代方案。

---

## 目錄結構

```
88361-BLE-logger/
├── manual/                     # 原廠手冊與評估報告
│   ├── 88361 中文說明書.pdf
│   ├── 88361 英文說明書.pdf
│   ├── 88361 規格表.pdf
│   └── AZ88361_冰箱溫度量測能力評估報告.{pdf,pptx}
│
├── src/
│   ├── PI-4/                   # Raspberry Pi 4 執行環境（核心實作）
│   │   ├── README.md           # ★ BLE 協定完整報告 + 踩坑紀錄（先讀這份）
│   │   ├── REA report.md       # REA 反組譯實測報告
│   │   ├── nicelogger_gateway.py   # 廣播收集閘道器 → MQTT
│   │   └── nicelogger_rw_id.py     # Logger ID 讀寫工具
│   │
│   ├── apache/                 # 歷史記錄網頁（Apache + PHP + Chart.js）
│   │   ├── index.html          # 儀表板頁面
│   │   ├── app.js              # 輪詢與圖表繪製
│   │   ├── style.css
│   │   ├── api/records.php     # MySQL 直讀端點
│   │   └── chart.umd.min.js, chartjs-adapter-date-fns.bundle.js
│   │
│   ├── mqtt/                   # Mosquitto broker
│   │   ├── docker-compose.yml
│   │   ├── mosquitto.conf
│   │   └── data/mosquitto/     # 執行期狀態（已 gitignore）
│   │
│   ├── wireshark-pcap/         # nRF Sniffer 側錄封包（8 組 .pcapng）
│   │
│   └── official/               # 原廠 Android App（參考用）
│       └── Nice+Logger_1.0.025_APKPure.xapk
│
├── LICENSE
└── .gitignore
```

---

## 協定摘要

### Wire format

所有指令寫入 **FFF1** 特徵值（`0000fff1-0000-1000-8000-00805f9b34fb`），
格式為 **純二進位 Raw Bytes**：

```
[Opcode: 2 bytes big-endian] + Payload
```

> ⚠️ **沒有** MAC 前綴、**沒有** checkType、**沒有** CRLF。
> 過去使用的 ASCII 格式（如 `3CE4B0810E81/00490049`）會被 MCU 靜默丟棄，永不回覆。

### Opcode 對照表

| 動作 | Raw Bytes (Hex) | 長度 | 說明 |
|---|---|---|---|
| 校時握手 | `43 43` + `YYYYMMDDHHMMSS` (ASCII) | 16B | 連線後必備，解鎖 MCU 狀態機 |
| 讀 Logger ID | `49 49` + 6 碼 ID (ASCII) | 8B | 回覆以 `T` 結尾之 ID |
| 寫入 Logger ID | `49 49` + 6 碼新 ID (ASCII) | 8B | 嚴格限 6 碼；寫入後斷線使 MCU 存入 Flash |
| 讀組態 | `61 61` | 2B | 取得上下限門檻與取樣間隔 |
| ReadOut Phase 1 | `64 00 00 00 00` | 5B | 查詢歷史筆數 |
| ReadOut Phase 2 | `64 FF FF FF FF` | 5B | 啟動全量下載（無分頁） |

### BLE 廣播結構（24 Bytes，Company ID `0x5002` / 20482）

```
01 49 49 32 35 30 30 31 34 01 A0 00 03 0F 7F FD 7F 9B 00 00 0A 00 02 64
```

| Offset | 意義 |
|---|---|
| `[0]` | 協定版本 |
| `[1]` `[2]` | 狀態：`0x49` = Idle、`0x48` = Recording |
| `[3:9]` | **Logger ID**（6 碼 ASCII，例如 `250014`） |
| `[9]` | 模式旗標 |
| `[10:12]` | 系統狀態旗標 |
| `[12:14]` | 機身內部溫度 T1（0.1°F Big-Endian） |
| `[14:16]` | 外部探棒溫度 T2（`>= 0x7F00` 表示未接探棒） |
| `[16:19]` | 保留 |
| `[19:21]` | 記錄間隔（秒） |
| `[21:23]` | 已記錄資料筆數 |
| `[23]` | 電池電量百分比 |

> **注意**：設備處於 **Recording 狀態（`0x48`）** 時，`[3:8]` 固定顯示 `Logg2`。
> 唯有 **Idle 狀態（`0x49`）** 下，廣播才會攜帶真實 Logger ID。

溫度換算（0.1°F → °C）：`((raw / 10.0) - 32) × 5 / 9`

完整細節見 **[src/PI-4/README.md](src/PI-4/README.md)**。

---

## 系統架構

```
┌──────────────┐  BLE 廣播   ┌─────────────────┐   MQTT    ┌────────────┐
│  88361 記錄器 │ ──────────► │ nicelogger_     │ ────────► │ Mosquitto  │
│  (x2 台)     │              │ gateway.py      │  :1883    │  broker    │
└──────────────┘              └─────────────────┘           └─────┬──────┘
                                                                    │ MQTT
                                                              ┌─────▼──────┐
                                                              │ mqtt-worker │
                                                              │ (Windows)  │
                                                              └─────┬──────┘
                                                                    │ SQL
                                                              ┌─────▼──────┐
                                                              │   MySQL    │
                                                              └─────┬──────┘
                                                                    │ HTTP
                                                              ┌─────▼──────┐
                                                              │ Apache/PHP │
                                                              │  儀表板    │
                                                              └────────────┘
```

---

## 快速開始

### 1. 啟動 MQTT Broker

```bash
cd src/mqtt
docker compose up -d
```

Mosquitto 設定於 `src/mqtt/mosquitto.conf`（listener 1883、匿名存取、持久化）。
此檔案使用 docker-compose 中定義的外部網路 `apache_mynet`，需先由其他 compose 檔建立：

```bash
docker network create apache_mynet
```

### 2. 執行廣播收集閘道器（Pi 4）

```bash
pip install bleak paho-mqtt
python3 src/PI-4/nicelogger_gateway.py
```

閘道器會持續掃描 BLE 廣播，解析溫度／電量／筆數，並發布至
MQTT topic `sensor/nicelogger/<device_id>`。

> **部署提醒**：目標 MAC 與 MQTT broker 位址目前寫在 `nicelogger_gateway.py` 內，
> 請依你的環境調整 `TARGET_MACS` 與 `MQTT_BROKER`。

### 3. 讀寫 Logger ID

```bash
sudo systemctl stop nicelogger_gateway.service   # 避免掃描競爭
python3 src/PI-4/nicelogger_rw_id.py
```

工具會依序執行：免連線廣播讀取 → GATT 連線寫入 → 目標過濾驗證。

### 4. 部署網頁儀表板

將 `src/apache/` 內容部署至 Apache `htdocs`（例如 `/nicelogger-history/`），
並依 `src/apache/api/records.php` 的說明設定資料庫連線設定檔：

```
config.php 解析順序：
1. 環境變數 NICELOGGER_CONFIG 指定的檔案
2. <apache html>/mqtt/config.php
3. 本機 src/apache/api/config.php
```

找不到設定檔時端點回傳 `503 {"error": "config_missing"}`，而非靜默以空值連線。
實際的 `config.php` 含資料庫帳密，已列入 `.gitignore`，不進版控。

---

## 主要踩坑紀錄

完整清單見 [src/PI-4/README.md](src/PI-4/README.md)，以下為最關鍵幾項：

| 問題 | 原因 | 解法 |
|---|---|---|
| ASCII 指令無回覆 | 韌體要求 Raw Bytes | 一律用 `bytes([opcode, opcode]) + payload` |
| 寫入後讀回舊 ID | BlueZ D-Bus 殘留舊廣播快取 | 驗證時比對目標 ID，不只檢查「有收到」 |
| 連線逾時 | 常駐服務 Active Scanning 與連線競爭 | 讀寫前先 `systemctl stop` |
| 卡死在配對階段 | 88361 為 No-Bonding 裝置 | `BleakClient(..., pair=False)` |
| CCCD `INSUFFICIENT_RESOURCE` | MCU 內部連線實例未清除 | 拔電池 5 秒重裝，或靜置 30 秒 |

---

## 逆向分析素材

| 來源 | 內容 |
|---|---|
| `src/wireshark-pcap/` | 8 組 nRF Sniffer 側錄封包：id_save、config_save、record_start/stop、read_out、broadcast |
| `src/official/` | 原廠 Android App（Unity + IL2CPP），供 REA 反組譯比對 |
| `src/PI-4/REA report.md` | 反組譯結果與實測驗證對照 |

---

## 授權

本專案程式碼採 [MIT License](LICENSE)。

第三方程式碼與文件保留其各自授權，詳見 LICENSE 檔案末尾的 Third-party notices：
Chart.js 與 date-fns adapter 為 MIT；原廠 Nice Logger App 與 AZ Instrument 手冊為專有權利，
僅作為互通性參考收錄。

---

## 免責聲明

本專案與 AZ Instrument 無任何關聯。所有協定分析僅基於使用者自有設備與公開文件，
僅供技術研究與個人使用。
