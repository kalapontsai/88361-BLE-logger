# AZ Instrument 88361 — BLE 協定實測報告

**日期**: 2026-11-02　**平台**: Raspberry Pi 4 (nice-logger, Tailscale 100.102.221.71)
**狀態**: Logger ID / 組態 / 即時資料讀取 ✅ 已驗證；歷史紀錄 (ReadOut) ❌ 未觸發

---

## 1. 結論摘要

| 項目 | 狀態 | 證據 |
|---|---|---|
| **Wire 格式** | ✅ 已確認 | raw bytes: `[opcode:2 BE] + payload` |
| **讀 Logger ID** | ✅ 可用 | `43 43 <time>` → `49 49 250014` → `49 49 '250014' 'T'` |
| **讀組態 (0x61)** | ✅ 可用 | `61 F0 01 F4 01 40 01 F4 01 40 7F 9B 7F 9B` |
| **讀即時資料 (VDATA)** | ✅ 可用 | `43 43 '202' 'VDATA' 02 01 06 1B FF 02` |
| **廣播欄位結構** | ✅ 完整解出 | 24 bytes 全欄位對照表（§4） |
| **歷史紀錄 ReadOut (0x64)** | ❌ 未觸發 | 已試 9 種 payload 組合皆無回應 |
| **gateway 生產 bug** | ✅ 已修並驗證 | 廣播從未進 MQTT → 現在正常 |

---

## 2. 關鍵發現：wire 格式是 raw bytes

之前的失敗根因是**格式搞錯**。正確格式：

```
[opcode: 2 bytes big-endian] + payload
```

**沒有** MAC 前綴、**沒有** checkType、**沒有** CRLF。

已排除的錯誤格式（實測全部 0 回應）：

| 格式 | 範例 |
|---|---|
| MAC + ASCII hex | `3CE4B0810E81/00490049` |
| MAC + ASCII hex + CRLF | `3CE4B0810E81/00430043<time>` + CR LF |
| 裸 ASCII hex | `00490049` |
| 4-byte 大頭端標頭 | `3CE4B0810E81/00640000` (type+checkType) |

> 這個結論與 2026-11-02 08:48 那次 nRF Sniffer 空氣擷取一致
> （「推翻舊 skill 的 ASCII wire format，實際是 raw bytes」）。
> 問題出在 Pi 上的 `nice_logger/ble_client.py` 與 `nicelogger_rw_id.py`
> 都沿用了錯誤的 ASCII 字串格式。

---

## 3. 已驗證的 opcode

### 3.1 設定時間 (0x43)

```
TX: 43 43 32 30 32 36 31 31 30 32 32 30 33 38 33 35 34
     op    '20261102203809'

RX: 43 43 32 30 32 56 44 41 54 41 02 01 06 1B FF 02
     op   '202' 'VDATA' + 02 01 06 1B FF 02
```

### 3.2 讀 Logger ID (0x49)

```
TX: 49 49 32 35 30 30 31 34
     op    '250014'

RX: 49 49 32 35 30 30 31 34 54
     op    '250014' + 'T'
```

Logger ID = 6 個 ASCII 字元 + `T` 結尾。**完整流程**：先送 `0x43` 校時，
再送 `0x49` 讀 ID。

### 3.3 讀組態 (0x61)

```
TX: 61 61
RX: 61 F0 01 F4 01 40 01 F4 01 40 7F 9B 7F 9B
```

| Offset | 值 | 推測意義 |
|---|---|---|
| 1..2 | `F0 01` | 閥值高 (0x01F0 = 496) |
| 3..4 | `F4 01` | 閥值低 (0x01F4 = 500) |
| 5..6 | `40 01` | 設定 (320) |
| 7..8 | `F4 01` | 設定 (500) |
| 9..10 | `40 01` | 設定 (320) |
| 11..12 | `7F 9B` | 探棒未接（與廣播 [16:18] 相同） |
| 13..14 | `7F 9B` | 同上 |

