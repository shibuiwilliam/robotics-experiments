# 進捗（Claude Code が作業ごとに更新する）

最終更新: 2026-09-12

## 現在のフェーズ
P0 相当（シミュレーション基盤）。CLAUDE.md「現在のフェーズと着手順」の 1 から開始。

## 着手順チェックリスト
- [x] 1. 足場（pyproject / uv / Makefile / ruff / mypy / pre-commit / gtwm doctor）
- [x] 2. sim（MJCF 倉庫、センサ、アンカー、WMS モック、`make sim-smoke`）
- [ ] 3. kg（gt-core.ttl、SHACL 3本、KGStore、EPCIS 取込）
- [ ] 4. wm（エンコーダ、スロット、動態、`make train-smoke`）
- [ ] 5. grounding（α / γ / ε / 同一性 / 乖離台帳）
- [ ] 6. eval（ランナー、EXP-01/02/03/06）
- [ ] 7. whatif + ui
- [ ] 8. P1 相当（realism、EXP-04/05/11）
- [ ] 9. P2 相当（概念発見、2拠点連合、EXP-07/08/09、EXP-10 準備）

## 実験の状態
| EXP | 仮説 | 状態 | 最新結果（runs/ パス） | 判定 |
|---|---|---|---|---|
| EXP-01 | H1 | 未着手 | | |

## 既知の制約・記録
- MPS fallback が発生した op：（未計測、着手順4で計測）
- `platform: linux/amd64` を使ったサービス：なし
- 計画書からの差分：全フェーズをシミュレーションで実施（CLAUDE.md 参照）
- `make setup` の `pre-commit install` がこの開発機では失敗する：親リポジトリ（`robotics-experiments`）の `core.hooksPath` が明示的に `.git/hooks`（既定値と同じ）に設定されており、pre-commit がこれを検出すると安全のためインストールを拒否する。git config の変更は方針上行わないため、`uv run pre-commit install` を手動で通すか `core.hooksPath` を外すかはユーザー側の判断に委ねる。`doctor` / `lint` / `test` は `pre-commit install` に依存せず単独で緑になることを確認済み。
- 開発機には mujoco が `uv sync --all-extras` で導入済みで、`gtwm doctor` のオフスクリーン描画チェックは OK。

## 着手順2（sim）の実装メモ（2026-09-12）
- MJCF は `sim/assets/generate_mjcf.py` で生成する静的ファイル（`warehouse.xml` + `include/*.xml`）。レイアウトを変える場合はこのスクリプトを編集して再実行する。
- 実体数：ゾーン6・ラック4×スロット9=36・ドック2・コンベア1・AGV2（3自由度の簡易平面モデル：スライドx/y＋ヨー、真の差動2輪ではない簡略化）・作業者3（mocap キネマティック、Catmull-Rom スプライン巡回）・パレット20＋ケース40（自由関節、最大3段スタック10組）・カメラ4。
- 遮蔽率は「セグメンテーション可視画素数 ÷ 解析的に投影したバウンディングボックスの外接矩形面積」で近似（2回目のレンダリングによる真のシルエット計算はコスト上見送り、外接矩形近似である旨を `render.py` に明記）。
- 30秒 smoke（`make sim-smoke`）の生成時間は **7.1秒**（実時間の約0.24倍、目標2倍以内に対し余裕あり）。オブジェクト数の削減は不要だった。内訳目安：物理6000ステップ≈0.8秒、4カメラ×3パス×300フレームのレンダリング≈3.2秒（レンダラーのウォームアップ後）。
- WMS 記録イベントは、着手順2の時点ではアンカー検出結果から即時導出している（gate:1→receiving、gate:2→shipping、scale→inspecting、scan+ゾーンで storing/picking）。遅延・欠落・誤登録などの乖離注入（`InjectionConfig`）は全件数0のデフォルトで no-op、実装は着手順8。
- `tests/sim`（決定性・アンカー検出・書出形状、計8件）は `make test-sim` で3秒未満、GUI ウィンドウは一切開かない。
