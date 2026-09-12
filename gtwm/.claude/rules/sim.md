---
paths:
  - "src/gtwm/sim/**"
  - "sim/**"
  - "configs/sim/**"
  - "configs/realism/**"
  - "tests/sim/**"
---

# シミュレーション（C1 相当、MuJoCo）

## シーンと命名
- MJCF は `sim/assets/warehouse.xml`（本体）と `sim/assets/include/*.xml`（棚・ドック・コンベア）。単位はメートル・秒。床は 12 m × 8 m から始め、必要になるまで大きくしない。
- ゾーンは `<site name="zone:<id>">`（Dock_In, Inspect, Storage_A, Storage_B, Pick, Dock_Out）。棚位置は `slot:<rack>-<level>-<bay>`。
- 個体名は `pallet:<id>` `case:<id>` `agv:<id>` `worker:<id>` `cam:<id>`。オントロジー個体 ID（`gt:Pallet_0042`）との対応は `sim/assets/registry.yaml` に置き、コードに埋め込まない。
- AGV は箱ボディ＋速度アクチュエータ（差動2輪の簡略）。作業者はカプセルのキネマティック移動（スプライン経路）。ラグドールや接触の複雑化はしない。
- パレット・ケースは free joint の箱。積み重ねは最大3段。物性値は `configs/sim/physics.yaml`。

## レンダリング（macOS の制約）
- `mujoco.Renderer(model, height=128, width=128)` をメインスレッドで使う。スレッド・プロセス並列でレンダリングしない。EGL/OSMesa は macOS で使えない。
- 各カメラで RGB、セグメンテーション（`enable_segmentation_rendering()`）、深度を取る。遮蔽率は「セグメンテーションで見えている画素数 ÷ 深度無視の投影面積」で計算し、`occlusion[T,N]` に保存する。
- 既定解像度 128×128、10 Hz、カメラ4台。5分エピソードの生成が実時間の2倍以内に収まらない場合は解像度ではなく物体数を減らす。
- `mujoco.viewer.launch_passive` は `mjpython` 経由でしか動かない。テストとデータ生成から呼ばない。

## 時間とアンカー
- 物理 timestep 0.005 s、ログ 10 Hz。シミュレーション時計が真値。アンカーの時刻ジッタ・欠落は物理の中では入れず、書出時に `realism` 設定で加える（真値は必ず別に残す）。
- アンカー検出は `sim/sensors/anchors.py` の純粋 Python：RFID ゲート＝ゲート平面の通過、スキャン＝作業者が対象から 1 m 以内に 1 秒以上滞在（確率 `scan_prob`）、秤＝秤サイト上で静止 2 秒、扉＝AGV の通過、PLC＝コンベアの稼働状態。物理コールバックの中で判定しない。

## WMS モックと EPCIS
- `sim/wms_mock.py` はオーダー生成、作業割当、期待プロセス F（入荷→検品→棚入れ→ピッキング→出荷）を持ち、EPCIS 2.0 JSON-LD（ObjectEvent / AggregationEvent、bizStep・disposition は CBV 語彙）を発行する。
- 乖離注入は `configs/realism/*.yaml` の `injections:` で型（unscanned_move, wrong_slot, wrong_scan, late_registration, ghost_stock）と件数を指定し、注入台帳を `data/injections/<episode>.parquet` に書く。検知側はこの台帳を読まない。
- シナリオ列挙は `sim/scenarios.py`：オントロジーの制約（進入禁止、危険物隣接、容量）からシナリオを生成する。ドメインランダム化は照明・テクスチャ・初期配置のみ。

## データ書出（`gtwm sim gen`）
- 出力は `data/sim/<set>/<episode_id>/`：`cam_<id>.mp4`（h264, crf 18）、`masks.npz`（uint16 セグメント ID）、`depth.npz`（float16）、`poses.parquet`（T×N の位置・姿勢・ゾーン）、`occlusion.npz`、`events.parquet`（アンカー＋WMS イベント、真値時刻と観測時刻の両方）、`meta.json`（seed、設定、registry の版）。
- 生成は seed から完全に再現できること。`tests/sim/test_determinism.py` で同一 seed の poses 一致を検査する。
- 30秒 smoke（`make sim-smoke`）を常に通す。5分エピソードを `set=p0_train`（50本）、`p0_eval`（10本）として生成する。