### 3.4 不回應的 opcode（已測）

`49`(單獨) · `64` · `40` · `50` · `53` · `58` · `41` · `64 00 00` · `64 FF FF` ·
`64 64 00` · `64 + sn(int LE/BE)` · `64 + tz`

---

## 4. 廣播結構（24 bytes, mfr_id=0x5002）★完整解出

實際廣播位元組：

```
01 49 49 32 35 30 30 31 34 01 A0 00 03 0F 7F FD 7F 9B 00 00 0A 00 02 64
```

| Offset | 值 | 意義 |
|---|---|---|
| 0 | `0x01` | type / version |
| 1 | `0x49` 'I' | 狀態 + opcode（`0x49`=Idle/SetID，`0x48`=Recording） |
| 2 | `0x49` 'I' | 狀態重複 |
| **3..8** | **`250014`** | **Logger ID（6 個 ASCII 字元）** |
| 9 | `0x01` | 模式（idle=`0x01`，recording=`0x20`） |
| 10..11 | `A0 00` | flags |
| 12..13 | `03 0F` | 溫度1（0.1°F BE）= 78.3°F = 25.72°C |
| 14..15 | `7F FD` | 溫度2 / 探棒（≥ `0x7F00` = 未接） |
| 16..18 | `7F 9B 00` | 固定 |
| 19..20 | `00 0A` | 間隔秒數 = 10 |
| 21..22 | `00 02` | 已記錄筆數 = 2 |
| 23 | `64` | 電量 = 100% |

錄影中的裝置 `3C:E4:B0:81:18:48`：

```
01 48 4C 6F 67 67 32 20 20 01 A1 00 02 FD 7F FD ...
      [3:8] = 'Logg2'（不是 Logger ID）
```

> **重要**：Logger ID **只在 idle 狀態下**出現在廣播裡。
> 錄影中時 [3:8] 是 `"Logg2"`。要讀 ID 需先讓裝置停止錄影。

---

## 5. 修好的兩個生產 bug

### Bug 1：廣播解析器 tag 檢查錯誤 → 資料從未進入 MQTT

`nicelogger_gateway.py` 的 `parse_nicelog_adv()` 檢查 `data[2:9]` 是否含
`"Nicelog"`，但該欄位實際是 Logger ID（idle）或 `"Logg2"`（recording），
**永遠不含 "Nicelog"** → 函式恆回 `None` → 所有廣播被靜默丟棄。

修正：

```python
state = data[1]
if state not in (0x49, 0x48):
    return None
logger_id = data[3:9].decode("latin1", errors="ignore").strip()
```

並在回傳 dict 加上 `logger_id`、`state` 兩個欄位。

**修正前**：gateway 跑了 22 分鐘，一筆都沒送出。
**修正後**：

```
[logger_02 | 3C:E4:B0:81:0E:81] 25.72°C | [Idle] ID=250014 | 電量: 100% | 筆數: 2 -> MQTT (sensor/nicelogger/logger_02) 已送出
```

已驗證 MySQL 歷史 API (`/mqtt/api/latest_nicelogger.php`) 最新資料含
`"logger_id":"250014","state":"Idle"`（舊資料則無此欄位）。

備份：`nicelogger_gateway.py.bak-2026-11-02-tagfix`（v1）、
`.bak-2026-11-02-parsefix`（原始版）

### Bug 2：systemd unit 的 ExecStart 指向不存在的 venv

```
ExecStart=/home/kali/nice-logger-pi/nicelogger_env/bin/python ...   # 該目錄已被刪除
```

Pi 上只有 `.venv` 與 `venv`（皆含 paho + bleak）。systemd 啟動 gateway
必然失敗。修正腳本已備妥：

```bash
sudo bash /home/kali/nice-logger-pi/fix_gateway_unit.sh
```

（unit 檔為 root 所有，需要 sudo。）

---

## 6. 使用方式

```bash
PY=/home/kali/nice-logger-pi/.venv/bin/python3

# 讀 Logger ID
$PY /home/kali/nice-logger-pi/nl_read.py id     3C:E4:B0:81:0E:81
# 讀組態
$PY /home/kali/nice-logger-pi/nl_read.py config 3C:E4:B0:81:0E:81
# 讀即時資料 (VDATA)
$PY /home/kali/nice-logger-pi/nl_read.py live   3C:E4:B0:81:0E:81
```

輸出範例：

```
[+] 已連線並訂閱 FFF2 notify
  -> set-time 0x43: 43433230323631313032323034303139
    [RX] 434332303256444154410201061BFF02
  -> read-id 0x49: 4949323530303134
    [RX] 4949323530303134

==============================================
  Logger ID : 250014
  MAC       : 3C:E4:B0:81:0E:81
==============================================
```

---

## 7. 已完成：歷史紀錄（ReadOut / 0x64）

App 的「Download」按鈕對應 `DeviceManager.ReadOut(uint sn, string timeZone)`（RVA `0xE3A31C`）。

### 7.1 App 實際送出的指令（僅兩條）

```
0xE3A3AC  ldr x9, [x9, #0xd8]   ; LIT '64,00,00,00,00'    phase-1  (type = 0)
0xE3A3BC  bl  #0xe3fb48         ; InternalSendData
0xE3A3D4  ldr x9, [x9, #0xd0]   ; LIT '64,FF,FF,FF,FF'    phase-2  (type = 0xFFFF)
0xE41E4  bl  #0xe3fb48          ; InternalSendData
```

`InternalSendData` (RVA `0xE3FB48`) 只把字串塞進 send queue，真正的 byte 組裝在 Java 層。

### 7.2 完整資料流（含 Java plugin 層）

| 層 | 函式 | 行為 |
|---|---|---|
| Java | `BleMeter.doSendData(str)` | `str.split(",")[1..]` → `Integer.parseInt(x,16)` → byte[]（**丟掉第一段 MAC**） |
| Java | `BleMeter.ParseData(byte[])` | `",".join("%02X" % b)` → 例：`2F,89,3F,56` |
| Java | `onBTData` | 組成 `"<MAC>|" + ParseData(bytes)` 送給 Unity |
| IL2CPP | `OnWriteDataOk` (RVA `0xE194C8`) | `Split('|')` 取第 1 段 |
| IL2CPP | `OnReceivedDataFromDeviceSend` (RVA `0xE3EF64`) | 讀 opcode / checkType，dispatch |
| IL2CPP | `RecordData.Parse` (RVA `0xE42C84`) | type==0 → ParseInfo；type==0xFFFF → 取 total |
| IL2CPP | `RecordData.ParseInfo` (RVA `0xE41B10`) | `total`, `frameCount` |
| IL2CPP | `RecordData.ParseData` (RVA `0xE41FA0`) | 逐筆解析紀錄、組 CSV |

### 7.3 `Parse(data, index, length)` 語意（RVA `0xE211F4`）

```
0xE21234  add w1, w22, w22, lsl #1   ; start = index * 3
0xE2124C  add w8, w21, w21, lsl #1   ; length * 3
0xE21258  sub w2, w8, #1             ; count = length*3 - 1
0xE2125C  bl  #0x18bf7c4             ; String.Substring(start, count)
```

field 為固定寬度 2 個 hex char、以 `,` 分隔，所以 `index*3` 正好跳過前一組 field 與其逗號。

```python
def app_parse(data, index, length):
    start = index * 3
    if start >= len(data):
        return 0
    chunk = data[start : start + length * 3 - 1]
    return int(chunk.replace(",", "") or "0", 16)
```

### 7.4 兩階段流程

```
phase-1  寫入 bytes([0x64,0x00,0x00,0x00,0x00])
         回覆 3 bytes → data = "00,02,04"
         total      = app_parse(data, 0, 2)   # Substring(0,5) 去逗號 → 0x0002 = 2
         frameCount = app_parse(data, 2, 1)   # Substring(6,2)            → 0x04   = 4

phase-2  寫入 bytes([0x64,0xFF,0xFF,0xFF,0xFF])
         回覆一次帶回「全部」紀錄 → data = "2F,89,3F,56"
         每筆紀錄佔 2 個 field；每 frameCount 筆之後多 2 個 field 的 group header
```

`ParseData` 索引推進規則（RVA `0xE42648` / `0xE42314`）：

```
每筆紀錄  idx += 2
且當 rec > 0 且 rec % frameCount == 0 時，額外 idx += 2（group header）
```

### 7.5 關鍵結論：不需要 paging

**一次 phase-2 回覆就含全部紀錄。** 先前誤判為「phase-2 只回第一筆、需要 indexed command」
（試過 `64 FF FF <n> 64`、`64 <count> 00 00 64` 全部 no reply），
真正原因是**解析單位錯了**：App 每筆紀錄佔 2 個 field，我們把整包當成 1 筆。

### 7.6 實機驗證（2026-11-03 07:51，Pi `nice-logger`）

```
[+] connected 3C:E4:B0:81:0E:81 / notify FFF2
  -> 0x43 set-time: 43433230323631313033303735313433
    [RX] 434332303256444154410201061BFF02      'CC202VDATA...'

=== ReadOut phase 1 (query count) ===
  -> 0x64 phase1: 6400000000
    [RX] 000204
  data       : 00,02,04
  total      : 2
  frameCount : 4

=== ReadOut phase 2 (fetch records) ===
  -> 0x64 phase2: 64FFFFFFFF
    [RX] 2F893F56
  data       : 2F,89,3F,56
  records    : ['0x2f89', '0x3f56']

  total     : 2
  records   : [12169, 16214]
```

輸出檔：`/tmp/readout.json`、`/tmp/readout.csv`

```csv
index,value,value_hex
1,12169,2F89
2,16214,3F56
```

### 7.7 尚待釐清

`RecordData.m_ValueTimeStamps` 為 `long[]`，`[12169, 16214]` 推測為 timestamp offset
（相差 4045 s ≈ 67 min）。實際溫溼度值位於 `m_GroupRawData` / `_NeedReceiveLength`
對應的另一段資料流，尚未對應回 °C / %RH。

### 7.8 工具

`/home/bt994846/.openclaw/workspace/scripts/nice-logger/nl_readout.py`
（Pi 端：`/home/kali/nice-logger-pi/nl_readout.py`）

```
python nl_readout.py [MAC] [--json OUT.json] [--csv OUT.csv]
```

---

## 8. 踩坑紀錄

| 坑 | 解法 |
|---|---|
| `bluetoothctl pair` 回報 `No agent is registered` | 需常駐 agent；用 FIFO 保持 stdin 開啟，agent 死則 link key 丟棄、`Paired` 退回 `no` |
| `pkill -f "pattern"` 匹配到自己的 shell → SSH exit 255 無輸出 | 用 `pgrep -f "pat[.]ern"` 再 kill pid |
| `pgrep -f <pattern>` 同樣匹配自己的 `bash -c` | 同上，`[.]` 寫法 |
| `scp -o IdentityFile ~/path` 在巢狀 shell 中 `~` 不展開 | 用 `ssh <alias>` 直接吃 `~/.ssh/config` |
| 系統 `python3` 沒有 bleak | 用 `/home/kali/nice-logger-pi/.venv/bin/python3` |
| `bluetoothctl remove <MAC>` 會清 GATT 快取 | 改用 `bluetoothctl power off/on` |
| systemd `LocalForward 8000` 被占用會噴 bind 錯誤 | 無害，忽略即可 |
